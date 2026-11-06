# -*- coding: utf-8 -*-
"""仓储层（需求 12 / 设计 2）：查询封装，业务代码禁裸 SQL 散落。

约定：
- 全部方法收 SQLAlchemy Session 或自建短会话（默认自建，跨线程安全）。
- 写入自动补 id（ULID 前缀）/user_id/时间戳；调用方不手填。
- run_events 是 append-only（DB 触发器强制），repo 只提供 append/list。
"""
from __future__ import annotations

import threading
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session as SASession

from kairos.storage.conn import get_engine
from kairos.storage.models import (
    Mandate, Message, Run, RunEvent, Session, Task, Ticket, Trigger,
)
from kairos.storage.ulid import LOCAL_USER_ID, new_id


def _session() -> SASession:
    """短会话：commit 后对象不过期（ DetachedInstance 规避）。"""
    return SASession(get_engine(), expire_on_commit=False)

_seq_lock = threading.Lock()  # 同 run 的 seq 分配锁（单机单进程足够）


def _now() -> str:
    from kairos.storage.models import utcnow
    return utcnow()


# ── Sessions ───────────────────────────────────────────
def create_session(title: str | None = None, *, project: str = "default",
                   user_id: str = LOCAL_USER_ID, model: str | None = None) -> Session:
    with _session() as s:
        row = Session(id=new_id("session"), user_id=user_id, project=project,
                      title=title, model=model,
                      created_at=_now(), updated_at=_now())
        s.add(row)
        s.commit()
        return row


def list_sessions(user_id: str = LOCAL_USER_ID, *, project: str | None = None,
                  include_archived: bool = False, limit: int = 200) -> list[Session]:
    with _session() as s:
        q = select(Session).where(Session.user_id == user_id)
        if project:
            q = q.where(Session.project == project)
        if not include_archived:
            q = q.where(Session.archived.is_(False))
        q = q.order_by(Session.updated_at.desc()).limit(limit)
        return list(s.scalars(q))


def get_session(session_id: str) -> Session | None:
    with _session() as s:
        return s.get(Session, session_id)


def touch_session(session_id: str) -> None:
    with _session() as s:
        row = s.get(Session, session_id)
        if row:
            row.updated_at = _now()
            s.commit()


def rename_session(session_id: str, title: str, *, auto: bool = False) -> None:
    with _session() as s:
        row = s.get(Session, session_id)
        if row:
            row.title, row.title_auto = title, auto
            row.updated_at = _now()
            s.commit()


def archive_session(session_id: str, archived: bool = True) -> None:
    with _session() as s:
        row = s.get(Session, session_id)
        if row:
            row.archived = archived
            row.updated_at = _now()
            s.commit()


def update_session(session_id: str, **fields) -> Session | None:
    """通用字段更新（v2.0）：model / mode / autonomy / plan_md / goal_ref / title。"""
    allowed = {"model", "mode", "autonomy", "plan_md", "goal_ref", "title", "project"}
    with _session() as s:
        row = s.get(Session, session_id)
        if row is None:
            return None
        for k, v in fields.items():
            if k in allowed:
                setattr(row, k, v)
        row.updated_at = _now()
        s.commit()
        return row


# ── Messages ───────────────────────────────────────────
def append_message(session_id: str, role: str, content: str, *,
                   user_id: str = LOCAL_USER_ID, tokens: int = 0,
                   tool_calls: list | None = None) -> Message:
    import json
    with _seq_lock:  # 会话内 seq 连续分配（消息游标与回放依赖它）
        with _session() as s:
            max_seq = s.execute(
                select(func.coalesce(func.max(Message.seq), 0))
                .where(Message.session_id == session_id)
            ).scalar_one()
            row = Message(id=new_id("message"), session_id=session_id, user_id=user_id,
                          seq=max_seq + 1, role=role, content=content, tokens=tokens,
                          tool_calls_json=(json.dumps(tool_calls, ensure_ascii=False)
                                           if tool_calls else None),
                          created_at=_now())
            s.add(row)
            sess = s.get(Session, session_id)
            if sess:
                sess.updated_at = _now()
            s.commit()
            return row


