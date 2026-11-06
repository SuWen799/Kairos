# -*- coding: utf-8 -*-
"""Eval 两层评估（M-S2，抄 dsh-superagent 领域模型）：

- gate：确定性规则引擎（纯代码，零 token）——去重/频率/长度/黑名单；
- assessment：LLM 研判"这个活值不值得接"（Flash 档省钱）；
- 主动接纳三档（off/observe/auto）里的 observe 档默认只记录不干活，
  用户在「发现」列表点一下才转工单——模型不能自我扩权，开单必须人批。

入口 ingest()：Source 事件 → gate → dedup → assessment → observed 落库。
"""
from __future__ import annotations

import hashlib
import json

from . import db, llm
from .audit import audit
from .storage import repo

# gate 的确定性拦截词（触发即 block，不进观察列表；命中说明是噪声或危险内容）
_GATE_BLOCK_WORDS = ("验证码", "password", "otp:")

_ASSESS_SYS = (
    "你是个人智能体的任务评估器。用户是 2026/2027 届求职硕士生，主战场是秋招。"
    "下面是一条事件源触发的'发现'（网页变化/定时巡检产出）。判断它值不值得立项去干：\n"
    "- 与求职/用户明确登记的关注源相关，且确有可干的实质内容 → accept；\n"
    "- 噪声（广告/改版样式变化/相对时间戳抖动/无实质信息）→ ignore。\n"
    "只输出 JSON：{\"verdict\": \"accept|ignore\", \"reason\": \"一句话\"}"
)


def gate_check(title: str, payload: str) -> str:
    """确定性 gate。返回 'pass' 或 'block:原因'。"""
    if not title.strip():
        return "block:空标题"
    if len(payload.strip()) < 30:
        return "block:内容过短（<30 字，多半是抓取失败或空壳页）"
    low = (title + payload[:500]).lower()
    for w in _GATE_BLOCK_WORDS:
        if w in low:
            return f"block:命中敏感词 {w}"
    return "pass"


def assess(title: str, payload: str) -> dict:
    """LLM 研判。失败时保守放行进观察列表（observe 档本来就不自动执行，代价低）。"""
    try:
        raw = llm.chat_text(_ASSESS_SYS, f"标题：{title}\n\n内容摘要：\n{payload[:2000]}")
        data = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
        return {"verdict": data.get("verdict", "accept"), "reason": str(data.get("reason", ""))[:120]}
    except Exception as e:
        return {"verdict": "accept", "reason": f"评估失败保守放行: {e}"}


def ingest(*, source_kind: str, source_ref: str, title: str, payload: str,
           dedup_extra: str = "") -> dict:
    """Source 事件统一入口：gate → 幂等落库 → assessment。

    dedup_key = sha256(source_ref + 内容摘要 + dedup_extra)，同内容重复巡检不再打扰。
    返回 {trigger, gate, assessment, dedup}。
    """
    g = gate_check(title, payload)
    digest = hashlib.sha256((payload[:600] + dedup_extra).encode("utf-8")).hexdigest()[:16]
    dedup_key = f"{source_kind}:{source_ref}:{digest}"
    assessment = None
    if g == "pass":
        a = assess(title, payload)
        assessment = f"[{a['verdict']}] {a['reason']}"
    row = repo.create_trigger(
        source_kind=source_kind, source_ref=source_ref, title=title,
        payload=payload, dedup_key=dedup_key, gate=g, assessment=assessment)
    audit("evals", "trigger.ingest",
          note=f"kind={source_kind} dedup={dedup_key[:48]} gate={g} trig={row.id if row else 'dup'}")
    return {"trigger": row, "gate": g, "assessment": assessment,
            "dedup": row is None}


def promote(trigger_id: str, *, instruction: str | None = None) -> dict:
    """观察 → 开单（人点一下 = explicit 授权）。建 Mandate + auto_start 工单，
    租约 worker 自动执行；执行结果以消息形式写回工单绑定会话。"""
    t = repo.get_trigger(trigger_id)
    if t is None:
        return {"ok": False, "msg": "触发器不存在"}
    if t.status != "observed":
        return {"ok": False, "msg": f"该触发器已处理（{t.status}）"}
    # 工单绑一个会话：优先源登记时的上下文，否则开「事件工单」会话
    sess = repo.list_sessions(limit=1)
    session_id = sess[0].id if sess else repo.create_session("事件工单").id
    mandate = repo.create_mandate(session_id, basis="explicit",
                                  scope=f"事件转工单：{t.title[:80]}",
                                  strength="explicit")
    instr = instruction or (
        f"处理以下事件发现并交付结果（产出写工作区）：\n\n标题：{t.title}\n"
        f"来源：{t.source_kind} {t.source_ref}\n内容：\n{t.payload[:2500]}")
    ticket = repo.create_ticket(session_id, mandate.id, t.title[:60],
                                instruction=instr, auto_start=True)
    repo.set_trigger_status(trigger_id, "accepted", ticket_id=ticket.id)
    audit("evals", "trigger.promote", note=f"trig={trigger_id} ticket={ticket.id}")
    return {"ok": True, "ticket_id": ticket.id, "session_id": session_id}


def dismiss(trigger_id: str) -> dict:
    t = repo.set_trigger_status(trigger_id, "dismissed")
    if t is None:
        return {"ok": False, "msg": "触发器不存在"}
    audit("evals", "trigger.dismiss", note=f"trig={trigger_id}")
    return {"ok": True}
