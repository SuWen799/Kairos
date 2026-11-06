"""工具集：白名单式受控工具。fetch 只出不进；文件只限工作区内；notify 是 HITL 提议。"""
import hashlib
import json
import os
import secrets
import threading

import requests

from . import db, memory, skills
from .config import DATA_DIR

FETCH_TIMEOUT = 20
MAX_HTML = 12000
WORKSPACE = os.path.join(DATA_DIR, "workspace")
os.makedirs(WORKSPACE, exist_ok=True)

# ── 运行上下文（v2.0 R3/R5/R7）：run_turn 派发工具前注入 ──
# thread-local 承载当前轮的 session_id/模式/权限档/事件回调，工具函数按需读取。
_ctx = threading.local()

# 规划模式下禁用的写类工具（服务端硬禁，双保险：spec 已过滤 + 执行时再拦）
WRITE_TOOLS = {"ws_write", "run_python", "remember", "save_skill", "agent_run",
               "goal_add", "goal_progress", "goal_done", "app_add", "app_update",
               "watch_add", "propose_notification"}

# 全自动权限档也不可豁免的高危动作关键词（v2.0 R5 硬边界）
HIGH_RISK_KEYWORDS = ("发送", "发送给", "删除", "对外", "邮件", "publish",
                      "send", "delete", "drop ", "format ")


def set_run_ctx(*, session_id=None, mode="chat", autonomy="standard", emit=None):
    """run_turn 进入工具派发段前调用；轮结束 clear_run_ctx。
    v3.2(#2)：先保存父级上下文（线程内栈深 1），clear 时恢复而非清默认——
    修复单角色 agent_run 子代理跑完把父轮 session/autonomy/emit 清空的 bug。"""
    _ctx.prev = (getattr(_ctx, "session_id", None), getattr(_ctx, "mode", "chat"),
                 getattr(_ctx, "autonomy", "standard"), getattr(_ctx, "emit", None))
    _ctx.session_id = session_id
    _ctx.mode = mode
    _ctx.autonomy = autonomy
    _ctx.emit = emit


def clear_run_ctx():
    prev = getattr(_ctx, "prev", None)
    if prev is not None:
        (_ctx.session_id, _ctx.mode, _ctx.autonomy, _ctx.emit) = prev
        _ctx.prev = None
    else:
        _ctx.session_id = None
        _ctx.mode = "chat"
        _ctx.autonomy = "standard"
        _ctx.emit = None


def get_run_ctx() -> dict:
    return {"session_id": getattr(_ctx, "session_id", None),
            "mode": getattr(_ctx, "mode", "chat"),
            "autonomy": getattr(_ctx, "autonomy", "standard"),
            "emit": getattr(_ctx, "emit", None)}


def _html_to_text(html: str) -> str:
    import re
    html = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", html, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _egress_allow_private() -> bool:
    """egress.allow_private 开关（config.local.json）：抓内网地址需显式打开（默认拒）。"""
    try:
        from . import config as _cfg
        return bool(_cfg.CFG.get("egress", {}).get("allow_private"))
    except Exception:
        return False