def list_messages(session_id: str, *, limit: int = 500,
                  after: str | None = None) -> list[Message]:
    with _session() as s:
        q = select(Message).where(Message.session_id == session_id)
        if after:  # 按 ULID 时间有序特性做游标分页
            q = q.where(Message.id > after)
        q = q.order_by(Message.id).limit(limit)
        return list(s.scalars(q))


# ── Runs ───────────────────────────────────────────────
def create_run(session_id: str, *, trigger: str = "chat",
               user_id: str = LOCAL_USER_ID, model: str | None = None) -> Run:
    with _session() as s:
        row = Run(id=new_id("run"), session_id=session_id, user_id=user_id,
                  status="running", trigger=trigger, model=model,
                  started_at=_now(), steps=0, tokens_in=0, tokens_out=0, cost_est=0.0,
                  created_at=_now(), updated_at=_now())
        s.add(row)
        s.commit()
        return row


def finish_run(run_id: str, *, status: str, steps: int = 0,
               tokens_in: int = 0, tokens_out: int = 0,
               cost_est: float = 0.0, error: str | None = None) -> None:
    with _session() as s:
        row = s.get(Run, run_id)
        if row:
            row.status = status
            row.steps = steps
            row.tokens_in = tokens_in
            row.tokens_out = tokens_out
            row.cost_est = cost_est
            row.error = error
            row.ended_at = _now()
            row.updated_at = _now()
            s.commit()


def get_run(run_id: str) -> Run | None:
    with _session() as s:
        return s.get(Run, run_id)


# ── RunEvents（append-only）────────────────────────────
def append_event(run_id: str, event_type: str, payload: dict, *,
                 ctx_digest: str | None = None) -> RunEvent:
    import json
    with _seq_lock:
        with _session() as s:
            max_seq = s.execute(
                select(func.coalesce(func.max(RunEvent.seq), 0))
                .where(RunEvent.run_id == run_id)
            ).scalar_one()
            row = RunEvent(id=new_id("event"), run_id=run_id, seq=max_seq + 1,
                           event_type=event_type,
                           payload_json=json.dumps(payload, ensure_ascii=False),
                           ctx_digest=ctx_digest, created_at=_now())
            s.add(row)
            s.commit()
            return row


def list_events(run_id: str, *, after_seq: int = 0,
                limit: int = 1000) -> list[RunEvent]:
    with _session() as s:
        q = (select(RunEvent).where(RunEvent.run_id == run_id,
                                     RunEvent.seq > after_seq)
             .order_by(RunEvent.seq).limit(limit))
        return list(s.scalars(q))


# ── Tasks ──────────────────────────────────────────────
def create_task(run_id: str | None = None, *, kind: str = "background",
                user_id: str = LOCAL_USER_ID, budget_limit: int | None = None,
                scheduled_for: str | None = None) -> Task:
    with _session() as s:
        row = Task(id=new_id("task"), run_id=run_id, user_id=user_id, kind=kind,
                   status="queued", budget_limit=budget_limit,
                   scheduled_for=scheduled_for,
                   created_at=_now(), updated_at=_now())
        s.add(row)
        s.commit()
        return row


