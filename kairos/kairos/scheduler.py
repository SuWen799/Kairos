"""主动式调度：后台线程定时巡检盯页源——Muse 式"主动干活"的底座。

- 默认每 6 小时巡检一轮（可配置 watch.interval_hours）；
- 有变化的源：调 LLM 生成增量简报（带 ground truth）→ 写入工作区 + 提议通知（HITL）；
- 手动触发：POST /watch/run。
"""
import json
import threading
import time
import traceback
from datetime import datetime

from . import db, llm, tools, watcher
from .runtime import tasks as runtime_tasks

_thread_started = False
_INTERVAL_CFG = 6 * 3600


def _digest_report(changes: list) -> str:
    """对有变化的源生成一份增量简报（LLM + ground truth）。"""
    ground = "\n\n".join(
        f"[{c['name']}] {c['url']}\n{c['page_text'][:2500]}" for c in changes)
    try:
        return llm.chat_text(
            "你是秋招情报助理。以下是一次巡检中检测到内容变化的页面原文（ground truth）。"
            "为每个页面写 3-5 条增量要点（这是相对上次巡检的变化），只依据原文，"
            "不要编造。最后输出一行'建议动作'（如：尽快投递/更新台账）。",
            ground)
    except Exception as e:
        return f"[简报生成失败] {e}"


def _advance_goals():
    """Muse 式目标制：每个 active 目标由一个独立子代理推进一小步并记录进展。"""
    rows = db.query("SELECT * FROM goals WHERE status='active' ORDER BY id")
    results = []
    for g in rows:
        from . import agent as _agent
        sys_p = (
            "你是 EvoAgent 的目标执行代理，负责后台推进用户的长期目标。"
            f"目标：{g['title']}\n计划：{g['plan'] or '（暂无，先自行拆解）'}\n"
            f"已有进展记录：\n{g['progress'] or '（无）'}\n"
            "本轮只推进一小步：调用必要工具实际执行（抓网页/查台账/写工作区等），"
            "然后用 goal_progress 记录本轮进展（一行，含结论）；若目标已彻底完成则改用 goal_done。"
            "不要重复已完成的事，优先做计划里的下一步。最多 8 步。")
        try:
            out = _agent.run_turn(f"推进目标：{g['title']}", max_steps=8, system_override=sys_p)
            results.append({"goal": g["title"], "answer": out["answer"][:200]})
        except Exception as e:
            results.append({"goal": g["title"], "error": str(e)})
    return results


def run_once(scheduled: bool = False) -> dict:
    """手动/定时触发的单轮巡检。M-S2 observe 档：变化一律先落「发现」，
    用户在动态页点「转工单」才真干活（模型不能自我扩权，开单必须人批）。"""
    changes = watcher.check_all()
    if not changes:
        report = "全部盯页源无更新"
        goals_out = _advance_goals() if scheduled else []
        return {"checked": True, "changed": 0, "report": report,
                "goals_advanced": len(goals_out), "goals": goals_out}
    from . import evals as _evals
    observed = []
    for c in changes:
        r = _evals.ingest(
            source_kind="web_watch", source_ref=str(c.get("url", "")),
            title=f"页面更新：{c.get('name', '')}",
            payload=f"{c.get('url', '')}\n\n{c.get('page_text', '')[:3500]}",
            dedup_extra=c.get("old", ""))
        observed.append({
            "name": c.get("name"), "url": c.get("url"),
            "trig": r["trigger"].id if r["trigger"] else None,
            "dedup": r["dedup"], "gate": r["gate"], "assessment": r["assessment"],
        })
    goals_out = _advance_goals() if scheduled else []
    return {"checked": True, "changed": len(changes), "observed": observed,
            "report": f"检测到 {len(changes)} 个页面更新，已进入「发现」待你处理",
            "goals_advanced": len(goals_out), "goals": goals_out}


def _automation_tick():
    """检查到期的 automations → 建任务（执行权在 runtime worker，租约闭环）。"""
    from datetime import datetime
    from sqlalchemy import select
    from sqlalchemy.orm import Session as SASession
    from kairos.storage.conn import get_engine
    from kairos.storage.models import Automation
    from .storage import repo
    from .runtime import cron as _cron
    now = datetime.now()
    with SASession(get_engine(), expire_on_commit=False) as s:
        rows = s.execute(select(Automation).where(Automation.enabled.is_(True))).scalars().all()
        for a in rows:
            due = a.next_fire and _parse_ts(a.next_fire) <= now
            if a.next_fire is None:
                # 初始化 next_fire
                nf = _cron.next_fire(a.cron)
                a.next_fire = nf.isoformat(timespec="seconds") if nf else None
                continue
            if due:
                repo.create_task(kind="automation", scheduled_for=a.id)
                nf = _cron.next_fire(a.cron, after=now)
                a.next_fire = nf.isoformat(timespec="seconds") if nf else None
                a.last_run = now.isoformat(timespec="seconds")
        s.commit()


def _parse_ts(v: str) -> datetime:
    from datetime import datetime
    try:
        return datetime.fromisoformat(v)
    except Exception:
        return datetime.min


def _unified_loop():
    """统一轮（设计 3.2）：watch 间隔大，automation/task 巡检 60s 一次。"""
    n = 0
    while True:
        try:
            if not runtime_tasks.is_stopped():
                _automation_tick()
                runtime_tasks.reap_expired()
                runtime_tasks.reap_expired_tickets()  # M-S3：工单租约过期回队（崩溃恢复）
                if n % (_INTERVAL_CFG // 60) == 0:  # 到 watch 间隔点
                    if db.query("SELECT COUNT(*) AS n FROM sources WHERE status='active'")[0]["n"]:
                        run_once(scheduled=True)
                    else:
                        _advance_goals()
            n += 1
        except Exception:
            traceback.print_exc()
        time.sleep(60)


def _loop():
    """旧巡检循环（保留给单独 watch 模式），统一轮启动后不再使用。"""
    while True:
        try:
            if db.query("SELECT COUNT(*) AS n FROM sources WHERE status='active'")[0]["n"]:
                run_once(scheduled=True)
            else:
                _advance_goals()
        except Exception:
            traceback.print_exc()
        time.sleep(_INTERVAL_CFG)


def start_scheduler(interval_hours: float | None = None):
    """进程内启动：统一调度轮 + 任务工作线程（FastAPI 启动时调用一次）。"""
    global _thread_started, _INTERVAL_CFG
    from . import runtime
    if interval_hours:
        _INTERVAL_CFG = int(interval_hours * 3600)
    if _thread_started:
        return
    _thread_started = True
    runtime.tasks.start_worker()
    threading.Thread(target=_unified_loop, daemon=True).start()
    print(f"[scheduler] 统一调度轮已启动（watch 间隔 {_INTERVAL_CFG // 3600}h，automation/task 巡检 60s）")