def validate_public_url(url: str) -> str | None:
    """SSRF 出口校验（Muse 简化版，OpenMuse validatePublicUrl 抄改）：
    scheme 白名单 + DNS 解析后拒绝环回/私网/链路本地/保留段。
    返回 None=放行；字符串=拒绝原因。诚实边界：内核级污点追踪没有，这是应用层出口闸。"""
    try:
        from urllib.parse import urlparse
        import ipaddress
        import socket
        u = urlparse(url)
        if u.scheme not in ("http", "https"):
            return f"scheme 不允许: {u.scheme or '(空)'}"
        host = u.hostname
        if not host:
            return "缺主机名"
        # 字面 IP 直接判；域名先解析再判（防 DNS rebinding 的应用层尽力而为）
        try:
            ipaddress.ip_address(host)
            addrs = [host]
        except ValueError:
            addrs = [ai[4][0] for ai in socket.getaddrinfo(host, None, socket.AF_INET)]
        for a in addrs:
            ip = ipaddress.ip_address(a)
            if not _egress_allow_private() and (
                    ip.is_loopback or ip.is_private or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
                return f"解析到受限地址 {a}（私网/环回/保留段），出口拒绝"
        return None
    except Exception as e:
        return f"出口校验异常: {e}"


def fetch_url(url: str) -> str:
    """抓取网页转纯文本。requests 失败或内容过短（JS 渲染 SPA）时用 Playwright
    本地 Edge 无头渲染兜底——和秋招通爬虫同款方案。只出不进，超时受控。
    安全：出口过 validate_public_url 白名单；重定向手动逐跳复验（防 302 逃逸进内网）。"""
    blocked = validate_public_url(url)
    if blocked:
        return f"[fetch拒绝] {blocked}"
    text = ""
    try:
        # 手动跟重定向：每跳都过一遍私网校验（requests 自动跟跳会绕过逐跳检查）
        cur = url
        for _hop in range(5):
            resp = requests.get(cur, timeout=FETCH_TIMEOUT, allow_redirects=False,
                                headers={"User-Agent": "Mozilla/5.0 (EvoAgent)"})
            if resp.is_redirect or 300 < resp.status_code < 400:
                loc = resp.headers.get("location", "")
                if not loc:
                    break
                from urllib.parse import urljoin
                cur = urljoin(cur, loc)
                blocked = validate_public_url(cur)
                if blocked:
                    return f"[fetch拒绝] 重定向目标受限：{blocked}"
                continue
            resp.raise_for_status()
            text = _html_to_text(resp.text)
            break
    except Exception:
        text = ""
    if len(text) >= 300:
        return text[:MAX_HTML]
    # JS 渲染兜底（入口已校验；浏览器内部重定向无法逐跳控制，诚实边界）
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(user_agent="Mozilla/5.0 (EvoAgent)")
            page.goto(url, timeout=25000, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            text = _html_to_text(page.content())
            browser.close()
    except Exception as e:
        if not text:
            return f"[fetch失败] {e}"
    return text[:MAX_HTML]


# ---- 工具实现（供 agent.py 的 tool calling 调用） ----

def tool_fetch_url(url: str) -> str:
    try:
        return fetch_url(url)[:6000]
    except Exception as e:
        return f"[fetch失败] {e}"


def tool_remember(content: str, kind: str = "fact") -> str:
    db.execute("INSERT INTO memories(content, kind, updated_at) VALUES(?,?,?)",
               (content, kind, db.now()))
    return f"已记忆：{content}"


def tool_recall(query: str) -> str:
    rows = memory.recall(query)
    return json.dumps(rows, ensure_ascii=False) if rows else "（无相关记忆）"


def tool_save_skill(name: str, content: str) -> str:
    skills.save_skill(name, content)
    return f"技能已固化：{name}"


def tool_find_skills(query: str) -> str:
    rows = skills.search_skills(query)
    return json.dumps(rows, ensure_ascii=False) if rows else "（无相关技能）"


def tool_create_task(title: str) -> str:
    db.execute("INSERT INTO tasks(title, created_at) VALUES(?,?)", (title, db.now()))
    return f"任务已创建：{title}"


def tool_list_tasks() -> str:
    rows = db.query("SELECT id,title,status FROM tasks ORDER BY id DESC LIMIT 20")
    return json.dumps([dict(r) for r in rows], ensure_ascii=False) if rows else "（无任务）"


def tool_complete_task(title: str) -> str:
    """完成任务（deepagents write_todos 的闭环：计划→执行→勾掉）。"""
    cur = db.get_conn().execute("UPDATE tasks SET status='done' WHERE title=? AND status!='done'", (title,))
    db.get_conn().commit()
    return f"已完成：{title}" if cur.rowcount else f"未找到进行中的任务：{title}"


# ---- 多 Agent（deepagents 的 task/子代理模式抄改：子代理只回结论，不污染主对话）----

SUBAGENT_ROLES = {
    "researcher": {
        "label": "调研员",
        "desc": "抓网页、查信息、交叉核对，输出结构化调研结论（要点+来源 URL）",
        "prompt": ("你是 EvoAgent 的调研子代理。你的任务：{task}\n"
                   "只做调研：抓网页、查信息、交叉核对。输出=结构化调研结论（要点+来源 URL），"
                   "不要寒暄、不要反问、不要提后续计划。最多 8 步。"),
    },
    "writer": {
        "label": "写手",
        "desc": "直接产出成稿（邮件/文案/复盘/总结），结构清晰可直接使用",
        "prompt": ("你是 EvoAgent 的写作子代理。你的任务：{task}\n"
                   "基于任务描述直接产出成稿（邮件/文案/复盘/总结），结构清晰可直接使用，"
                   "不确定的事实标注[待核实]。只输出成稿，不要解释写作思路。最多 6 步。"),
    },
    "reviewer": {
        "label": "审校",
        "desc": "对材料挑毛病：事实错误、逻辑漏洞、遗漏项，输出问题清单+修改建议",
        "prompt": ("你是 Kairos 的审校子代理。你的任务：{task}\n"
                   "对给定材料挑毛病：事实错误、逻辑漏洞、遗漏项、格式问题。"
                   "输出=问题清单（按严重度排序）+修改建议，没有问题就明确说通过。最多 5 步。"),
    },
    "coder": {
        "label": "码手",
        "desc": "写代码、跑 run_python 验证、修数据处理脚本，输出可用代码+运行结果",
        "prompt": ("你是 Kairos 的写码子代理。你的任务：{task}\n"
                   "优先用 run_python 实际运行验证再给结论。输出=可用代码+关键运行结果，"
                   "不要解释大段背景。最多 8 步。"),
    },
}


def tool_agent_run(role: str = "", task: str = "", batch: list | None = None) -> str:
    """派发子代理执行任务，只返回结论（deepagents：子代理只回结论不回过程）。
    v2.0 R7：支持 batch=[{role,task},...] 并行派发（线程池，上限 settings.max_subagents），
    子代理过程以 subagent.step 事件外推主 SSE，不再是黑盒。"""
    from .config import SETTINGS
    ctx = get_run_ctx()
    emit = ctx.get("emit")

    def _one(t: dict) -> dict:
        r = t.get("role", "")
        if r not in SUBAGENT_ROLES:
            return {"role": r, "ok": False,
                    "answer": f"[未知角色 {r}]，可选：{', '.join(SUBAGENT_ROLES)}"}
        from . import agent as _agent  # 延迟导入防循环

        def _sub_emit(ev):
            if emit:
                try:
                    emit({"type": "subagent.step", "role": r,
                          "label": SUBAGENT_ROLES[r]["label"], **ev})
                except Exception:
                    pass
        out = _agent.run_turn(t.get("task", ""), max_steps=8, on_event=_sub_emit,
                              system_override=SUBAGENT_ROLES[r]["prompt"].format(task=t.get("task", "")))
        return {"role": r, "label": SUBAGENT_ROLES[r]["label"],
                "ok": True, "steps": len(out.get("trace", [])),
                "answer": out["answer"]}

    if batch:
        from .runtime.subagents import run_batch
        results = run_batch(batch, _one, emit=emit)
        lines = []
        for res in results:
            head = f"[{res.get('label') or res.get('role')}·{res.get('steps', 0)}步]"
            lines.append(f"{head}\n{res['answer']}")
        return "\n\n──\n\n".join(lines)

    if role not in SUBAGENT_ROLES:
        return f"[未知角色 {role}]，可选：{', '.join(SUBAGENT_ROLES)}"
    res = _one({"role": role, "task": task})
    return f"[{SUBAGENT_ROLES[role]['label']}·{res.get('steps', 0)}步] {res['answer']}"


def tool_run_python(code: str) -> str:
    """受控代码执行（OpenHands/E2B 思路的本地简化版）：工作区沙盒目录 + 超时。
    v2.0 R6 分级：basic=子进程+10s 超时+沙盒 cwd；strict=额外套 Windows Job Object
    （内存 512MB 上限 + 进程树随 job 回收）。诚实边界：网络隔离 strict 档暂无
    （需防火墙规则/管理员），多用户生产场景不可依赖本沙箱。"""
    from .config import SETTINGS
    import subprocess
    import os as _os
    sandbox = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "data", "sandbox")
    _os.makedirs(sandbox, exist_ok=True)
    strict = SETTINGS.get("sandbox") == "strict"
    popen_kwargs = dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                        cwd=sandbox, encoding="utf-8", errors="replace",
                        env={**_os.environ, "PYTHONUTF8": "1"})
    try:
        if strict:
            from .sandbox import run_in_job
            r, note = run_in_job([_os.sys.executable, "-c", code], popen_kwargs, timeout=10)
        else:
            r = subprocess.run([_os.sys.executable, "-c", code], timeout=10, **popen_kwargs)
            note = ""
        out = (r.stdout or "") + (f"[stderr] {r.stderr}" if r.stderr else "")
        if note:
            out += f"\n[sandbox] {note}"
        return (out or "(无输出)")[:1500] + (f"\n[exit {r.returncode}]" if r.returncode else "")
    except subprocess.TimeoutExpired:
        return "[超时] 执行超过 10 秒被终止"
    except Exception as e:
        # strict 降级：Job Object 不可用时回落基础档并注明
        try:
            r = subprocess.run([_os.sys.executable, "-c", code],
                               capture_output=True, text=True, timeout=10, cwd=sandbox,
                               encoding="utf-8", errors="replace",
                               env={**_os.environ, "PYTHONUTF8": "1"})
            return f"[strict 沙箱降级 basic：{e}]\n" + ((r.stdout or "") + (f"\n[stderr] {r.stderr}" if r.stderr else "") or "(无输出)")[:1400]
        except subprocess.TimeoutExpired:
            return "[超时] 执行超过 10 秒被终止"
        except Exception as e2:
            return f"[执行异常] {e2}"