def claim_next(worker_id: str, *, lease_seconds: float = 120.0) -> Task | None:
    """租约认领：queued 或租约过期的 interrupted/running 任务（需求 11.3）。"""
    import time
    now = time.time()
    with _session() as s:
        row = s.execute(
            select(Task).where(
                Task.status.in_(["queued", "interrupted"]),
                (Task.lease_until.is_(None)) | (Task.lease_until < now),
            ).order_by(Task.created_at).limit(1).with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        if row is None:
            return None
        row.status = "running"
        row.lease_until = now + lease_seconds
        row.heartbeat_at = now
        row.updated_at = _now()
        s.commit()
        return row


def heartbeat(task_id: str, *, lease_seconds: float = 120.0) -> None:
    import time
    with _session() as s:
        row = s.get(Task, task_id)
        if row:
            now = time.time()
            row.heartbeat_at = now
            row.lease_until = now + lease_seconds
            s.commit()


def transition_task(task_id: str, status: str, *, error: str | None = None,
                    checkpoint_ref: str | None = None) -> None:
    with _session() as s:
        row = s.get(Task, task_id)
        if row:
            row.status = status
            row.error = error
            if checkpoint_ref:
                row.checkpoint_ref = checkpoint_ref
            row.updated_at = _now()
            s.commit()


def list_tasks(*, status: str | None = None,
               user_id: str = LOCAL_USER_ID, limit: int = 100) -> list[Task]:
    with _session() as s:
        q = select(Task).where(Task.user_id == user_id)
        if status:
            q = q.where(Task.status == status)
        q = q.order_by(Task.created_at.desc()).limit(limit)
        return list(s.scalars(q))


# ── Tickets / Mandates（v3.0 M-S1 工单域）──────────────

_ALLOWED_TICKET_STATUS = {
    "queued", "working", "waiting_human", "ready", "blocked", "failed", "done", "cancelled",
}


def create_mandate(session_id: str, *, basis: str = "plan_approval", scope: str = "",
                   strength: str = "explicit", plan_ref: str | None = None,
                   note: str | None = None, user_id: str = LOCAL_USER_ID) -> Mandate:
    """不可变授权：只由人的明确动作创建（计划批准 / 手动开单），模型无权写入。"""
    with _session() as s:
        row = Mandate(id=new_id("mandate"), session_id=session_id, basis=basis,
                      scope=scope, strength=strength, plan_ref=plan_ref, note=note,
                      user_id=user_id, created_at=_now())
        s.add(row)
        s.commit()
        return row


def get_mandate(mandate_id: str) -> Mandate | None:
    with _session() as s:
        return s.get(Mandate, mandate_id)


def create_ticket(session_id: str, mandate_id: str, title: str, *,
                  instruction: str | None = None,
                  auto_start: bool = False,
                  user_id: str = LOCAL_USER_ID) -> Ticket:
    with _session() as s:
        row = Ticket(id=new_id("ticket"), session_id=session_id, mandate_id=mandate_id,
                     title=title, status="queued", instruction=instruction,
                     auto_start=auto_start,
                     user_id=user_id, created_at=_now(), updated_at=_now())
        s.add(row)
        s.commit()
        return row


def get_ticket(ticket_id: str) -> Ticket | None:
    with _session() as s:
        return s.get(Ticket, ticket_id)


def list_tickets(*, session_id: str | None = None, status: str | None = None,
                 user_id: str = LOCAL_USER_ID, limit: int = 50) -> list[Ticket]:
    with _session() as s:
        q = select(Ticket).where(Ticket.user_id == user_id)
        if session_id:
            q = q.where(Ticket.session_id == session_id)
        if status:
            q = q.where(Ticket.status == status)
        q = q.order_by(Ticket.created_at.desc()).limit(limit)
        return list(s.scalars(q))


def transition_ticket(ticket_id: str, status: str, *, run_id: str | None = None,
                      error: str | None = None) -> Ticket | None:
    """工单状态流转。合法目标集校验；ready/failed/done/cancelled 落 finished_at。"""
    if status not in _ALLOWED_TICKET_STATUS:
        raise ValueError(f"非法工单状态: {status}")
    with _session() as s:
        row = s.get(Ticket, ticket_id)
        if row is None:
            return None
        row.status = status
        if run_id:
            row.latest_run_id = run_id
        row.error = error
        if status in ("ready", "failed", "done", "cancelled"):
            row.finished_at = _now()
        row.updated_at = _now()
        s.commit()
        return row


def open_ticket_for_session(session_id: str) -> Ticket | None:
    """该会话当前活跃工单（queued/working），供 run 绑定。"""
    with _session() as s:
        row = s.execute(
            select(Ticket).where(
                Ticket.session_id == session_id,
                Ticket.status.in_(["queued", "working", "waiting_human"]),
            ).order_by(Ticket.created_at.desc()).limit(1)
        ).scalar_one_or_none()
        return row


# ── M-S2：Trigger（事件源观察档）──────────────────────────

def create_trigger(*, source_kind: str, source_ref: str, title: str,
                   payload: str = "", dedup_key: str,
                   gate: str = "pass", assessment: str | None = None,
                   user_id: str = LOCAL_USER_ID) -> Trigger | None:
    """落一条触发事件。dedup_key 幂等：同一事件已存在返回 None（不重复打扰）。"""
    with _session() as s:
        dup = s.execute(select(Trigger).where(
            Trigger.user_id == user_id, Trigger.dedup_key == dedup_key)
        ).scalar_one_or_none()
        if dup is not None:
            return None
        row = Trigger(id=new_id("trigger"), user_id=user_id,
                      source_kind=source_kind, source_ref=source_ref,
                      title=title, payload=payload[:4000], dedup_key=dedup_key,
                      status="observed", gate=gate, assessment=assessment,
                      created_at=_now(), updated_at=_now())
        s.add(row)
        s.commit()
        return row


def get_trigger(trigger_id: str) -> Trigger | None:
    with _session() as s:
        return s.get(Trigger, trigger_id)


def list_triggers(*, status: str | None = None, user_id: str = LOCAL_USER_ID,
                  limit: int = 30) -> list[Trigger]:
    with _session() as s:
        q = select(Trigger).where(Trigger.user_id == user_id)
        if status:
            q = q.where(Trigger.status == status)
        q = q.order_by(Trigger.created_at.desc()).limit(limit)
        return list(s.scalars(q))


def set_trigger_status(trigger_id: str, status: str, *,
                       ticket_id: str | None = None) -> Trigger | None:
    with _session() as s:
        row = s.get(Trigger, trigger_id)
        if row is None:
            return None
        row.status = status
        if ticket_id:
            row.ticket_id = ticket_id
        row.updated_at = _now()
        s.commit()
        return row


# ── M-S3：Ticket 租约认领（auto_start 工单的后台自动执行）──────────

def claim_next_ticket(worker_id: str, *, lease_seconds: float = 180.0) -> Ticket | None:
    """CAS 认领下一条 auto_start 且 queued 的工单 → working + 租约。"""
    import time as _t
    now = _t.time()
    with _session() as s:
        row = s.execute(
            select(Ticket).where(
                Ticket.status == "queued",
                Ticket.auto_start.is_(True),
                (Ticket.lease_until.is_(None)) | (Ticket.lease_until < now),
            ).order_by(Ticket.created_at).limit(1)
        ).scalar_one_or_none()
        if row is None:
            return None
        row.status = "working"
        row.lease_until = now + lease_seconds
        row.claimed_by = worker_id
        row.updated_at = _now()
        s.commit()
        return row


def heartbeat_ticket(ticket_id: str, *, lease_seconds: float = 180.0) -> bool:
    """续租。失败（工单被取消等）返回 False，执行方应尽快中止。"""
    import time as _t
    with _session() as s:
        row = s.get(Ticket, ticket_id)
        if row is None or row.status != "working":
            return False
        row.lease_until = _t.time() + lease_seconds
        row.updated_at = _now()
        s.commit()
        return True


def reap_expired_tickets() -> int:
    """租约过期的 working 工单 → 回到 queued 等重新认领（崩溃恢复语义）。"""
    import time as _t
    now = _t.time()
    n = 0
    with _session() as s:
        rows = s.execute(select(Ticket).where(
            Ticket.status == "working", Ticket.auto_start.is_(True),
            Ticket.lease_until.is_not(None), Ticket.lease_until < now,
        )).scalars().all()
        for t in rows:
            t.status = "queued"
            t.claimed_by = None
            t.updated_at = _now()
            n += 1
        s.commit()
    return n
