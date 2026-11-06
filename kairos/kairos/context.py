# -*- coding: utf-8 -*-
"""上下文组装（设计 3.1 context.assemble）：小而稳的 system + 记忆摘要 + 近期消息。

M1 起对话带历史（v0.4 只有单轮）；token 估算用 CJK 保守口径 len//2。
"""
from __future__ import annotations

import hashlib
import json

from . import agent as _agent
from .storage import repo


def estimate_tokens(text: str) -> int:
    """CJK 保守估算：按字符数一半计（数字/英文更省，偏大不影响安全）。"""
    return max(1, len(text or "") // 2)


def assemble(session_id: str, *, keep_last: int = 20) -> dict:
    """组装本轮上下文：system(模板+记忆) + 历史窗口（最新 keep_last 条）。

    返回 {system_messages, history, digest, est_tokens}。
    digest = 注入内容的指纹（写入 run_events，UI 可见"本轮注入了什么"）。
    """
    sess = repo.get_session(session_id)
    system = _agent.SYSTEM_TMPL.format(memory="（运行时召回）", skills="（运行时召回）")
    msgs = repo.list_messages(session_id, limit=1000)
    window = msgs[-keep_last:]
    history = [{"role": m.role, "content": m.content or ""} for m in window
               if m.role in ("user", "assistant") and m.content]

    digest_src = json.dumps({"system_len": len(system), "hist": len(history)},
                            ensure_ascii=False)
    digest = hashlib.sha256(digest_src.encode()).hexdigest()[:16]
    est = estimate_tokens(system) + sum(estimate_tokens(h["content"]) for h in history)
    return {"system_messages": [{"role": "system", "content": system}],
            "history": history, "digest": digest, "est_tokens": est}


def mask_tools(tools_spec: list, phase: str = "chat") -> list:
    """工具遮蔽（设计 3.1）：全量 schema 常驻，按阶段标 unavailable（保 KV-cache）。

    M2 phase 只有 chat（不遮蔽）；后续 plan/act 分相时启用。
    """
    return tools_spec