def tool_kb_search(query: str) -> str:
    """知识库检索（Dify RAG 思路的关键词版）：命中 chunk 按相关度返回 top5。"""
    kws = [w for w in query.replace("，", " ").replace("？", " ").split() if len(w) >= 2]
    if not kws:
        kws = [query]
    hits = {}
    for w in kws:
        rows = db.query(
            "SELECT doc_name, chunk FROM kb WHERE chunk LIKE ? LIMIT 10", (f"%{w}%",))
        for r in rows:
            key = (r["doc_name"], r["chunk"])
            hits[key] = hits.get(key, 0) + 1
    if not hits:
        return "（知识库无命中；可让用户在知识库界面上传文档）"
    top = sorted(hits.items(), key=lambda x: -x[1])[:5]
    return json.dumps([
        {"doc": k[0], "excerpt": k[1][:300]} for k, _ in top], ensure_ascii=False)


# ---- 长期目标（Muse goals 模式：登记后调度器每轮自动推进一小步）----

def tool_automation_add(name: str, cron: str, prompt: str) -> str:
    """OpenDot 式"Dot 自己创建定时任务"：agent 听到例行需求时自助建 automation，
    到期由 scheduler → 租约 worker 执行，结果写回绑定会话。"""
    from .runtime import cron as _cron
    try:
        nf = _cron.next_fire(cron)
    except Exception as e:
        return f"cron 表达式无效（{e}）。用五段式：分 时 日 月 周，如 '0 8 * * *' 每天8点"
    if not nf:
        return "cron 表达式算不出下次执行时间，请检查（五段式：分 时 日 月 周）"
    from sqlalchemy import select
    from sqlalchemy.orm import Session as SASession
    from kairos.storage.conn import get_engine
    from kairos.storage.models import Automation
    from kairos.storage.ulid import new_id as _new_id
    with SASession(get_engine(), expire_on_commit=False) as s:
        dup = s.execute(select(Automation).where(
            Automation.name == name, Automation.enabled.is_(True))).scalar_one_or_none()
        if dup:
            return f"同名定时任务已在运行：{name}"
        t = _now_iso()
        row = Automation(id=_new_id("automation"), user_id="usr_local", name=name,
                         cron=cron, prompt=prompt, enabled=True,
                         next_fire=nf.isoformat(timespec="seconds"),
                         created_at=t, updated_at=t)
        s.add(row)
        s.commit()
    return f"定时任务已创建：{name}（cron: {cron}，下次执行 {nf.isoformat(timespec='seconds')}）任务：{prompt[:80]}"


