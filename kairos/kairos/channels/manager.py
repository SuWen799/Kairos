# -*- coding: utf-8 -*-
"""通道层（设计 3.3，M4）：IM 收发统一入口。

通道凭据从 config.local.json 的 channels 段读：
  "channels": { "feishu": {"app_id": "cli_xxx", "app_secret": "xxx"} }

绑定量：channel_bindings 表（channel + external_id → session_id）。
命令（首字符 /）：
  /bind <session_id>   绑定当前聊天到指定会话（缺省新建）
  /pending             列出待审批 confirmations
  /ok <cfm_id>[:hash]  批准（哈希前 8 位防 TOCTOU）
  /reject <cfm_id>     拒绝
M4.1：HITL 审批以交互卡片推送（push_approval_card，飞书按钮回调直达 decide），
文本命令保留为兜底。其余文本 → 作为该绑定会话的一轮对话执行并回信。
"""
from __future__ import annotations

import json
import os
import threading

from .. import agent
from ..audit import audit
from ..config import BASE_DIR, LOCAL_CONFIG_PATH
from ..storage import repo
from ..storage.ulid import LOCAL_USER_ID

_channels: dict[str, object] = {}
_started = False


def _load_channel_cfg() -> dict:
    try:
        with open(LOCAL_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("channels", {}) or {}
    except Exception:
        return {}


# ── 绑定与会话路由 ─────────────────────────────────────
def _binding_session(channel: str, external_id: str) -> str:
    """channel+external_id → 绑定会话；没有则新建并登记。"""
    from sqlalchemy import select
    from sqlalchemy.orm import Session as SASession
    from kairos.storage.conn import get_engine
    from kairos.storage.models import ChannelBinding
    from kairos.storage.ulid import new_id
    with SASession(get_engine(), expire_on_commit=False) as s:
        row = s.execute(select(ChannelBinding).where(
            ChannelBinding.channel == channel,
            ChannelBinding.external_id == external_id)).scalar_one_or_none()
        if row:
            return row.session_id
        sess = repo.create_session(f"{channel}:{external_id}")
        s.add(ChannelBinding(id=new_id("channel_binding"), user_id=LOCAL_USER_ID,
                             channel=channel, external_id=external_id,
                             session_id=sess.id, enabled=True,
                             created_at=repo_ts(), updated_at=repo_ts()))
        s.commit()
        return sess.id


def repo_ts() -> str:
    from kairos.storage.models import utcnow
    return utcnow()


# ── 入站处理 ───────────────────────────────────────────
def handle_inbound(channel: str, external_id: str, text: str, reply) -> None:
    """统一入站：命令路由 / 对话执行。reply(text) 为通道回信函数。"""
    text = (text or "").strip()
    if not text:
        return
    audit(f"channel:{channel}", "inbound", note=f"from={external_id} len={len(text)}")
    try:
        if text.startswith("/"):
            reply(_handle_command(channel, external_id, text))
            return
        sess_id = _binding_session(channel, external_id)
        repo.append_message(sess_id, "user", text)
        run = repo.create_run(sess_id, trigger=f"channel:{channel}")
        out = agent.run_turn(text, max_steps=12)
        repo.append_message(sess_id, "assistant", out.get("answer", ""))
        repo.finish_run(run.id, status="done", steps=len(out.get("trace", [])))
        reply(out.get("answer", "") or "（空回复）")
        audit(f"channel:{channel}", "replied", note=f"run={run.id}")
    except Exception as e:  # noqa: BLE001
        reply(f"[出错] {e}")


def _handle_command(channel: str, external_id: str, text: str) -> str:
    parts = text.split(maxsplit=2)
    cmd = parts[0].lower()
    sess_id = _binding_session(channel, external_id)

    if cmd == "/bind":
        if len(parts) < 2:
            return "用法：/bind <session_id>"
        from sqlalchemy import select
        from sqlalchemy.orm import Session as SASession
        from kairos.storage.conn import get_engine
        from kairos.storage.models import ChannelBinding
        with SASession(get_engine(), expire_on_commit=False) as s:
            row = s.execute(select(ChannelBinding).where(
                ChannelBinding.channel == channel,
                ChannelBinding.external_id == external_id)).scalar_one_or_none()
            if row:
                row.session_id = parts[1]
            s.commit()
        return f"已绑定会话 {parts[1]}"

    if cmd == "/pending":
        rows = db_pending()
        if not rows:
            return "当前没有待审批项。"
        return "\n".join(f"#{r['id']} {r.get('action') or ''} {str(r.get('payload') or '')[:60]}" for r in rows)

    if cmd in ("/ok", "/reject"):
        if len(parts) < 2:
            return f"用法：{cmd} <cfm_id 前12位>"
        return _decide(parts[1], approve=(cmd == "/ok"))

    return ("可用命令：/bind <session_id> · /pending · /ok <id> · /reject <id>\n"
            "其它文本直接作为对话执行。")


def db_pending() -> list[dict]:
    import sqlite3
    from ..config import LEGACY_DB_PATH
    conn = sqlite3.connect(LEGACY_DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, action, payload, status, created_at FROM confirmations "
        "WHERE status='pending' ORDER BY id DESC LIMIT 10").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _decide(prefix: str, approve: bool) -> str:
    import sqlite3
    from ..config import LEGACY_DB_PATH
    conn = sqlite3.connect(LEGACY_DB_PATH)
    cur = conn.execute(
        "UPDATE confirmations SET status=? WHERE id LIKE ? AND status='pending' RETURNING id",
        ("approved" if approve else "rejected", prefix + "%"))
    row = cur.fetchone()
    conn.commit()
    conn.close()
    return (f"已{'批准' if approve else '拒绝'} {row[0]}" if row else "没找到匹配的待审批项")


# 公开别名：卡片按钮回调（feishu._on_card_action）与文本命令 /ok 共用同一审批路径
def decide(prefix: str, approve: bool) -> str:
    return _decide(prefix, approve)


# ── M4.1：HITL 卡片主动推送 ────────────────────────────
def _bound_targets(channel: str) -> list[str]:
    """该通道所有启用绑定的 external_id（通常 1 个）。"""
    from sqlalchemy import select
    from sqlalchemy.orm import Session as SASession
    from kairos.storage.conn import get_engine
    from kairos.storage.models import ChannelBinding
    try:
        with SASession(get_engine(), expire_on_commit=False) as s:
            rows = s.execute(select(ChannelBinding.external_id).where(
                ChannelBinding.channel == channel, ChannelBinding.enabled == True)).all()  # noqa: E712
        return [r[0] for r in rows]
    except Exception:
        return []


def push_approval_card(cfm: dict) -> None:
    """新审批产生后向所有绑定用户推卡片（feishu 交互卡；无卡片能力的通道文本兜底）。
    非阻塞：由调用方（tools.propose_notification）在 daemon 线程里调。"""
    if not _channels or not cfm:
        return
    for name, ch in list(_channels.items()):
        try:
            for ext_id in _bound_targets(name):
                if hasattr(ch, "send_card"):
                    ch.send_card(ext_id, cfm)
                elif hasattr(ch, "send"):
                    ch.send(ext_id, f"[待审批 {str(cfm.get('id', ''))[:12]}] "
                                    f"{cfm.get('action', 'propose')}: {str(cfm.get('payload', ''))[:120]}")
            audit(f"channel:{name}", "card.pushed",
                  note=f"cfm={str(cfm.get('id', ''))[:12]}")
        except Exception as e:  # noqa: BLE001
            audit(f"channel:{name}", "card.push_failed", note=str(e)[:200])


# ── 通道启动 ───────────────────────────────────────────
def start_channels() -> dict:
    """启动所有配置了凭据的通道（非阻塞）。返回已启动清单。"""
    global _started
    if _started:
        return {k: "already" for k in _channels}
    _started = True
    started = {}
    cfg = _load_channel_cfg()
    if cfg.get("feishu", {}).get("app_id"):
        try:
            from .feishu import FeishuChannel
            ch = FeishuChannel(cfg["feishu"])
            threading.Thread(target=ch.start, daemon=True).start()
            _channels["feishu"] = ch
            started["feishu"] = "ok"
        except ImportError:
            started["feishu"] = "lark-oapi 未安装（pip install lark-oapi）"
        except Exception as e:  # noqa: BLE001
            started["feishu"] = f"fail: {e}"
    return started


def restart_channels() -> dict:
    """凭据更新后重启全部通道（杀旧 WS 长连接 → 重新拉起）。"""
    global _started
    _started = False
    _channels.clear()
    return start_channels()


def save_channel_cfg(channel: str, creds: dict) -> None:
    """凭据写回 config.local.json（channels 段 merge）。"""
    path = LOCAL_CONFIG_PATH
    doc = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:
        doc = {}
    ch = doc.setdefault("channels", {})
    ch[channel] = {**(ch.get(channel) or {}), **creds}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
