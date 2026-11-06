"""SQLite 连接与 PRAGMA 基线（需求 12.1）：WAL + foreign_keys + busy_timeout。"""
import os
import sqlite3

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

from kairos.config import DB_PATH


def _ensure_dir():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


def get_engine() -> Engine:
    """SQLAlchemy 引擎（模型层用）。check_same_thread=False + 线程池由池管理。"""
    _ensure_dir()
    engine = create_engine(
        f"sqlite:///{DB_PATH}",
        connect_args={"check_same_thread": False, "timeout": 30},
        pool_pre_ping=True,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

    return engine


def get_raw_conn() -> sqlite3.Connection:
    """裸 sqlite3 连接（回填脚本/维护工具用）。"""
    _ensure_dir()
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