def _now_iso() -> str:
    from kairos.storage.models import utcnow
    return utcnow()


def tool_goal_add(title: str, plan: str = "") -> str:
    if db.query("SELECT id FROM goals WHERE title=? AND status='active'", (title,)):
        return f"该目标已在进行中：{title}"
    t = db.now()
    db.execute("INSERT INTO goals(title,plan,created_at,updated_at) VALUES(?,?,?,?)",
               (title, plan, t, t))
    return f"长期目标已登记，后台会周期推进：{title}（计划：{plan or '待拆解'}）"


def tool_goal_list() -> str:
    rows = db.query("SELECT id,title,status,progress FROM goals ORDER BY id DESC LIMIT 20")
    return json.dumps([dict(r) for r in rows], ensure_ascii=False) if rows else "（无长期目标）"


def tool_goal_progress(title: str, note: str) -> str:
    import time as _t
    row = db.query("SELECT id,progress FROM goals WHERE title=? AND status='active'", (title,))
    if not row:
        return f"未找到进行中的目标：{title}"
    merged = (row[0]["progress"] + f"\n[{_t.strftime('%m-%d %H:%M')}] {note}").strip()
    db.execute("UPDATE goals SET progress=?, updated_at=? WHERE id=?",
               (merged[-2000:], _t.time(), row[0]["id"]))
    return f"进展已记录：{title}"


def tool_goal_done(title: str) -> str:
    cur = db.get_conn().execute(
        "UPDATE goals SET status='done', updated_at=? WHERE title=? AND status='active'",
        (db.now(), title))
    db.get_conn().commit()
    return f"目标完成 🎉：{title}" if cur.rowcount else f"未找到进行中的目标：{title}"


def payload_hash(payload: str) -> str:
    """提案-哈希 HITL（v3.2）：提案时对 payload 拍快照，批准时校验"所见=所执行"防 TOCTOU。"""
    return hashlib.sha256((payload or "").encode("utf-8")).hexdigest()


def is_high_risk(action: str, payload: str) -> bool:
    """高危动作判定（v3.2(#1) 提为公共函数：decide 端点记账时也要拦高危）。"""
    return any(k in action for k in HIGH_RISK_KEYWORDS) or \
        any(k in (payload or "")[:80] for k in ("删除", "清空", "格式化"))


def mark_session_approved(session_id, action: str) -> None:
    """v3.2(#1)：semi 档记账只在用户真实批准后调用（decide 端点），
    修复"提议入队即记账 → 第二次同类动作未经批准就自动放行"的 bug。"""
    _semi_approved.add((str(session_id or ""), action))


