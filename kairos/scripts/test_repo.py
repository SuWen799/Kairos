# -*- coding: utf-8 -*-
"""repo 层单测：临时库跑 alembic → 验证 ID 格式、消息/事件 seq 并发分配、任务租约。

用法：venv python scripts/test_repo.py
"""
import os
import sys
import tempfile
import traceback
from concurrent.futures import ThreadPoolExecutor

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import kairos.config as _cfg
import kairos.storage.conn as conn_mod

_tmpdir = tempfile.mkdtemp(prefix="kairos_repo_test_")
_smoke_db = os.path.join(_tmpdir, "test.db")
conn_mod.DB_PATH = _smoke_db
_cfg.DB_PATH = _smoke_db

from alembic import command
from alembic.config import Config as AlembicConfig

_cfg_ = AlembicConfig(os.path.join(BASE, "alembic.ini"))
_cfg_.set_main_option("script_location", os.path.join(BASE, "alembic"))
command.upgrade(_cfg_, "head")

from kairos.storage import repo
from kairos.storage.ulid import PREFIXES, is_valid, new_id

RESULTS = []


def check(name, fn):
    try:
        fn()
        RESULTS.append((name, True, ""))
        print(f"[PASS] {name}")
    except Exception as e:  # noqa: BLE001
        RESULTS.append((name, False, str(e)))
        print(f"[FAIL] {name}: {e}")
        traceback.print_exc()


# 1. ULID 格式与唯一性（2000 并发生成）
def test_ulid_format_unique():
    ids = set()
    with ThreadPoolExecutor(max_workers=8) as ex:
        for i in ex.map(lambda _: new_id("message"), range(2000)):
            ids.add(i)
    assert len(ids) == 2000, f"ULID 并发生成出现重复: {2000 - len(ids)}"
    for kind, prefix in [("session", "sess"), ("run", "run"), ("event", "evt"),
                         ("message", "msg"), ("task", "task")]:
        sample = new_id(kind)
        assert sample.startswith(prefix + "_"), f"{kind} 前缀错误: {sample}"
        assert is_valid(sample, kind)
    sess_sample = new_id("session")
    assert not is_valid(sess_sample, "run"), "is_valid 交叉类型应返回 False"


# 2. 会话/消息/事件基础流 + seq 单调
def test_session_message_flow():
    sess = repo.create_session("测试会话", project="p1")
    assert is_valid(sess.id, "session")
    m1 = repo.append_message(sess.id, "user", "你好")
    m2 = repo.append_message(sess.id, "assistant", "你好，有什么可以帮你？")
    m3 = repo.append_message(sess.id, "user", "继续")
    assert (m1.seq, m2.seq, m3.seq) == (1, 2, 3), f"seq 应连续: {m1.seq},{m2.seq},{m3.seq}"
    msgs = repo.list_messages(sess.id)
    assert len(msgs) == 3 and msgs[0].id < msgs[2].id, "消息列表应按 ULID 有序"
    # 游标分页
    page2 = repo.list_messages(sess.id, after=msgs[0].id)
    assert len(page2) == 2, "after 游标分页错误"
    repo.rename_session(sess.id, "改名后")
    got = repo.get_session(sess.id)
    assert got.title == "改名后"
    return sess


# 3. run + append-only 事件流 + 并发 seq 分配
def test_run_events_concurrent():
    sess = repo.create_session()
    run = repo.create_run(sess.id, trigger="chat")
    assert is_valid(run.id, "run") and run.status == "running"

    with ThreadPoolExecutor(max_workers=8) as ex:
        evts = list(ex.map(
            lambda i: repo.append_event(run.id, "step", {"i": i}), range(64)))
    assert len(evts) == 64
    seqs = sorted(e.seq for e in evts)
    assert seqs == list(range(1, 65)), f"并发 seq 出现重复/断档: {seqs[:10]}..."

    back = repo.list_events(run.id)
    assert [e.seq for e in back] == list(range(1, 65)), "事件回放顺序错误"

    repo.finish_run(run.id, status="done", steps=5, tokens_in=100, tokens_out=200)
    got = repo.get_run(run.id)
    assert got.status == "done" and got.steps == 5 and got.ended_at
    return run


# 4. 任务租约认领与状态机
def test_task_lease():
    t1 = repo.create_task(kind="background")
    t2 = repo.create_task(kind="scheduled", scheduled_for="2026-11-02T09:00")
    assert is_valid(t1.id, "task")

    claimed = repo.claim_next("worker_A")
    assert claimed and claimed.id == t1.id and claimed.status == "running"
    assert claimed.lease_until is not None, "认领应带租约"

    repo.heartbeat(t1.id)  # 续租不报错即可
    repo.transition_task(t1.id, "done")
    tasks = repo.list_tasks(status="done")
    assert any(t.id == t1.id for t in tasks)
    # t2 仍是 queued，可被下一次认领
    again = repo.claim_next("worker_B")
    assert again and again.id == t2.id


sess_out, run_out = None, None
check("ULID 格式与 2000 并发唯一性", test_ulid_format_unique)


def _t2():
    global sess_out
    sess_out = test_session_message_flow()
check("会话/消息 CRUD + seq 连续 + 游标分页", _t2)


def _t3():
    global run_out
    run_out = test_run_events_concurrent()
check("run 事件并发 seq 分配 + append-only 回放", _t3)

check("任务租约认领/心跳/状态迁移", test_task_lease)

failed = [r for r in RESULTS if not r[1]]
print(f"\n===== repo 单测: {len(RESULTS) - len(failed)}/{len(RESULTS)} 通过 =====")
sys.exit(1 if failed else 0)
