"""轻量列迁移（v2.0）：sessions 表补 mode/plan_md/autonomy/goal_ref 列。
SQLite ALTER ADD COLUMN 幂等靠 PRAGMA 检查。alembic 之外的过渡期兜底。"""
from sqlalchemy import text
from sqlalchemy.orm import Session as SASession

from kairos.storage.conn import get_engine

_EXTRA_COLUMNS = {
    "sessions": [
        ("mode", "TEXT DEFAULT 'chat'"),
        ("plan_md", "TEXT"),
        ("autonomy", "TEXT DEFAULT 'standard'"),
        ("goal_ref", "TEXT"),
    ],
    # M-S3 租约执行：老库 tickets 表补列（新库由 create_all 直接建）
    "tickets": [
        ("auto_start", "INTEGER DEFAULT 0"),
        ("lease_until", "REAL"),
        ("claimed_by", "TEXT"),
    ],
    # 消息渠道绑定：channel_bindings 补 session_id / enabled
    "channel_bindings": [
        ("session_id", "TEXT"),
        ("enabled", "INTEGER DEFAULT 1"),
    ],
}


def ensure_extra_columns() -> dict:
    applied = {}
    with SASession(get_engine()) as s:
        for table, cols in _EXTRA_COLUMNS.items():
            rows = s.execute(text(f"PRAGMA table_info({table})")).fetchall()
            existing = {r[1] for r in rows}
            for name, ddl in cols:
                if name not in existing:
                    s.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                    applied[f"{table}.{name}"] = True
        s.commit()
    return applied
