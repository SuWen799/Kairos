"""Token 预算与熔断（需求 8.4 / 9.6）。
四维聚合（日/会话/任务/通道）+ 超限挂起 + 同类工具连续失败冷却。"""
import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from kairos.audit import audit
from kairos.storage.conn import get_engine
from kairos.storage.models import Budget
from kairos.storage.ulid import LOCAL_USER_ID, new_id

# 工具熔断器：tool_name -> {"fails": n, "open_until": ts}
_breakers: dict[str, dict] = {}
FAIL_THRESHOLD = 5
COOLDOWN_SECONDS = 1800


def _period(day: bool = True) -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%d") if day else now.strftime("%Y-%m")


def _get_or_create(session, scope: str, period: str, tokens_limit: int = 200_000) -> Budget:
    row = session.execute(
        select(Budget).where(Budget.scope == scope, Budget.period == period)
    ).scalar_one_or_none()
    if row is None:
        row = Budget(id=new_id("budget"), user_id=LOCAL_USER_ID,
                     scope=scope, period=period, tokens_limit=tokens_limit)
        session.add(row)
        session.flush()
    return row


def consume(tokens_in: int, tokens_out: int, *, session_id: str | None = None,
            channel: str | None = None) -> None:
    """按 global/日 与可选的 session/通道 维度累计消耗。"""
    day = _period(day=True)
    scopes = ["global"] + ([f"session:{session_id}"] if session_id else []) \
        + ([f"channel:{channel}"] if channel else [])
    with Session(get_engine()) as session:
        for scope in scopes:
            row = _get_or_create(session, scope, day)
            row.tokens_used += tokens_in + tokens_out
            row.updated_at = _period(day=True)  # 触发 onupdate 无所谓，显式写
        session.commit()


def check(session_id: str | None = None, channel: str | None = None) -> dict:
    """熔断判定：任一 scope 超限 → blocked。verdict: {blocked, scope, used, limit}"""
    day = _period(day=True)
    scopes = ["global"] + ([f"session:{session_id}"] if session_id else []) \
        + ([f"channel:{channel}"] if channel else [])
    with Session(get_engine()) as session:
        for scope in scopes:
            row = session.execute(
                select(Budget).where(Budget.scope == scope, Budget.period == day)
            ).scalar_one_or_none()
            if row and row.tokens_used >= row.tokens_limit:
                return {"blocked": True, "scope": scope,
                        "used": row.tokens_used, "limit": row.tokens_limit}
    return {"blocked": False, "scope": None, "used": 0, "limit": 0}


def record_failure(tool_name: str) -> bool:
    """工具失败记账；连续 FAIL_THRESHOLD 次 → 打开熔断（冷却 COOLDOWN_SECONDS）。
    返回是否刚打开熔断。"""
    st = _breakers.setdefault(tool_name, {"fails": 0, "open_until": 0.0})
    st["fails"] += 1
    if st["fails"] >= FAIL_THRESHOLD and time.time() > st["open_until"]:
        st["open_until"] = time.time() + COOLDOWN_SECONDS
        audit("loop", "budget.circuit_open", tool_name=tool_name,
              note=f"连续失败 {st['fails']} 次，冷却 {COOLDOWN_SECONDS}s")
        return True
    return False


def record_success(tool_name: str) -> None:
    _breakers.pop(tool_name, None)


def is_open(tool_name: str) -> bool:
    st = _breakers.get(tool_name)
    return bool(st and time.time() < st["open_until"])
