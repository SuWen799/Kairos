"""长期记忆（Mem0 式）：写时冲突消解 + 跨会话召回。

四操作：ADD / UPDATE / DELETE / NOOP——写入前先与现有相似记忆比对，
矛盾在"写时"解决而不是留到检索时打架；无新增价值就不写。
"""
import json

from . import db, llm

_EXTRACT_SYS = (
    "你负责从对话中判断是否有值得长期记住的用户事实（偏好/计划/背景/结论）。\n"
    "按 DeerMem 式写入门控逐条判定，只收符合门槛的：\n"
    "- scope=personal：关于用户本人（偏好/背景/计划），不收助手自己的过程性动作；\n"
    "- durability=durable：跨会话仍有效，不收一次性任务细节；\n"
    "- authority=confirmed：用户明确陈述，不收推测。\n"
    "每条自动流程通过的事实不向用户确认，直接入库。\n"
    "输出 JSON 数组，每项 {\"content\": \"...\", \"kind\": \"preference|fact|plan\"}，没有则输出 []。只输出 JSON。"
)

_RESOLVE_SYS = (
    "你是记忆冲突消解器。下面给出一条新记忆和若干条已有的相似记忆（带 id）。\n"
    "判定应执行的操作，只输出 JSON：\n"
    ' {"op": "ADD"} 新信息与现有记忆不重复不矛盾；\n'
    ' {"op": "UPDATE", "id": <id>, "content": "<合并后的新表述>"} 新信息使某条记忆过时（更新它）；\n'
    ' {"op": "DELETE", "id": <id>} 新信息明确否定某条记忆；\n'
    ' {"op": "NOOP"} 无新增价值（完全重复）。只输出 JSON。'
)


def _keyword_hits(text: str, limit: int = 5) -> list:
    """朴素关键词检索：取新记忆里最长的几个词做 LIKE 命中。"""
    words = [w for w in json.dumps(text, ensure_ascii=False).split('"') if len(w) >= 2][:6]
    rows = []
    seen = set()
    for w in words:
        for r in db.query(
            "SELECT id, content, kind FROM memories WHERE content LIKE ? ORDER BY updated_at DESC LIMIT ?",
            (f"%{w}%", limit),
        ):
            if r["id"] not in seen:
                seen.add(r["id"])
                rows.append(dict(r))
    return rows[:limit]


def extract_and_store(user_text: str, assistant_text: str) -> list:
    """从一轮对话提取候选记忆，逐条做冲突消解后落库。返回执行的操作列表。"""
    try:
        raw = llm.chat_text(
            _EXTRACT_SYS, f"用户：{user_text}\n\n助手：{assistant_text[:1500]}")
        candidates = json.loads(raw[raw.index("["): raw.rindex("]") + 1])
    except Exception:
        return []
    ops = []
    for cand in candidates:
        if not cand.get("content"):
            continue
        existing = _keyword_hits(cand["content"])
        try:
            verdict = json.loads(llm.chat_text(
                _RESOLVE_SYS,
                "新记忆：" + cand["content"] + "\n\n已有记忆："
                + json.dumps(existing, ensure_ascii=False)))
        except Exception:
            continue
        op = verdict.get("op", "NOOP")
        if op == "ADD":
            db.execute(
                "INSERT INTO memories(content, kind, updated_at) VALUES(?,?,?)",
                (cand["content"], cand.get("kind", "fact"), db.now()))
            ops.append({"op": "ADD", "content": cand["content"]})
        elif op == "UPDATE" and verdict.get("id"):
            db.execute("UPDATE memories SET content=?, updated_at=? WHERE id=?",
                       (verdict["content"], db.now(), verdict["id"]))
            ops.append({"op": "UPDATE", "id": verdict["id"], "content": verdict["content"]})
        elif op == "DELETE" and verdict.get("id"):
            db.execute("DELETE FROM memories WHERE id=?", (verdict["id"],))
            ops.append({"op": "DELETE", "id": verdict["id"]})
        else:
            ops.append({"op": "NOOP", "content": cand["content"][:50]})
    return ops


def recall(query: str, limit: int = 6) -> list:
    """召回：关键词命中的记忆 + 全部偏好类记忆。"""
    rows = _keyword_hits(query, limit=limit)
    prefs = db.query("SELECT id, content, kind FROM memories WHERE kind='preference' ORDER BY updated_at DESC LIMIT 5")
    out, seen = [], set()
    for r in rows + [dict(p) for p in prefs]:
        if r["id"] not in seen:
            seen.add(r["id"])
            out.append(r)
    return out[:limit + 5]


def all_memories() -> list:
    return [dict(r) for r in db.query("SELECT * FROM memories ORDER BY updated_at DESC")]
