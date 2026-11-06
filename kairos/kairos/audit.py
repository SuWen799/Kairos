"""审计写入（需求 9.5）：append-only + 简化哈希链。
prev_hash = sha256(上一条 id + ts + event_type)。Agent 无任何工具能写本表（仅内核代码调用）。"""
import hashlib
import threading

from sqlalchemy import select
from sqlalchemy.orm import Session

from kairos.storage.conn import get_engine
from kairos.storage.models import AuditEvent
from kairos.storage.ulid import LOCAL_USER_ID, new_id

_lock = threading.Lock()


def _last_hash(session) -> str:
    last = session.execute(
        select(AuditEvent).order_by(AuditEvent.ts.desc(), AuditEvent.id.desc()).limit(1)
    ).scalar_one_or_none()
    if last is None:
        return "GENESIS"
    return hashlib.sha256(f"{last.id}{last.ts}{last.event_type}".encode()).hexdigest()


def audit(actor: str, event_type: str, *, user_id: str = LOCAL_USER_ID,
          risk_level: str | None = None, policy_decision: str | None = None,
          tool_name: str | None = None, params_hash: str | None = None,
          result_hash: str | None = None, tokens_in: int = 0, tokens_out: int = 0,
          approver: str | None = None, note: str | None = None) -> str:
    """写一条审计事件（同步、线程安全）。audit 表只增不改：应用层不提供 update/delete。"""
    with _lock, Session(get_engine()) as session:
        row = AuditEvent(
            id=new_id("audit"), user_id=user_id, actor=actor, event_type=event_type,
            risk_level=risk_level, policy_decision=policy_decision, tool_name=tool_name,
            params_hash=params_hash, result_hash=result_hash,
            tokens_in=tokens_in, tokens_out=tokens_out,
            approver=approver, note=note,
            prev_hash=_last_hash(session),
        )
        session.add(row)
        session.commit()
        return row.id


def verify_chain(limit: int = 1000) -> dict:
    """校验哈希链完整性（审计页工具）。返回 {checked, broken_at}。"""
    with Session(get_engine()) as session:
        rows = session.execute(
            select(AuditEvent).order_by(AuditEvent.ts, AuditEvent.id).limit(limit)
        ).scalars().all()
    prev = "GENESIS"
    for i, row in enumerate(rows):
        expect = hashlib.sha256(f"{row.id}{row.ts}{row.event_type}".encode()).hexdigest()
        if row.prev_hash != prev:
            return {"checked": i, "broken_at": row.id}
        prev = expect
    return {"checked": len(rows), "broken_at": None}