def tool_propose_notification(action: str, payload: str) -> str:
    """HITL：只提议不执行，进确认队列。v2.0 R5 权限分档：
    - semi（半自动）：本会话内同类动作已批准过 → 自动放行（内存记账，v3.2(#1) 起记账时机=真实批准）。
    - auto（全自动）：非高危动作自动放行；高危（HIGH_RISK_KEYWORDS）任何档都强制人工。
    永久授权过的同类动作自动放行（Muse Sentinel 粒度，原有逻辑保留）。"""
    from .audit import audit
    ctx = get_run_ctx()
    autonomy = ctx.get("autonomy") or "standard"
    is_high = is_high_risk(action, payload)
    sid = str(ctx.get("session_id") or "")

    hit = db.query(
        "SELECT id FROM allowlist WHERE action=? AND ? LIKE sig||'%' LIMIT 1",
        (action, payload))
    if hit:
        cid = db.execute(
            "INSERT INTO confirmations(action, payload, status, scope, content_hash, session_id, created_at) VALUES(?,?,?,?,?,?,?)",
            (action, payload, "approved", "自动放行（永久授权）", payload_hash(payload), sid, db.now()))
        return (f"该类动作已获用户永久授权，自动放行并记录执行（id={cid}），无需再次确认。")

    # 半自动：本会话同类动作已批准过 → 放行（进程内记账，重启即失效，诚实）
    if autonomy == "semi" and not is_high:
        if (sid, action) in _semi_approved:
            cid = db.execute(
                "INSERT INTO confirmations(action, payload, status, scope, content_hash, session_id, created_at) VALUES(?,?,?,?,?,?,?)",
                (action, payload, "approved", "半自动档（本会话同类已批准）", payload_hash(payload), sid, db.now()))
            audit("loop", "approval.auto", policy_decision="auto-approved",
                  tool_name=action, note=f"semi 档自动放行 id={cid}")
            return f"本会话已批准过同类动作（{action}），自动放行并记录执行（id={cid}）。"

    # 全自动：非高危 → 放行；高危 → 照常进人工队列
    if autonomy == "auto" and not is_high:
        cid = db.execute(
            "INSERT INTO confirmations(action, payload, status, scope, content_hash, session_id, created_at) VALUES(?,?,?,?,?,?,?)",
            (action, payload, "approved", "全自动档（非高危）", payload_hash(payload), sid, db.now()))
        audit("loop", "approval.auto", risk_level="medium", policy_decision="auto-approved",
              tool_name=action, note=f"全自动档自动放行 id={cid}")
        return f"当前会话为全自动权限档，该动作（{action}）已自动放行并记录执行（id={cid}）。"
    if autonomy == "auto" and is_high:
        audit("loop", "approval.blocked_auto", risk_level="high", policy_decision="manual-required",
              tool_name=action, note="全自动档不可豁免高危动作")

    cid = db.execute(
        "INSERT INTO confirmations(action, payload, content_hash, session_id, created_at) VALUES(?,?,?,?,?)",
        (action, payload, payload_hash(payload), sid, db.now()))
    # v3.2(#1)：此处不再记账！semi 档的"本会话已批准"只在 decide 端点真实批准后记录
    # M4.1：新审批推 HITL 交互卡到已绑定的 IM 通道（非阻塞，失败静默——审批仍在队列里）
    import threading as _th
    _th.Thread(
        target=lambda: _push_card_safely(
            {"id": str(cid), "action": action, "payload": payload, "created_at": db.now()}),
        daemon=True).start()
    return (f"已进入人工确认队列（id={cid}）。这是高风险动作，需用户批准后才会执行，"
            f"你不能自行执行。用户可选择单次批准或永久授权。")


_semi_approved: set = set()  # {(session_id, action)} 半自动档本会话已批准记账


def _push_card_safely(cfm: dict) -> None:
    try:
        from .channels import manager as _chm
        _chm.push_approval_card(cfm)
    except Exception:
        pass


# ---- Agent 中调中枢（v2.6：派发外部 Agent CLI）----

def tool_hub_run(name: str, prompt: str, timeout: int = 600) -> str:
    """把任务派发给外部 Agent（claude/codex/pi…），返回其输出。"""
    from . import hub
    r = hub.run_agent(name, prompt, timeout)
    head = f"[{name}·{r.get('note', '')}]"
    return f"{head} {r['output']}"


# ---- 盯校招（增量 diff） ----

def tool_watch_add(name: str, url: str) -> str:
    from . import watcher
    return watcher.add_source(name, url)


def tool_watch_list() -> str:
    from . import watcher
    rows = watcher.list_sources()
    return json.dumps(rows, ensure_ascii=False) if rows else "（暂无盯页源）"


