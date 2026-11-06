# -*- coding: utf-8 -*-
"""M1 冒烟测试：在临时测试库上验证 storage 层核心机制，不碰真实 kairos.db。

覆盖 4 项：
  1. append-only 触发器（run_events 禁 UPDATE/DELETE）
  2. audit 哈希链可验证
  3. budget 限额 + 熔断器
  4. 幂等键唯一约束

用法：venv python scripts/smoke_m1.py
"""
import os
import sys
import tempfile
import traceback

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import kairos.config as _cfg
import kairos.storage.conn as conn_mod
from kairos.storage import ulid

# ── 指向临时库（必须在 import 其他模块前 patch）──
_tmpdir = tempfile.mkdtemp(prefix="kairos_smoke_")
_smoke_db = os.path.join(_tmpdir, "smoke.db")
conn_mod.DB_PATH = _smoke_db
_cfg.DB_PATH = _smoke_db  # alembic env.py 读这里

# ── 用 alembic 迁移链建库（端到端验证 0001→0004）──
from alembic import command
from alembic.config import Config as AlembicConfig

_alembic_cfg = AlembicConfig(os.path.join(BASE, "alembic.ini"))
_alembic_cfg.set_main_option("script_location", os.path.join(BASE, "alembic"))
command.upgrade(_alembic_cfg, "head")
print("[setup] alembic upgrade head 完成（临时库）")

from sqlalchemy import select, text
from sqlalchemy.orm import Session as SASession

from kairos.storage.conn import get_engine
from kairos.storage.models import (
    IdempotencyKey, Run, RunEvent, Session, User,
)
from kairos.storage.ulid import LOCAL_USER_ID, new_id
from kairos.audit import audit as write_audit, verify_chain
from kairos import budget

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


engine = get_engine()


# ── 1. append-only 触发器 ──────────────────────────────
def test_append_only():
    with SASession(engine) as s:
        s.add(User(id=LOCAL_USER_ID, created_at="2026-01-01T00:00:00Z"))
        sess = Session(id=new_id("session"), user_id=LOCAL_USER_ID,
                       title="smoke", created_at="2026-01-01T00:00:00Z",
                       updated_at="2026-01-01T00:00:00Z")
        s.add(sess)
        s.flush()
        run = Run(id=new_id("run"), session_id=sess.id, user_id=LOCAL_USER_ID,
                  status="running", trigger="chat", steps=0,
                  tokens_in=0, tokens_out=0, cost_est=0.0,
                  created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z")
        s.add(run)
        s.flush()
        evt = RunEvent(id=new_id("event"), run_id=run.id, seq=1,
                       event_type="run_started", payload_json="{}",
                       created_at="2026-01-01T00:00:00Z")
        s.add(evt)
        s.commit()
        global _evt_id, _run_id
        _evt_id, _run_id = evt.id, run.id

    # UPDATE 必须被触发器拦截
    try:
        with SASession(engine) as s:
            s.execute(text("UPDATE run_events SET event_type='tampered' WHERE id=:i"),
                      {"i": _evt_id})
            s.commit()
        raise AssertionError("UPDATE run_events 未被触发器拦截")
    except Exception as e:
        if "append-only" not in str(e) and "read-only" not in str(e).lower():
            raise AssertionError(f"拦截原因异常: {e}")

    # DELETE 必须被触发器拦截
    try:
        with SASession(engine) as s:
            s.execute(text("DELETE FROM run_events WHERE id=:i"), {"i": _evt_id})
            s.commit()
        raise AssertionError("DELETE run_events 未被触发器拦截")
    except Exception as e:
        if "append-only" not in str(e) and "read-only" not in str(e).lower():
            raise AssertionError(f"拦截原因异常: {e}")


# ── 2. audit 哈希链 ────────────────────────────────────
def test_audit_chain():
    for i in range(3):
        write_audit("smoke_test", f"test.event.{i}", note=f"round {i}")
    res = verify_chain()
    assert res["broken_at"] is None, f"哈希链校验失败: {res}"
    assert res["checked"] >= 3, f"校验条数不足: {res}"


# ── 3. budget 限额 + 熔断 ─────────────────────────────
def test_budget():
    budget.consume(150_000, 0)  # global/日 维度累计
    st = budget.check()
    assert not st["blocked"], f"首次消费 150K 不应熔断: {st}"
    budget.consume(100_000, 0)  # 累计 250K 超默认 200K
    st = budget.check()
    assert st["blocked"] and st["scope"] == "global", f"超额应被拦截: {st}"

    # 工具熔断器：连续 5 次失败 → 打开；record_success → 恢复
    for _ in range(5):
        budget.record_failure("tool_x")
    assert budget.is_open("tool_x"), "连续 5 次失败应熔断"
    budget.record_success("tool_x")
    assert not budget.is_open("tool_x"), "成功后应恢复"


# ── 4. 幂等键唯一约束 ─────────────────────────────────
def test_idempotency():
    key = f"{_run_id}:1:tool_call"
    with SASession(engine) as s:
        s.add(IdempotencyKey(key=key, result_hash="abc", created_at="2026-01-01T00:00:00Z"))
        s.commit()
    try:
        with SASession(engine) as s:
            s.add(IdempotencyKey(key=key, result_hash="abc", created_at="2026-01-01T00:00:00Z"))
            s.commit()
        raise AssertionError("重复幂等键未被唯一约束拦截")
    except Exception as e:
        if "UNIQUE" not in str(e).upper():
            raise AssertionError(f"拦截原因异常: {e}")


check("append-only 触发器拦截 UPDATE/DELETE", test_append_only)
check("audit 哈希链写入与校验", test_audit_chain)
check("budget 限额与熔断器", test_budget)
check("幂等键唯一约束", test_idempotency)

failed = [r for r in RESULTS if not r[1]]
print(f"\n===== 冒烟结果: {len(RESULTS) - len(failed)}/{len(RESULTS)} 通过 =====")
sys.exit(1 if failed else 0)
