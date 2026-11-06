"""v0.4 旧库（data/evogent.db）→ 新库（data/kairos.db）一次性回填。
用法：python scripts/backfill_v04.py [--dry-run]
映射规则见设计文档 2.3 对照表；goals/allowlist 不迁（goals 为实验数据，诚实声明）。
幂等：新库已有对应前缀数据时跳过。"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kairos.config import DATA_DIR, LEGACY_DB_PATH  # noqa: E402
from kairos.storage.conn import get_raw_conn  # noqa: E402
from kairos.storage.ulid import LOCAL_USER_ID, new_id  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

OLD = os.path.join(DATA_DIR, "evogent.db")


def ts(real: float | None) -> str | None:
    if real is None:
        return None
    return datetime.fromtimestamp(real, tz=timezone.utc).isoformat(timespec="seconds")


def main(dry_run: bool = False) -> None:
    if not os.path.exists(OLD):
        print(f"旧库不存在：{OLD}，无需回填")
        return
    old = sqlite3.connect(OLD)
    old.row_factory = sqlite3.Row
    new = get_raw_conn()
    counts: dict[str, int] = {}

    def already(pref: str) -> bool:
        return new.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
            (pref,)).fetchone()[0] and new.execute(
            f"SELECT COUNT(*) FROM {pref}").fetchone()[0] > 0

    # 1. sessions（旧 id → 新 ULID 映射）
    sid_map: dict[int, str] = {}
    for r in old.execute("SELECT * FROM sessions ORDER BY id"):
        sid = new_id("session")
        sid_map[r["id"]] = sid
        if not dry_run:
            new.execute(
                "INSERT INTO sessions(id,user_id,project,title,title_auto,archived,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (sid, LOCAL_USER_ID, "default", r["title"], 0, 0,
                 ts(r["created_at"]), ts(r["created_at"])))
        counts["sessions"] = counts.get("sessions", 0) + 1

    # 2. messages（session_id 重映射，seq 按旧 id 顺序）
    per_sess_seq: dict[str, int] = {}
    for r in old.execute("SELECT * FROM messages ORDER BY id"):
        sess = sid_map.get(r["session_id"]) if r["session_id"] else None
        if sess is None:
            continue  # 孤儿消息跳过
        per_sess_seq[sess] = per_sess_seq.get(sess, 0) + 1
        if not dry_run:
            new.execute(
                "INSERT INTO messages(id,user_id,session_id,seq,role,content,tokens,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (new_id("message"), LOCAL_USER_ID, sess, per_sess_seq[sess],
                 r["role"], r["content"], 0, ts(r["created_at"]), ts(r["created_at"])))
        counts["messages"] = counts.get("messages", 0) + 1

    # 3. memories / skills / failures / confirmations / sources(→watch_targets) / applications / kb
    for r in old.execute("SELECT * FROM memories"):
        if not dry_run:
            new.execute(
                "INSERT INTO memories(id,user_id,content,kind,confidence,forgettable,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (new_id("memory"), LOCAL_USER_ID, r["content"], r["kind"] or "fact",
                 1.0, 1, ts(r["updated_at"]), ts(r["updated_at"])))
        counts["memories"] = counts.get("memories", 0) + 1

    for r in old.execute("SELECT * FROM skills"):
        if not dry_run:
            new.execute(
                "INSERT INTO skills(id,user_id,slug,name,content,use_count,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (new_id("skill"), LOCAL_USER_ID, r["name"], r["name"], r["content"],
                 r["use_count"] or 0, ts(r["created_at"]), ts(r["created_at"])))
        counts["skills"] = counts.get("skills", 0) + 1

    for r in old.execute("SELECT * FROM failures"):
        if not dry_run:
            new.execute(
                "INSERT INTO failures(id,user_id,task_desc,reason,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?)",
                (new_id("failure"), LOCAL_USER_ID, r["task_desc"], r["reason"],
                 ts(r["created_at"]), ts(r["created_at"])))
        counts["failures"] = counts.get("failures", 0) + 1

    for r in old.execute("SELECT * FROM confirmations"):
        scope = {"单次": "once", "会话": "session", "永久": "permanent"}.get(r["scope"] or "", "once")
        decision = "pending" if (r["status"] or "pending") == "pending" else "approved"
        if not dry_run:
            new.execute(
                "INSERT INTO confirmations(id,user_id,action,content_json,scope,decision,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (new_id("confirmation"), LOCAL_USER_ID, r["action"], r["payload"] or "{}",
                 scope, decision, ts(r["created_at"]), ts(r["created_at"])))
        counts["confirmations"] = counts.get("confirmations", 0) + 1

    for r in old.execute("SELECT * FROM sources"):
        if not dry_run:
            new.execute(
                "INSERT INTO watch_targets(id,user_id,name,url,cadence_hours,last_digest,last_check_at,status,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (new_id("watch"), LOCAL_USER_ID, r["name"], r["url"], 6.0,
                 r["last_digest"] or "", ts(r["last_check_at"]), r["status"] or "active",
                 ts(r["last_check_at"]) or ts(0), ts(r["last_check_at"]) or ts(0)))
        counts["watch_targets"] = counts.get("watch_targets", 0) + 1

    for r in old.execute("SELECT * FROM applications"):
        if not dry_run:
            new.execute(
                "INSERT INTO applications(id,user_id,company,position,status,source_url,notes,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (new_id("application"), LOCAL_USER_ID, r["company"], r["position"],
                 r["status"] or "投递", r["source_url"] or "", r["notes"] or "",
                 ts(r["updated_at"]), ts(r["updated_at"])))
        counts["applications"] = counts.get("applications", 0) + 1

    doc_map: dict[str, str] = {}
    for r in old.execute("SELECT DISTINCT doc_name FROM kb"):
        did = new_id("kb_doc")
        doc_map[r["doc_name"]] = did
        if not dry_run:
            new.execute(
                "INSERT INTO kb_docs(id,user_id,doc_name,meta_json,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (did, LOCAL_USER_ID, r["doc_name"], "{}", ts(0), ts(0)))
    doc_seq: dict[str, int] = {}
    for r in old.execute("SELECT * FROM kb ORDER BY id"):
        did = doc_map.get(r["doc_name"])
        if did and not dry_run:
            doc_seq[did] = doc_seq.get(did, 0) + 1
            new.execute(
                "INSERT INTO kb_chunks(id,user_id,doc_id,seq,text,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (new_id("kb_chunk"), LOCAL_USER_ID, did, doc_seq[did],
                 r["chunk"], ts(r["created_at"]), ts(r["created_at"])))
        counts["kb_chunks"] = counts.get("kb_chunks", 0) + 1

    # 4. 本地用户行
    if not dry_run:
        new.execute(
            "INSERT OR IGNORE INTO users(id,display_name,settings_json,created_at,updated_at)"
            " VALUES(?,?,?,?,?)",
            (LOCAL_USER_ID, "local", "{}", ts(0), ts(0)))
        new.commit()

    print(("dry-run " if dry_run else "") + "回填统计：", counts)
    print("跳过：goals（实验数据）、allowlist（由 channel_bindings 取代）")


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