def tool_watch_check(url: str) -> str:
    """手动触发单源检查；有更新返回页面新内容供总结。"""
    from . import watcher
    res = watcher.check_source(url)
    if res.get("changed"):
        return (f"[{res['name']}] 页面有更新！请基于以下最新页面内容生成增量摘要"
                f"（只讲新增/变化的部分，标注来源 {res['url']}）：\n{res['page_text'][:3500]}")
    return res.get("summary", "无更新")


# ---- 投递台账 ----

def tool_app_add(company: str, position: str, status: str = "投递", notes: str = "") -> str:
    db.execute("INSERT INTO applications(company, position, status, notes, updated_at) VALUES(?,?,?,?,?)",
               (company, position, status, notes, db.now()))
    return f"台账已记录：{company} · {position} · {status}"


def tool_app_update(company: str, status: str, notes: str = "") -> str:
    rows = db.query("SELECT id FROM applications WHERE company LIKE ? ORDER BY updated_at DESC LIMIT 1",
                    (f"%{company}%",))
    if not rows:
        return f"台账里没有 {company} 的记录，先用 app_add 登记"
    db.execute("UPDATE applications SET status=?, notes=CASE WHEN ?!='' THEN ? ELSE notes END, updated_at=? WHERE id=?",
               (status, notes, notes, db.now(), rows[0]["id"]))
    return f"已更新 {company} 状态为：{status}"


def tool_app_list() -> str:
    rows = db.query("SELECT company, position, status, notes FROM applications ORDER BY updated_at DESC LIMIT 30")
    return json.dumps([dict(r) for r in rows], ensure_ascii=False) if rows else "（台账为空）"


def tool_app_stats() -> str:
    rows = db.query("SELECT status, COUNT(*) AS n FROM applications GROUP BY status")
    total = db.query("SELECT COUNT(*) AS n FROM applications")[0]["n"]
    return json.dumps({"total": total, "by_status": [dict(r) for r in rows]}, ensure_ascii=False)


# ---- 工作区（迷你"电脑"：文件读写限定在 data/workspace 内） ----

def _safe_path(rel: str) -> str:
    base = os.path.realpath(WORKSPACE)
    p = os.path.realpath(os.path.join(WORKSPACE, rel))
    if not p.startswith(base):
        raise ValueError("路径越界：只能访问工作区内文件")
    return p


def tool_ws_write(path: str, content: str) -> str:
    p = _safe_path(path)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)
    return f"已写入工作区：{path}（{len(content)} 字）"


def tool_ws_read(path: str) -> str:
    p = _safe_path(path)
    if not os.path.exists(p):
        return f"文件不存在：{path}"
    with open(p, "r", encoding="utf-8") as f:
        return f.read()[:6000]


# ---- 深度研究（方案 C：DeerFlow 本地引擎作为子代理，2026-11-02 接入） ----

DEERFLOW_BASE = "http://127.0.0.1:8001"  # DeerFlow Gateway（backend uvicorn :8001）


def tool_deep_research(topic: str, depth: str = "standard") -> str:
    """把深度调研路由给本地 DeerFlow 引擎：多源搜索+交叉核验+结构化报告，
    报告落工作区 reports/，返回路径+摘要。DeerFlow 未启动时优雅报错。"""
    try:
        r = requests.get(f"{DEERFLOW_BASE}/health", timeout=3)
        r.raise_for_status()
    except Exception:
        return ("[深度研究引擎未就绪] DeerFlow gateway(127.0.0.1:8001) 无响应。"
                "普通调研请改用 fetch_url/web_search 自查，或提示用户启动 DeerFlow。")
    prompt = (f"{topic}\n\n请对上述主题做深度研究：多源 web 搜索、交叉核验，"
              f"输出结构化 Markdown 报告（执行摘要 / 分主题详查 / 结论 / 参考链接列表）。"
              f"深度档位：{depth}。")
    # DeerFlow CSRF 为 double-submit 模式：cookie 与 header 同值即可（服务端不校验值来源）
    _csrf = secrets.token_urlsafe(64)
    try:
        resp = requests.post(
            f"{DEERFLOW_BASE}/api/runs/wait",
            json={"input": {"messages": [{"role": "user", "content": prompt}]}},
            headers={"X-CSRF-Token": _csrf, "Cookie": f"csrf_token={_csrf}"},
            timeout=900)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return f"[DeerFlow 调用失败] {e}"
    text = ""
    try:
        values = data.get("values") or data
        msgs = values.get("messages") or []
        for m in reversed(msgs):
            c = m.get("content") if isinstance(m, dict) else None
            if c:
                text = c if isinstance(c, str) else str(c)
                break
    except Exception:
        pass
    if not text:
        return f"[DeerFlow 无正文返回] 顶层字段：{list(data.keys())[:10]}"
    import time as _t
    path = f"reports/deep_research_{_t.strftime('%Y%m%d_%H%M%S')}.md"
    try:
        tool_ws_write(path, text)
        return (f"深度研究完成（DeerFlow），报告已落工作区：{path}（{len(text)} 字）。"
                f"摘要：{text[:200]}")
    except Exception as e:
        return f"研究完成但工作区写入失败：{e}。正文前 500 字：{text[:500]}"


