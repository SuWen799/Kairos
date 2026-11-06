# -*- coding: utf-8 -*-
"""持久执行（设计 3.2）：任务工作循环 + 租约 + 优雅退出语义。

- worker 线程：claim_next 认领 → heartbeat 续租 → 执行 → transition
- reap：租约过期的 running 任务标记 interrupted（等待重新认领）
- RUNTIME.stop 置位后：worker 不再认领新任务（GraphDrained 语义：跑完手头的就停）
"""
from __future__ import annotations

import threading
import time
import traceback

from .. import agent
from ..audit import audit
from ..storage import repo

_heartbeat_stop = threading.Event()
_worker_started = False


def execute_task(task) -> None:
    """任务执行体。kind=automation：拿绑定的 session 跑一轮 prompt。"""
    from kairos.storage.models import Automation
    from sqlalchemy import select
    from sqlalchemy.orm import Session as SASession
    from kairos.storage.conn import get_engine
    from kairos.storage.ulid import new_id

    # 找到触发它的 automation（借 scheduled_for 存 automation id）
    auto = None
    with SASession(get_engine(), expire_on_commit=False) as s:
        if task.scheduled_for:
            auto = s.get(Automation, task.scheduled_for)

    sess_id = (auto.session_id if auto else None) or _latest_session_id() or _new_session()
    prompt = auto.prompt if auto else "执行后台任务"
    run = repo.create_run(sess_id, trigger="automation")
    repo.transition_task(task.id, "running")
    try:
        out = agent.run_turn(prompt, max_steps=10)
        repo.append_message(sess_id, "assistant",
                            f"[自动化 · {auto.name if auto else 'task'}] {out.get('answer', '')}")
        repo.finish_run(run.id, status="done", steps=len(out.get("trace", [])))
        repo.transition_task(task.id, "done")
        audit("scheduler", "task.done", note=f"task={task.id} run={run.id}")
    except Exception as e:  # noqa: BLE001
        repo.finish_run(run.id, status="failed", error=str(e)[:300])
        repo.transition_task(task.id, "failed", error=str(e)[:300])
        audit("scheduler", "task.failed", note=f"task={task.id} {str(e)[:200]}")


def _latest_session_id() -> str | None:
    rows = repo.list_sessions(limit=1)
    return rows[0].id if rows else None


def _new_session() -> str:
    s = repo.create_session("后台任务")
    return s.id


def execute_ticket(ticket) -> None:
    """auto_start 工单执行体（M-S3）：独立租约保护，instruction 驱动，结果写回绑定会话。"""
    run = repo.create_run(ticket.session_id, trigger="ticket")
    try:
        out = agent.run_turn(ticket.instruction or ticket.title,
                             max_steps=12, session_id=ticket.session_id)
        repo.append_message(ticket.session_id, "assistant",
                            f"[工单 {ticket.title}] {out.get('answer', '')}")
        repo.finish_run(run.id, status="done", steps=len(out.get("trace", [])))
        repo.transition_ticket(ticket.id, "ready", run_id=run.id)
        audit("runtime", "ticket.ready", note=f"ticket={ticket.id} run={run.id}")
    except Exception as e:  # noqa: BLE001
        repo.finish_run(run.id, status="failed", error=str(e)[:300])
        repo.transition_ticket(ticket.id, "failed", run_id=run.id, error=str(e)[:300])
        audit("runtime", "ticket.failed", note=f"ticket={ticket.id} {str(e)[:200]}")


def worker_loop(poll_seconds: float = 3.0) -> None:
    """任务工作线程：先认领 auto_start 工单，再认领后台任务（执行内带心跳续租）。"""
    while not _heartbeat_stop.is_set():
        if is_stopped():
            time.sleep(poll_seconds)
            continue
        ticket = repo.claim_next_ticket("worker_main")
        if ticket is not None:
            hb_stop = threading.Event()
            hb = threading.Thread(target=_heartbeat_ticket, args=(ticket.id, 45.0, hb_stop), daemon=True)
            hb.start()
            try:
                execute_ticket(ticket)
            except Exception:
                traceback.print_exc()
                repo.transition_ticket(ticket.id, "failed", error=traceback.format_exc()[:300])
            finally:
                hb_stop.set()
            continue
        task = repo.claim_next("worker_main")
        if task is None:
            time.sleep(poll_seconds)
            continue
        hb_stop = threading.Event()
        hb = threading.Thread(target=_heartbeat, args=(task.id, 15.0, hb_stop), daemon=True)
        hb.start()
        try:
            execute_task(task)
        except Exception:
            traceback.print_exc()
            repo.transition_task(task.id, "failed", error=traceback.format_exc()[:300])
        finally:
            hb_stop.set()


def _heartbeat(task_id: str, interval: float, stop: threading.Event) -> None:
    while not stop.is_set() and not _heartbeat_stop.is_set():
        repo.heartbeat(task_id)
        stop.wait(interval)


def _heartbeat_ticket(ticket_id: str, interval: float, stop: threading.Event) -> None:
    """工单租约心跳：续租失败（被取消/接管）即中止后续续租，执行体下次落库会发现。"""
    while not stop.is_set() and not _heartbeat_stop.is_set():
        if not repo.heartbeat_ticket(ticket_id):
            break
        stop.wait(interval)


def reap_expired() -> int:
    """租约过期 → interrupted（等重新认领）。"""
    import time as _t
    from sqlalchemy import select
    from sqlalchemy.orm import Session as SASession
    from kairos.storage.conn import get_engine
    from kairos.storage.models import Task
    n = 0
    now = _t.time()
    with SASession(get_engine(), expire_on_commit=False) as s:
        rows = s.execute(select(Task).where(
            Task.status == "running", Task.lease_until.is_not(None),
            Task.lease_until < now)).scalars().all()
        for t in rows:
            t.status = "interrupted"
            n += 1
        s.commit()
    return n


def reap_expired_tickets() -> int:
    """工单租约过期回队（M-S3 崩溃恢复）。repo 实现的薄包装，统一调度轮调用。"""
    return repo.reap_expired_tickets()


# ── kill switch（需求 11.6 托盘 kill switch）─────────────
_runtime_stopped = False


def is_stopped() -> bool:
    return _runtime_stopped


def set_stopped(v: bool) -> bool:
    global _runtime_stopped
    _runtime_stopped = v
    audit("scheduler", "runtime.stop" if v else "runtime.resume")
    return _runtime_stopped


def start_worker() -> None:
    global _worker_started
    if _worker_started:
        return
    _worker_started = True
    threading.Thread(target=worker_loop, daemon=True).start()
