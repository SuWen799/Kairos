"""SQLite 落库：messages / memories / skills / tasks / failures / confirmations。
数据持久化落库是底线，不搞内存假状态。"""
import os
import sqlite3
import time

from .config import LEGACY_DB_PATH as DB_PATH  # 过渡期：旧 API 用旧库；新 storage/ 用新库

_conn: sqlite3.Connection | None = None


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS messages(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL, content TEXT NOT NULL,
                created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS memories(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL, kind TEXT DEFAULT 'fact',
                updated_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS skills(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL, content TEXT NOT NULL,
                use_count INTEGER DEFAULT 0,
                created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS tasks(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL, status TEXT DEFAULT 'open',
                created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS failures(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_desc TEXT NOT NULL, reason TEXT NOT NULL,
                created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS confirmations(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL, payload TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS sources(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL, url TEXT UNIQUE NOT NULL,
                last_digest TEXT DEFAULT '',
                last_check_at REAL DEFAULT 0,
                status TEXT DEFAULT 'active');
            CREATE TABLE IF NOT EXISTS applications(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company TEXT NOT NULL, position TEXT NOT NULL,
                status TEXT DEFAULT '投递',
                source_url TEXT DEFAULT '', notes TEXT DEFAULT '',
                updated_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS kb(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_name TEXT NOT NULL, chunk TEXT NOT NULL,
                created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS goals(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL, plan TEXT DEFAULT '',
                progress TEXT DEFAULT '', status TEXT DEFAULT 'active',
                created_at REAL NOT NULL, updated_at REAL NOT NULL);
            """
        )
        # 轻量迁移：messages 加 session_id 列（老库平滑升级）
        cols = [r["name"] for r in _conn.execute("PRAGMA table_info(messages)").fetchall()]
        if "session_id" not in cols:
            _conn.execute("ALTER TABLE messages ADD COLUMN session_id INTEGER DEFAULT 1")
        ccols = [r["name"] for r in _conn.execute("PRAGMA table_info(confirmations)").fetchall()]
        if "scope" not in ccols:
            _conn.execute("ALTER TABLE confirmations ADD COLUMN scope TEXT DEFAULT '单次'")
        if "content_hash" not in ccols:
            # v3.2 提案-哈希 HITL：批准时校验"所见=所执行"，防 TOCTOU
            _conn.execute("ALTER TABLE confirmations ADD COLUMN content_hash TEXT DEFAULT ''")
        if "session_id" not in ccols:
            # v3.2(#1)：semi 档记账需要在批准时知道提议所属会话
            _conn.execute("ALTER TABLE confirmations ADD COLUMN session_id TEXT DEFAULT ''")
        _conn.execute("""CREATE TABLE IF NOT EXISTS allowlist(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL, sig TEXT NOT NULL,
                created_at REAL NOT NULL)""")
        _conn.commit()
    return _conn


def now() -> float:
    return time.time()


def execute(sql: str, params: tuple = ()) -> int:
    cur = get_conn().execute(sql, params)
    get_conn().commit()
    return cur.lastrowid


def query(sql: str, params: tuple = ()) -> list:
    return get_conn().execute(sql, params).fetchall()