def tool_ws_list(subdir: str = "") -> str:
    base = _safe_path(subdir)
    if not os.path.isdir(base):
        return f"目录不存在：{subdir}"
    out = []
    for root, _dirs, files in os.walk(base):
        for fn in files:
            rel = os.path.relpath(os.path.join(root, fn), WORKSPACE)
            out.append(rel)
    return json.dumps(sorted(out)[:50], ensure_ascii=False) if out else "（工作区为空）"


TOOLS_SPEC = [
    {"type": "function", "function": {"name": "agent_hub", "description": "Agent 中调中枢：把一个独立任务派发给外部 Agent CLI 执行（claude=Claude Code/codex=Codex CLI/pi=Pi Agent 等，以 /api/hub 注册表为准），拿回它的输出。适合让不同 Agent 各干擅长的活；GUI 类应用（豆包等）无 CLI 会返回提示",
        "parameters": {"type": "object", "properties": {"name": {"type": "string", "description": "外部 Agent 名（claude/codex/pi/harness…）"}, "prompt": {"type": "string", "description": "给它的完整任务描述"}, "timeout": {"type": "integer", "description": "超时秒数，默认 600"}}, "required": ["name", "prompt"]}}},
    {"type": "function", "function": {"name": "fetch_url", "description": "抓取网页并转为纯文本（截断到 6000 字）",
        "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "remember", "description": "把一条稳定事实/偏好写入长期记忆",
        "parameters": {"type": "object", "properties": {"content": {"type": "string"}, "kind": {"type": "string", "enum": ["preference", "fact", "plan"]}}, "required": ["content"]}}},
    {"type": "function", "function": {"name": "recall", "description": "检索长期记忆",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "save_skill", "description": "把做得好的经验固化为技能（名字要能代表一类任务）",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "content": {"type": "string"}}, "required": ["name", "content"]}}},
    {"type": "function", "function": {"name": "find_skills", "description": "检索是否有同类任务的既有技能",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "create_task", "description": "创建一条计划任务（多步任务先拆解列计划，做完一条就 complete_task 勾掉）",
        "parameters": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}}},
    {"type": "function", "function": {"name": "list_tasks", "description": "列出计划任务及状态",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "complete_task", "description": "把某条计划任务标记为已完成",
        "parameters": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}}},
    {"type": "function", "function": {"name": "agent_run", "description": "派发子代理执行独立任务（researcher=调研/writer=写作/reviewer=审校/coder=写码），只返回结论。单个任务传 role+task；多个独立子任务传 batch 数组并行执行（最多3个）。复杂任务拆给子代理，主对话保持干净",
        "parameters": {"type": "object", "properties": {
            "role": {"type": "string", "enum": ["researcher", "writer", "reviewer", "coder"]},
            "task": {"type": "string"},
            "batch": {"type": "array", "items": {"type": "object", "properties": {
                "role": {"type": "string", "enum": ["researcher", "writer", "reviewer", "coder"]},
                "task": {"type": "string"}}, "required": ["role", "task"]},
                "description": "多个子任务并行派发（与 role/task 二选一）"}},
            "required": []}}},
    {"type": "function", "function": {"name": "run_python", "description": "在沙盒目录执行 Python 代码（10 秒超时），适合算数/数据处理/格式转换等用户交代的小活",
        "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}}},
    {"type": "function", "function": {"name": "kb_search", "description": "检索用户知识库（上传的文档切片），回答里引用时注明出处文档名",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "goal_add", "description": "登记长期目标（Muse goals 模式）：如'每天盯腾讯校招直到出 2027 届公告'。后台调度器会周期性自动推进并记录进展",
        "parameters": {"type": "object", "properties": {"title": {"type": "string"}, "plan": {"type": "string"}}, "required": ["title"]}}},
    {"type": "function", "function": {"name": "automation_add", "description": "创建定时任务（OpenDot 式例行工作）：用户说'每天X点帮我做XX'时用。cron 为五段式（分 时 日 月 周），如每天8点半='30 8 * * *'、每周一9点='0 9 * * 1'。到期自动执行并把结果写进会话",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "cron": {"type": "string"}, "prompt": {"type": "string", "description": "到点要执行的任务指令（写给未来执行的那次 AI 看）"}}, "required": ["name", "cron", "prompt"]}}},
    {"type": "function", "function": {"name": "goal_list", "description": "列出长期目标及进展",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "goal_progress", "description": "给长期目标追加一条进展记录",
        "parameters": {"type": "object", "properties": {"title": {"type": "string"}, "note": {"type": "string"}}, "required": ["title", "note"]}}},
    {"type": "function", "function": {"name": "goal_done", "description": "把长期目标标记为已完成",
        "parameters": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}}},
    {"type": "function", "function": {"name": "propose_notification", "description": "提议一个对外动作（发通知/发消息），需人工确认",
        "parameters": {"type": "object", "properties": {"action": {"type": "string"}, "payload": {"type": "string"}}, "required": ["action", "payload"]}}},
    {"type": "function", "function": {"name": "watch_add", "description": "开始盯一个页面（如公司校招页），之后每次检查自动做增量 diff",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "url": {"type": "string"}}, "required": ["name", "url"]}}},
    {"type": "function", "function": {"name": "watch_list", "description": "列出正在盯的页面",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "watch_check", "description": "立即检查某个盯页源是否有更新（有更新返回新内容，应接着生成增量摘要）",
        "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "app_add", "description": "在投递台账登记一条投递记录",
        "parameters": {"type": "object", "properties": {"company": {"type": "string"}, "position": {"type": "string"}, "status": {"type": "string", "enum": ["投递", "笔试", "一面", "二面", "HR面", "Offer", "挂了", "放弃"]}, "notes": {"type": "string"}}, "required": ["company", "position"]}}},
    {"type": "function", "function": {"name": "app_update", "description": "更新某公司的投递状态",
        "parameters": {"type": "object", "properties": {"company": {"type": "string"}, "status": {"type": "string"}, "notes": {"type": "string"}}, "required": ["company", "status"]}}},
    {"type": "function", "function": {"name": "app_list", "description": "列出投递台账",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "app_stats", "description": "投递台账统计（总数/各状态数量）",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "deep_research", "description": "深度调研（路由到本地 DeerFlow 引擎）：需要多源 web 搜索、交叉核验、产出结构化研究报告的复杂主题用这个；耗时数分钟，返回报告文件路径+摘要",
        "parameters": {"type": "object", "properties": {"topic": {"type": "string", "description": "调研主题/问题"}, "depth": {"type": "string", "enum": ["quick", "standard"], "description": "档位：quick=快速扫描，standard=完整管线"}}, "required": ["topic"]}}},
    {"type": "function", "function": {"name": "ws_write", "description": "把内容写入个人工作区文件（如每日简报、台账导出）",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "ws_read", "description": "读取工作区文件",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "ws_list", "description": "列出工作区文件",
        "parameters": {"type": "object", "properties": {}}}},
]

