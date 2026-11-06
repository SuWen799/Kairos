"""迷你 RSI 自进化循环：Curriculum → Actor → Verifier → 技能固化。

工程本质（诚实版）：
- Curriculum：看该任务族的历史失败案例 + 已有技能，生成一个"比上次更难一点"的任务实例；
- Actor：ReAct 主循环执行（可检索复用既有技能）；
- Verifier：两道校验——规则校验（引用的 URL 必须真的 fetch 过，防编造）+ LLM judge
  （是否满足任务要求）；不通过不放行，失败记入 failures 供下轮出题；
- 通过：LLM 从成功轨迹提炼可复用经验，固化为该任务族的技能——下次同类任务直接命中。
"""
import json
import re

from . import agent, db, llm, skills

_CURRICULUM_SYS = (
    "你是 Curriculum Agent：为一个日常任务族生成下一次练习任务。\n"
    "输入是任务族描述、历史失败案例、已有技能。规则：\n"
    "- 第一轮出基础版任务；\n"
    "- 若有失败案例，本轮任务要针对失败原因加难度（更多来源/更严格的结构要求）；\n"
    "- 任务必须可独立执行、有明确的完成标准。\n"
    '只输出 JSON：{"task": "任务描述（含具体 URL 或对象）", "harder_than_last": "为什么比上轮难"}'
)

_JUDGE_SYS = (
    "你是 Verifier（LLM judge）。对照任务要求检查 Actor 的回答：\n"
    "1. 是否完成了任务要求的全部内容；2. 结构是否符合要求；3. 有无敷衍/空话。\n"
    "重要：输入里附有 Actor 实际抓取到的页面内容（ground truth）——回答中的事实必须"
    "能在 ground truth 里找到出处，ground truth 里没有的'事实'按编造处理；"
    "ground truth 能支撑的内容不要凭直觉怀疑。严格打分，宁可错杀不可放过（fail-closed）。"
    '只输出 JSON：{"pass": true/false, "reason": "一句话理由"}'
)

_DISTILL_SYS = (
    "你从一次成功任务中提炼可复用经验。输入任务、回答、执行轨迹。\n"
    "输出一份'这类任务的标准做法'：步骤、必须包含的要素、容易翻车的点。"
    "不超过 300 字，不要空话，要可执行。直接输出正文，不要标题。"
)

_URL_RE = re.compile(r"https?://[^\s（）()\"'，。、`*\u4e00-\u9fff]+")


def _norm(u: str) -> str:
    return u.rstrip("/").rstrip("`*.,;:)）").strip()


def _verify_rules(task_desc: str, answer: str, trace: list) -> dict:
    """规则校验：引用的 URL 必须真的抓取过（防编造链接）；回答不能是空的。"""
    fetched = {_norm(t["args"].get("url", "")) for t in trace if t["tool"] == "fetch_url"}
    fetched.discard("")
    bad = [u for u in (_URL_RE.findall(answer)) if _norm(u) not in fetched]
    if not answer or len(answer) < 50:
        return {"pass": False, "reason": "回答过短或为空"}
    if bad:
        return {"pass": False, "reason": f"引用了未实际抓取的链接（编造嫌疑）：{bad[:2]}"}
    return {"pass": True, "reason": "规则校验通过"}


def run_rsi(task_family: str, rounds: int = 3) -> dict:
    rounds = max(1, min(int(rounds), 5))
    report = {"task_family": task_family, "rounds": []}
    for i in range(1, rounds + 1):
        failures = db.query(
            "SELECT task_desc, reason FROM failures ORDER BY id DESC LIMIT 5")
        fam_skills = skills.search_skills(task_family)
        # 1) Curriculum 出题
        try:
            cur = json.loads(llm.chat_text(
                _CURRICULUM_SYS,
                json.dumps({"任务族": task_family, "第几轮": i,
                            "失败案例": [dict(r) for r in failures],
                            "已有技能": [s["name"] for s in fam_skills]},
                           ensure_ascii=False)))
        except Exception as e:
            report["rounds"].append({"round": i, "error": f"Curriculum 解析失败: {e}"})
            continue
        task_desc = cur.get("task", task_family)
        # 2) Actor 执行
        act = agent.run_turn(task_desc)
        # 3) Verifier：规则 + LLM judge（judge 拿 ground truth 对照，不凭直觉判编造）
        rules = _verify_rules(task_desc, act["answer"], act["trace"])
        if rules["pass"]:
            ground_truth = "\n\n".join(
                f"[URL: {t['args'].get('url', '')}]\n{t['result']}"
                for t in act["trace"] if t["tool"] == "fetch_url")[:8000]
            try:
                judge = json.loads(llm.chat_text(
                    _JUDGE_SYS,
                    f"任务：{task_desc}\n\n回答：\n{act['answer'][:2500]}\n\n"
                    f"实际抓取到的页面内容（ground truth）：\n{ground_truth or '（未抓取任何页面）'}"))
            except Exception:
                judge = {"pass": False, "reason": "judge 解析失败"}
        else:
            judge = rules
        entry = {"round": i, "task": task_desc, "harder_than_last": cur.get("harder_than_last", ""),
                 "verifier": judge, "passed": bool(judge.get("pass"))}
        # 4) 固化 or 记失败
        if entry["passed"]:
            skill_content = llm.chat_text(
                _DISTILL_SYS,
                f"任务：{task_desc}\n\n回答：\n{act['answer'][:1500]}\n\n轨迹：\n"
                + json.dumps(act["trace"], ensure_ascii=False)[:1500])
            skills.save_skill(task_family, skill_content)
            entry["skill_saved"] = True
        else:
            db.execute("INSERT INTO failures(task_desc, reason, created_at) VALUES(?,?,?)",
                       (task_desc, judge.get("reason", "unknown"), db.now()))
        report["rounds"].append(entry)
        if entry["passed"]:
            break  # 通过即固化技能，停止本轮族练习
    report["converged"] = any(r.get("passed") for r in report["rounds"])
    return report