TOOL_IMPL = {
    "agent_hub": lambda a: tool_hub_run(a["name"], a["prompt"], a.get("timeout", 600)),
    "fetch_url": lambda a: tool_fetch_url(a["url"]),
    "remember": lambda a: tool_remember(a["content"], a.get("kind", "fact")),
    "recall": lambda a: tool_recall(a["query"]),
    "save_skill": lambda a: tool_save_skill(a["name"], a["content"]),
    "find_skills": lambda a: tool_find_skills(a["query"]),
    "create_task": lambda a: tool_create_task(a["title"]),
    "list_tasks": lambda a: tool_list_tasks(),
    "complete_task": lambda a: tool_complete_task(a["title"]),
    "agent_run": lambda a: tool_agent_run(a.get("role", ""), a.get("task", ""), a.get("batch")),
    "run_python": lambda a: tool_run_python(a["code"]),
    "kb_search": lambda a: tool_kb_search(a["query"]),
    "goal_add": lambda a: tool_goal_add(a["title"], a.get("plan", "")),
    "goal_list": lambda a: tool_goal_list(),
    "goal_progress": lambda a: tool_goal_progress(a["title"], a["note"]),
    "goal_done": lambda a: tool_goal_done(a["title"]),
    "propose_notification": lambda a: tool_propose_notification(a["action"], a["payload"]),
    "watch_add": lambda a: tool_watch_add(a["name"], a["url"]),
    "watch_list": lambda a: tool_watch_list(),
    "watch_check": lambda a: tool_watch_check(a["url"]),
    "app_add": lambda a: tool_app_add(a["company"], a["position"], a.get("status", "投递"), a.get("notes", "")),
    "app_update": lambda a: tool_app_update(a["company"], a["status"], a.get("notes", "")),
    "app_list": lambda a: tool_app_list(),
    "app_stats": lambda a: tool_app_stats(),
    "ws_write": lambda a: tool_ws_write(a["path"], a["content"]),
    "deep_research": lambda a: tool_deep_research(a["topic"], a.get("depth", "standard")),
    "ws_read": lambda a: tool_ws_read(a["path"]),
    "ws_list": lambda a: tool_ws_list(a.get("subdir", "")),
}
