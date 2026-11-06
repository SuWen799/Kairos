# -*- coding: utf-8 -*-
"""新 API 层（设计 3.5）：/api/* 全 ULID + repo 持久化 + SSE 事件协议。

SSE 事件（设计 5.2 全集）：
  run_started / message.delta / tool_start / tool_done / message_final / run_done / error
tool_* 与 run 生命周期事件同步 append 进 run_events（append-only），回放用 GET /api/runs/{id}/events；
message.delta 是高频增量事件，**只走 SSE 不落 run_events**（防表膨胀，设计 8 的预留取舍，M2 定案）。
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import agent, budget, compaction, context
from .audit import audit
from .runtime import tasks as runtime_tasks
from .storage import repo

router = APIRouter(prefix="/api")


# ── 系统 ───────────────────────────────────────────────
@router.get("/meta")
def meta():
    from .config import SETTINGS
    return {"app": "kairos", "version": "0.6.0-v2", "schema": "ulid-v1",
            "multiuser": __import__("os").environ.get("KAIROS_MULTIUSER") == "1",
            "sandbox": SETTINGS.get("sandbox", "basic"),
            "multi_agent": bool(SETTINGS.get("multi_agent", True))}


# ── v2.0 R4：多模型注册表 ───────────────────────────────
@router.get("/models")
def list_models():
    from .config import DEFAULT_MODEL, MODELS
    return {"default": DEFAULT_MODEL,
            "models": [{"name": m["name"], "label": m["label"], "tier": m["tier"]}
                       for m in MODELS]}


# ── v2.0 R5/R6/R7：全局设置（沙箱/多Agent/权限默认档）──
@router.get("/settings")
def get_settings():
    from .config import SETTINGS
    return SETTINGS


class SettingsIn(BaseModel):
    sandbox: str | None = None       # basic|strict
    multi_agent: bool | None = None
    max_subagents: int | None = None


@router.put("/settings")
def put_settings(inp: SettingsIn):
    from .config import save_settings
    patch = {}
    if inp.sandbox in ("basic", "strict"):
        patch["sandbox"] = inp.sandbox
    if inp.multi_agent is not None:
        patch["multi_agent"] = bool(inp.multi_agent)
    if inp.max_subagents is not None:
        patch["max_subagents"] = max(1, min(int(inp.max_subagents), 6))
    if not patch:
        raise HTTPException(400, "无可识别的设置项")
    from . import config as _cfg
    old = dict(_cfg.SETTINGS)
    new = save_settings(patch)
    audit("ui", "settings.updated",
          note=f"{ {k: (old.get(k), v) for k, v in patch.items()} }")
    return new


# ── M6.2：多用户认证（KAIROS_MULTIUSER=1 才启用）────────
class AuthIn(BaseModel):
    email: str
    password: str
    display_name: str = ""


@router.post("/auth/register")
def auth_register(inp: AuthIn):
    if __import__("os").environ.get("KAIROS_MULTIUSER") != "1":
        raise HTTPException(403, "多用户模式未开启（KAIROS_MULTIUSER=1）")
    from . import auth
    try:
        user = auth.register(inp.email, inp.password, inp.display_name)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"user": user, "token": auth.issue_token(user["id"])}


@router.post("/auth/login")
def auth_login(inp: AuthIn):
    if __import__("os").environ.get("KAIROS_MULTIUSER") != "1":
        raise HTTPException(403, "多用户模式未开启（KAIROS_MULTIUSER=1）")
    from . import auth
    uid = auth.verify(inp.email, inp.password)
    if uid is None:
        raise HTTPException(401, "邮箱或密码错误")
    return {"user_id": uid, "token": auth.issue_token(uid)}


# ── 会话 ───────────────────────────────────────────────
class SessionIn(BaseModel):
    title: str | None = None
    project: str = "default"


@router.get("/sessions")
def list_sessions():
    return [{"id": s.id, "title": s.title or "新对话", "project": s.project,
             "updated_at": s.updated_at, "mode": getattr(s, "mode", "chat"),
             "autonomy": getattr(s, "autonomy", "standard"),
             "model": s.model, "goal_ref": getattr(s, "goal_ref", None),
             "plan_md": getattr(s, "plan_md", None)}
            for s in repo.list_sessions()]


@router.post("/sessions")
def create_session(inp: SessionIn):
    s = repo.create_session(inp.title, project=inp.project)
    return {"id": s.id, "title": s.title or "新对话", "project": s.project}


class SessionPatch(BaseModel):
    title: str | None = None
    model: str | None = None          # 模型注册名（v2.0 R4）
    mode: str | None = None           # chat|plan|goal（v2.0 R3）
    autonomy: str | None = None       # standard|semi|auto（v2.0 R5）
    goal_ref: str | None = None
    plan_md: str | None = None        # 显式 null = 清除已批准计划
    project: str | None = None        # 工作区（v2.9，显式 null = 归回默认）


@router.patch("/sessions/{sid}")
def patch_session(sid: str, inp: SessionPatch):
    if repo.get_session(sid) is None:
        raise HTTPException(404, "session not found")
    # exclude_unset：显式传 null 表示清空（plan_md/goal_ref/model）
    fields = {k: v for k, v in inp.model_dump(exclude_unset=True).items()}
    if fields.get("mode") and fields["mode"] not in ("chat", "plan", "goal"):
        raise HTTPException(400, "mode 非法")
    if fields.get("autonomy") and fields["autonomy"] not in ("standard", "semi", "auto"):
        raise HTTPException(400, "autonomy 非法")
    if fields.get("mode") and fields["mode"] != "plan" and "plan_md" not in fields:
        fields["plan_md"] = None  # 离开 plan 模式清掉未批准计划（显式传 null 除外）
    if fields.get("mode") and fields["mode"] != "goal" and "goal_ref" not in fields:
        fields["goal_ref"] = None
    if "project" in fields:
        # project 列 NOT NULL（默认 "default"）：null/空串都归一化为默认工作区
        fields["project"] = (fields["project"] or "").strip() or "default"
    row = repo.update_session(sid, **fields)
    audit("ui", "session.updated", note=f"sid={sid} {fields}")
    return {"id": sid, "mode": row.mode, "autonomy": row.autonomy,
            "model": row.model, "goal_ref": row.goal_ref, "plan_md": row.plan_md,
            "project": getattr(row, "project", None)}


@router.get("/sessions/{sid}/messages")
def get_messages(sid: str):
    if repo.get_session(sid) is None:
        raise HTTPException(404, "session not found")
    return [{"id": m.id, "seq": m.seq, "role": m.role, "content": m.content,
             "created_at": m.created_at, "tokens": m.tokens or 0}
            for m in repo.list_messages(sid)]


# ── 对话（SSE）─────────────────────────────────────────
class ChatIn(BaseModel):
    session_id: str
    text: str
    model: str | None = None


@router.post("/chat/stream")
def chat_stream(inp: ChatIn):
    sess = repo.get_session(inp.session_id)
    if sess is None:
        raise HTTPException(404, "session not found")

    user_msg = repo.append_message(sess.id, "user", inp.text)
    run = repo.create_run(sess.id, trigger="chat", model=inp.model)
    # v3.0 M-S1：会话有活跃工单 → 本 run 接单执行（working），完成回流工单状态
    tkt = repo.open_ticket_for_session(sess.id)
    if tkt is not None:
        repo.transition_ticket(tkt.id, "working", run_id=run.id)

    def sse(d: dict) -> str:
        return f"event: {d['type']}\ndata: {json.dumps(d, ensure_ascii=False)}\n\n"

    def gen():
        yield sse({"type": "run_started", "run_id": run.id,
                   "session_id": sess.id, "user_message_id": user_msg.id})
        q: deque = deque()
        box: dict = {}

        def on_event(d: dict) -> None:
            # 循环线程产生的事件落 run_events（append-only）+ 推 SSE 队列；
            # message.delta 高频增量只推 SSE，不落库（防表膨胀）
            if d.get("type") != "message.delta":
                try:
                    repo.append_event(run.id, d["type"], d)
                except Exception:
                    pass
            q.append(d)

        def work():
            try:
                # M2：先压缩（tier1-5 阶梯），再组装历史窗口注入本轮
                c = compaction.maybe_compact(sess.id)
                if c:
                    on_event({"type": "compaction.performed",
                              "tiers": c.get("tiers", []), "trimmed": c.get("trimmed", 0)})
                ctx = context.assemble(sess.id)
                box["ctx_digest"] = ctx["digest"]
                # v2.0：模型=请求覆盖 > 会话绑定 > 默认；模式/权限/计划/goal 随会话走
                model = inp.model or getattr(sess, "model", None)
                mode = getattr(sess, "mode", "chat") or "chat"
                autonomy = getattr(sess, "autonomy", "standard") or "standard"
                plan_md = getattr(sess, "plan_md", None)
                goal = None
                if mode == "goal" and getattr(sess, "goal_ref", None):
                    try:
                        from . import db as _db
                        rows = _db.query("SELECT title,plan,progress FROM goals WHERE id=?",
                                         (int(sess.goal_ref),))
                        goal = dict(rows[0]) if rows else None
                    except Exception:
                        goal = None
                box["result"] = agent.run_turn(inp.text, on_event=on_event,
                                               model=model, history=ctx["history"],
                                               session_id=sess.id,
                                               mode=mode, autonomy=autonomy,
                                               plan_md=plan_md, goal=goal)
            except Exception as e:  # noqa: BLE001
                box["error"] = str(e)

        th = threading.Thread(target=work, daemon=True)
        th.start()
        while th.is_alive() or q:
            if q:
                yield sse(q.popleft())
            else:
                time.sleep(0.05)
        th.join()

        if "error" in box:
            repo.finish_run(run.id, status="failed", error=box["error"])
            if tkt is not None:
                repo.transition_ticket(tkt.id, "failed", run_id=run.id,
                                       error=box["error"][:200])
            audit("loop", "run.failed", note=f"run={run.id} {box['error'][:200]}")
            yield sse({"type": "error", "run_id": run.id, "message": box["error"]})
            return

        r = box.get("result") or {}
        answer = r.get("answer", "")
        steps = len(r.get("trace", []))
        # v2.8：真实 token 消耗（LLM usage 累加），拿不到再按字数估算兜底
        u = r.get("usage") or {}
        tin = int(u.get("prompt_tokens") or len(inp.text) // 2)
        tout = int(u.get("completion_tokens") or len(answer) // 2)
        repo.append_message(sess.id, "assistant", answer, tokens=tin + tout)
        repo.append_event(run.id, "message_final", {"answer": answer})
        repo.finish_run(run.id, status="done", steps=steps,
                        tokens_in=tin, tokens_out=tout)
        budget.consume(tin, tout, session_id=sess.id)
        if tkt is not None:
            repo.transition_ticket(tkt.id, "ready", run_id=run.id)
        audit("loop", "run.done", tokens_in=tin, tokens_out=tout,
              note=f"run={run.id} steps={steps}")
        yield sse({"type": "message_final", "run_id": run.id,
                   "session_id": sess.id, "answer": answer,
                   "trace": r.get("trace", []),
                   "usage": {"prompt_tokens": tin, "completion_tokens": tout,
                             "total": tin + tout}})
        yield sse({"type": "run_done", "run_id": run.id, "status": "done",
                   "ctx_digest": box.get("ctx_digest")})
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# ── Run 回放与任务 ─────────────────────────────────────
@router.get("/runs/{run_id}/events")
def run_events(run_id: str, after_seq: int = 0):
    return [{"id": e.id, "seq": e.seq, "type": e.event_type,
             "payload": json.loads(e.payload_json or "{}"),
             "created_at": e.created_at}
            for e in repo.list_events(run_id, after_seq=after_seq)]


@router.get("/tasks")
def list_tasks(status: str | None = None):
    return [{"id": t.id, "kind": t.kind, "status": t.status,
             "run_id": t.run_id, "created_at": t.created_at}
            for t in repo.list_tasks(status=status)]


# ── M2：审计 / 预算 / 运行开关 / automations ───────────
@router.get("/audit")
def query_audit(limit: int = 100, actor: str | None = None,
                event_type: str | None = None):
    from sqlalchemy import select
    from sqlalchemy.orm import Session as SASession
    from kairos.storage.conn import get_engine
    from kairos.storage.models import AuditEvent
    q = select(AuditEvent).order_by(AuditEvent.ts.desc()).limit(min(limit, 500))
    with SASession(get_engine(), expire_on_commit=False) as s:
        rows = s.execute(q).scalars().all()
    if actor:
        rows = [r for r in rows if r.actor == actor]
    if event_type:
        rows = [r for r in rows if r.event_type == event_type]
    return [{"id": r.id, "ts": r.ts, "actor": r.actor, "event_type": r.event_type,
             "tool_name": r.tool_name, "risk_level": r.risk_level,
             "tokens_in": r.tokens_in, "tokens_out": r.tokens_out,
             "note": r.note}
            for r in rows]


@router.get("/audit/verify")
def audit_verify():
    from . import audit as _audit
    return _audit.verify_chain()


@router.get("/budgets")
def list_budgets():
    from sqlalchemy import select
    from sqlalchemy.orm import Session as SASession
    from kairos.storage.conn import get_engine
    from kairos.storage.models import Budget
    with SASession(get_engine(), expire_on_commit=False) as s:
        rows = s.execute(select(Budget).order_by(Budget.scope)).scalars().all()
    return [{"scope": r.scope, "period": r.period, "used": r.tokens_used,
             "limit": r.tokens_limit} for r in rows]


class StopIn(BaseModel):
    stopped: bool


@router.post("/runtime/stop")
def runtime_stop(inp: StopIn):
    return {"stopped": runtime_tasks.set_stopped(inp.stopped)}


@router.get("/runtime/status")
def runtime_status():
    st = budget.check()
    return {"stopped": runtime_tasks.is_stopped(), "budget": st}


class AutomationIn(BaseModel):
    name: str
    cron: str
    prompt: str
    session_id: str | None = None


@router.get("/automations")
def list_automations():
    from sqlalchemy import select
    from sqlalchemy.orm import Session as SASession
    from kairos.storage.conn import get_engine
    from kairos.storage.models import Automation
    with SASession(get_engine(), expire_on_commit=False) as s:
        rows = s.execute(select(Automation).order_by(Automation.created_at.desc())).scalars().all()
    return [{"id": a.id, "name": a.name, "cron": a.cron, "prompt": a.prompt,
             "enabled": a.enabled, "next_fire": a.next_fire, "last_run": a.last_run}
            for a in rows]


@router.post("/automations")
def create_automation(inp: AutomationIn):
    from datetime import datetime
    from sqlalchemy.orm import Session as SASession
    from kairos.storage.conn import get_engine
    from kairos.storage.models import Automation
    from .runtime import cron as _cron
    from .storage.ulid import LOCAL_USER_ID, new_id
    nf = _cron.next_fire(inp.cron)
    with SASession(get_engine(), expire_on_commit=False) as s:
        row = Automation(id=new_id("automation"), user_id=LOCAL_USER_ID,
                         name=inp.name, cron=inp.cron, prompt=inp.prompt,
                         session_id=inp.session_id, enabled=True,
                         next_fire=nf.isoformat(timespec="seconds") if nf else None,
                         created_at=repo_ts(), updated_at=repo_ts())
        s.add(row)
        s.commit()
        return {"id": row.id, "next_fire": row.next_fire}


@router.post("/automations/{auto_id}/toggle")
def toggle_automation(auto_id: str):
    from sqlalchemy.orm import Session as SASession
    from kairos.storage.conn import get_engine
    from kairos.storage.models import Automation
    with SASession(get_engine(), expire_on_commit=False) as s:
        row = s.get(Automation, auto_id)
        if row is None:
            raise HTTPException(404, "automation not found")
        row.enabled = not row.enabled
        s.commit()
        return {"id": row.id, "enabled": row.enabled}


def repo_ts() -> str:
    from kairos.storage.models import utcnow
    return utcnow()


@router.get("/channels/status")
def channels_status():
    from .channels import manager as _m
    cfg = _m._load_channel_cfg()
    return {"feishu": {"configured": bool(cfg.get("feishu", {}).get("app_id")),
                       "running": "feishu" in _m._channels},
            "wecom": {"configured": False,
                      "note": "P1：需 frp 公网回调（需求 6.3）"}}


@router.post("/channels/{name}/test")
def channels_test(name: str):
    """连通性自检（M4：飞书鉴权探活）。"""
    if name != "feishu":
        raise HTTPException(404, "unknown channel")
    from .channels import manager as _m
    cfg = _m._load_channel_cfg().get("feishu", {})
    if not cfg.get("app_id"):
        raise HTTPException(400, "config.local.json 未配置 channels.feishu")
    import lark_oapi as lark
    client = (lark.Client.builder().app_id(cfg["app_id"])
              .app_secret(cfg["app_secret"]).build())
    req = lark.auth.v3.TenantAccessTokenInternalRequest.builder() \
        .app_id(cfg["app_id"]).app_secret(cfg["app_secret"]).build()
    resp = client.auth.v3.tenant_access_token_internal(req)
    return {"ok": resp.success, "msg": resp.msg}


# ── 消息渠道配置（OpenDot 式：UI 配凭据 → 重启通道）──────────────

class FeishuCfgIn(BaseModel):
    app_id: str
    app_secret: str


@router.post("/channels/feishu/config")
def feishu_config(inp: FeishuCfgIn):
    """保存飞书凭据并重启通道（WS 长连接重连）。"""
    from .channels import manager as _m
    _m.save_channel_cfg("feishu", {"app_id": inp.app_id.strip(),
                                   "app_secret": inp.app_secret.strip()})
    started = _m.restart_channels()
    audit("ui", "channel.feishu.config", note=f"started={started.get('feishu')}")
    return {"ok": True, "started": started.get("feishu", "unknown"),
            "hint": "WS 长连接后台重连中；记得在飞书开放平台给应用开长连接模式与 im.message.receive_v1 权限"}


@router.post("/channels/restart")
def channels_restart():
    from .channels import manager as _m
    started = _m.restart_channels()
    audit("ui", "channel.restart", note=str(started))
    return {"ok": True, "started": started}


# ── 通用 HTTP 信道入口（WorkBuddy / 脚本 / MCP 桥）：走统一入站路由 ──

class HttpChannelIn(BaseModel):
    sender: str = "workbuddy"
    text: str


@router.post("/channel/http")
def channel_http(inp: HttpChannelIn):
    """外部程序遥控入口：与飞书同一条 handle_inbound 路由（/bind /pending /ok
    命令 + 对话执行）。鉴权：远端请求已被中间件拦（LAN token），
    本机（WorkBuddy 桥）直接放行。同步执行，长任务建议改发"创建定时任务"类指令。"""
    from .channels import manager as _m
    box = {}
    out = _m.handle_inbound(f"http:{inp.sender[:24]}", inp.sender[:64],
                            inp.text, lambda t: box.setdefault("reply", t))
    return {"reply": box.get("reply", out or ""), "sender": inp.sender}


# ── v2.0：工作区产物预览 ───────────────────────────────
_TEXT_EXTS = {".md", ".txt", ".html", ".htm", ".py", ".js", ".ts", ".json",
              ".csv", ".log", ".yml", ".yaml", ".xml", ".css", ".sql"}


@router.get("/workspace/files")
def workspace_files():
    import os as _os
    from .tools import WORKSPACE, _safe_path
    out = []
    for root, _dirs, files in _os.walk(WORKSPACE):
        for fn in files:
            full = _os.path.join(root, fn)
            rel = _os.path.relpath(full, WORKSPACE).replace("\\", "/")
            try:
                st = _os.stat(full)
            except OSError:
                continue
            out.append({"path": rel, "name": fn,
                        "ext": _os.path.splitext(fn)[1].lower(),
                        "size": st.st_size, "mtime": st.st_mtime})
    out.sort(key=lambda x: -x["mtime"])
    return out[:200]


@router.get("/workspace/file")
def workspace_file(path: str):
    import os as _os
    from .tools import _safe_path
    try:
        p = _safe_path(path)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not _os.path.exists(p):
        raise HTTPException(404, "文件不存在")
    st = _os.stat(p)
    ext = _os.path.splitext(p)[1].lower()
    info = {"path": path, "name": _os.path.basename(p), "ext": ext,
            "size": st.st_size, "mtime": st.st_mtime}
    if ext == ".html" or ext == ".htm":
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            info["html"] = f.read()[:500_000]
    elif ext in _TEXT_EXTS and st.st_size < 2_000_000:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            info["content"] = f.read()
    return info


class OpenIn(BaseModel):
    path: str


@router.post("/workspace/open")
def workspace_open(inp: OpenIn):
    """用系统默认程序打开（分享/查看 Excel 等二进制产物的出口）。"""
    import os as _os
    import subprocess as _sp
    from .tools import _safe_path
    try:
        p = _safe_path(inp.path)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not _os.path.exists(p):
        raise HTTPException(404, "文件不存在")
    _os.startfile(p)  # noqa: S606（Windows 本机行为，单用户本地产品）
    return {"ok": True}


@router.post("/workspace/reveal")
def workspace_reveal(inp: OpenIn):
    """在资源管理器中定位文件（分享落点）。"""
    import os as _os
    import subprocess as _sp
    from .tools import _safe_path
    try:
        p = _safe_path(inp.path)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not _os.path.exists(p):
        raise HTTPException(404, "文件不存在")
    _sp.Popen(["explorer", "/select,", p])
    return {"ok": True}


# ── v2.0 R3：Plan 送审 + 确认队列（旧库 confirmations，与 tools 同表）──
class PlanIn(BaseModel):
    session_id: str
    plan_md: str


@router.post("/plans")
def submit_plan(inp: PlanIn):
    """plan 模式产出 → 转为待批准计划（复用 confirmations 审批流）。"""
    from . import db as _db
    from .storage import repo as _repo
    if _repo.get_session(inp.session_id) is None:
        raise HTTPException(404, "session not found")
    import json as _json
    payload = _json.dumps({"session_id": inp.session_id, "plan_md": inp.plan_md[:50_000]},
                          ensure_ascii=False)
    cid = _db.execute(
        "INSERT INTO confirmations(action, payload, status, content_hash, created_at) VALUES(?,?,?,?,?)",
        ("plan", payload, "pending", _payload_hash(payload), _db.now()))
    audit("ui", "plan.submitted", note=f"sid={inp.session_id} cfm={cid} len={len(inp.plan_md)}")
    return {"id": str(cid), "action": "plan", "status": "pending"}


@router.get("/confirmations")
def list_confirmations():
    from . import db as _db
    rows = _db.query("SELECT id,action,payload,status,scope,content_hash,created_at "
                     "FROM confirmations ORDER BY id DESC LIMIT 50")
    return [dict(r) for r in rows]


class DecideIn(BaseModel):
    decision: str  # approved|rejected
    expected_hash: str = ""  # v3.2 提案-哈希 HITL：审阅端回传所展示 payload 的哈希，防 TOCTOU


def _payload_hash(payload: str) -> str:
    import hashlib as _hl
    return _hl.sha256((payload or "").encode("utf-8")).hexdigest()


@router.post("/confirmations/{cfm_id}/decide")
def decide_confirmation(cfm_id: int, inp: DecideIn):
    from . import db as _db
    from .storage import repo as _repo
    import json as _json
    rows = _db.query("SELECT id,action,payload,status,content_hash,session_id FROM confirmations WHERE id=?", (cfm_id,))
    if not rows:
        raise HTTPException(404, "confirmation not found")
    row = dict(rows[0])
    if row["status"] != "pending":
        raise HTTPException(409, f"已决策过：{row['status']}")
    # 提案-哈希校验：审阅端看到的内容必须与队列当前内容一致（老数据无哈希则现算并补齐）
    stored = row.get("content_hash") or ""
    current = _payload_hash(row["payload"])
    if inp.expected_hash and inp.expected_hash != current:
        audit("ui", "approval.hash_mismatch", policy_decision="blocked", risk_level="high",
              tool_name=row["action"], note=f"cfm={cfm_id} expected!={current[:12]}")
        raise HTTPException(409, "内容哈希不一致：你审阅的内容与队列当前内容不同（可能已被改写），已拒绝本次批准，请刷新后重新审阅")
    _db.execute("UPDATE confirmations SET status=?, scope=CASE WHEN scope IS NULL THEN '单次' ELSE scope END, "
                "content_hash=CASE WHEN content_hash IS NULL OR content_hash='' THEN ? ELSE content_hash END "
                "WHERE id=?", (inp.decision, current, cfm_id))
    # v3.2(#1)：semi 档记账时机 = 真实批准（修"提议入队即记账"bug，mattabate/Kairos#1）
    if inp.decision == "approved" and row["action"] != "plan" and row.get("session_id"):
        from . import tools as _tools
        try:
            sess = _repo.get_session(row["session_id"])
            aut = (getattr(sess, "autonomy", "standard") or "standard") if sess else "standard"
        except Exception:
            aut = "standard"
        if aut == "semi" and not _tools.is_high_risk(row["action"], row["payload"]):
            _tools.mark_session_approved(row["session_id"], row["action"])
            audit("ui", "approval.semi_recorded", policy_decision="approved",
                  tool_name=row["action"], note=f"cfm={cfm_id} sid={row['session_id']}")
    if inp.decision == "approved" and row["action"] == "plan":
        # 批准计划：写回 session.plan_md 并切回 chat 模式 + 开工单（Mandate 授权，M-S1）
        try:
            data = _json.loads(row["payload"])
            _repo.update_session(data["session_id"], plan_md=data["plan_md"], mode="chat")
            title = next((ln.strip().lstrip("#* ") for ln in data["plan_md"].splitlines()
                          if ln.strip()), "未命名计划")[:80]
            mandate = _repo.create_mandate(data["session_id"], basis="plan_approval",
                                           scope=title, plan_ref=str(cfm_id))
            _repo.create_ticket(data["session_id"], mandate.id, title,
                                instruction="计划已批准，请按计划开始执行。")
            audit("ui", "ticket.created", note=f"sid={data['session_id']} cfm={cfm_id}")
        except Exception:
            pass
    audit("ui", "approval.decided", policy_decision=inp.decision,
          tool_name=row["action"], note=f"cfm={cfm_id}")
    return {"id": cfm_id, "decision": inp.decision}


# ── v3.0 M-S1：工单（Ticket = 授权工作单元，状态独立于会话）──

@router.get("/tickets")
def list_tickets(session_id: str | None = None, status: str | None = None):
    from .storage import repo as _repo
    rows = _repo.list_tickets(session_id=session_id, status=status)
    return [{"id": t.id, "session_id": t.session_id, "title": t.title,
             "status": t.status, "latest_run_id": t.latest_run_id,
             "error": t.error, "created_at": t.created_at,
             "updated_at": t.updated_at, "finished_at": t.finished_at}
            for t in rows]


class TicketIn(BaseModel):
    session_id: str
    title: str
    instruction: str | None = None
    auto_start: bool = False


@router.post("/tickets")
def create_ticket(inp: TicketIn):
    """人直接开单：explicit 强度授权。auto_start=1 时租约 worker 自动执行。"""
    from .storage import repo as _repo
    if _repo.get_session(inp.session_id) is None:
        raise HTTPException(404, "session not found")
    mandate = _repo.create_mandate(inp.session_id, basis="explicit", scope=inp.title)
    t = _repo.create_ticket(inp.session_id, mandate.id, inp.title,
                            instruction=inp.instruction, auto_start=inp.auto_start)
    audit("ui", "ticket.created", note=f"sid={inp.session_id} tkt={t.id} auto={inp.auto_start}")
    return {"id": t.id, "status": t.status, "mandate_id": mandate.id}


# ── M-S2：事件源观察档（发现 → 一键转工单）──────────────

@router.get("/triggers")
def list_triggers(status: str | None = "observed"):
    from .storage import repo as _repo
    rows = _repo.list_triggers(status=status)
    return [{"id": t.id, "source_kind": t.source_kind, "source_ref": t.source_ref,
             "title": t.title, "payload": t.payload[:500], "status": t.status,
             "gate": t.gate, "assessment": t.assessment, "ticket_id": t.ticket_id,
             "created_at": t.created_at} for t in rows]


@router.post("/triggers/{trigger_id}/promote")
def promote_trigger(trigger_id: str):
    """观察 → 开单（人点一下 = explicit 授权，租约 worker 自动执行）。"""
    from . import evals as _evals
    out = _evals.promote(trigger_id)
    if not out.get("ok"):
        raise HTTPException(400, out.get("msg", "promote 失败"))
    return out


@router.post("/triggers/{trigger_id}/dismiss")
def dismiss_trigger(trigger_id: str):
    from . import evals as _evals
    out = _evals.dismiss(trigger_id)
    if not out.get("ok"):
        raise HTTPException(400, out.get("msg", "dismiss 失败"))
    return out


# ── MCP 能力库（OpenDot 式管理）：mcp.json 增删改 + 热刷新 ──────────

@router.get("/mcp/servers")
def mcp_servers():
    from . import mcp_client
    return {"servers": mcp_client.masked_servers(), "status": mcp_client.get_status()}


class McpServerIn(BaseModel):
    name: str
    command: str
    args: list[str] = []
    env: dict[str, str] = {}
    enabled: bool = True


@router.post("/mcp/servers")
def mcp_add(inp: McpServerIn):
    from . import mcp_client
    out = mcp_client.add_server(inp.name, inp.command, inp.args, inp.env, inp.enabled)
    if not out.get("ok"):
        raise HTTPException(400, out.get("msg", "添加失败"))
    audit("ui", "mcp.server.add", note=inp.name)
    return out


@router.post("/mcp/servers/{name}/toggle")
def mcp_toggle(name: str):
    from . import mcp_client
    out = mcp_client.toggle_server(name)
    if not out.get("ok"):
        raise HTTPException(400, out.get("msg", "切换失败"))
    audit("ui", "mcp.server.toggle", note=name)
    return out


@router.delete("/mcp/servers/{name}")
def mcp_delete(name: str):
    from . import mcp_client
    out = mcp_client.remove_server(name)
    if not out.get("ok"):
        raise HTTPException(400, out.get("msg", "删除失败"))
    audit("ui", "mcp.server.remove", note=name)
    return out


@router.post("/mcp/refresh")
def mcp_refresh():
    """手动全量重连（npx 首跑可能分钟级，后台线程跑不卡请求）。"""
    from . import mcp_client
    mcp_client._refresh_async()
    return {"ok": True, "msg": "后台重连中，稍后点刷新状态查看"}


# ── 长期记忆管理（OpenDot 工作区记忆：可看、可忘）──────────────

@router.get("/memory")
def memory_list():
    from . import memory as _memory
    return _memory.all_memories()


@router.delete("/memory/{mid}")
def memory_forget(mid: int):
    """Muse 的 Forget：用户有权删掉任何一条记忆（删记忆不动聊天历史）。"""
    from . import db as _db
    _db.execute("DELETE FROM memories WHERE id=?", (mid,))
    return {"ok": True}


@router.post("/tickets/{ticket_id}/stop")
def stop_ticket(ticket_id: str):
    """人叫停工单：只有人能停（Executor 报告无权结束授权）。"""
    from .storage import repo as _repo
    t = _repo.transition_ticket(ticket_id, "cancelled")
    if t is None:
        raise HTTPException(404, "ticket not found")
    audit("ui", "ticket.stopped", note=f"tkt={ticket_id}")
    return {"id": ticket_id, "status": t.status}


# ── v2.5：文件上传（图片/Word/PDF → 工作区 + 文本抽取）──
import time as _time

from fastapi import File, UploadFile

_IMG_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}


@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """上传附件：落工作区 uploads/；图片走 Windows OCR，PDF/Word 自动抽文本。
    返回 {saved_path, kind, text_len, preview}——前端把引用块拼进消息，
    LLM 后续用 ws_read 读全文（uploads/<stem>.extracted.txt）。"""
    from .tools import WORKSPACE
    import os as _os

    name = _os.path.basename(file.filename or "file")
    ext = _os.path.splitext(name)[1].lower()
    updir = _os.path.join(WORKSPACE, "uploads")
    _os.makedirs(updir, exist_ok=True)
    stamp = _time.strftime("%Y%m%d_%H%M%S")
    safe = f"{stamp}_{name}"
    path = _os.path.join(updir, safe)
    data = await file.read()
    if len(data) > 20_000_000:
        raise HTTPException(413, "文件超过 20MB 上限")
    with open(path, "wb") as f:
        f.write(data)

    rel = f"uploads/{safe}"
    kind, text, note = "binary", "", ""
    import asyncio as _aio
    try:
        if ext in _IMG_EXTS:
            kind = "image"
            from . import ocr as _ocr
            if not _ocr.ocr_available():
                note = "图片已保存；OCR 引擎未启用（pip install winrt-Windows.Media.Ocr）"
            else:
                # winrt 异步需要独立事件循环：丢工作线程跑（FastAPI 主循环不阻塞）
                text = await _aio.to_thread(_ocr.ocr_image, path)
                note = f"OCR 完成（{len(text)} 字）"
        elif ext == ".pdf":
            kind = "pdf"
            text = await _aio.to_thread(_pdf_extract, path)
            note = f"PDF 抽文本 {len(text)} 字"
        elif ext == ".docx":
            kind = "docx"
            text = await _aio.to_thread(_docx_extract, path)
            note = f"Word 抽文本 {len(text)} 字"
        elif ext in {".txt", ".md", ".csv", ".log", ".json", ".py", ".html"}:
            kind = "text"
            text = data.decode("utf-8", errors="replace")
            note = "文本文件"
        elif ext == ".doc":
            kind = "binary"
            note = "老版 .doc 不支持抽文本，请另存为 .docx"
    except Exception as e:  # noqa: BLE001
        note = f"抽取失败：{e}"

    text_path = ""
    if text:
        text_path = rel.rsplit(".", 1)[0] + (".ocr.txt" if kind == "image" else ".extracted.txt")
        with open(_os.path.join(WORKSPACE, text_path), "w", encoding="utf-8") as f:
            f.write(text)
    audit("ui", "file.uploaded", note=f"{rel} {note}")
    return {"saved_path": rel, "text_path": text_path, "kind": kind,
            "name": name, "text_len": len(text), "preview": text[:400], "note": note}


def _pdf_extract(path: str) -> str:
    from .ocr import extract_pdf
    return extract_pdf(path)


def _docx_extract(path: str) -> str:
    from .ocr import extract_docx
    return extract_docx(path)


# ── v2.5：语音转文字（STT，OpenAI 兼容 /audio/transcriptions，可配 key）──
# v3.1 提速：复用 TLS 连接（免每次请求重新握手）；不走系统代理直连国内源
_stt_session = None

def _stt_http():
    global _stt_session
    if _stt_session is None:
        import requests as _rq
        _stt_session = _rq.Session()
        _stt_session.trust_env = False  # 系统代理多绕海外节点，SiliconFlow 直连反而快
    return _stt_session


@router.get("/stt/status")
def stt_status():
    from .config import CFG
    stt = CFG.get("stt", {})
    return {"configured": bool(stt.get("api_key")),
            "model": stt.get("model", "SenseVoiceSmall"),
            "base": stt.get("base_url", "")}


@router.post("/stt")
async def stt_transcribe(file: UploadFile = File(...)):
    from .config import CFG
    stt = CFG.get("stt", {})
    if not stt.get("api_key"):
        raise HTTPException(400,
            "未配置语音识别：config.local.json 加 \"stt\": {\"base_url\": \"https://api.siliconflow.cn/v1\", \"api_key\": \"...\", \"model\": \"SenseVoiceSmall\"}")
    base = stt["base_url"].rstrip("/")
    r = _stt_http().post(f"{base}/audio/transcriptions",
                 headers={"Authorization": f"Bearer {stt['api_key']}"},
                 files={"file": (file.filename or "audio.webm", await file.read(),
                                 file.content_type or "audio/webm")},
                 data={"model": stt.get("model", "SenseVoiceSmall")}, timeout=60)
    if r.status_code != 200:
        raise HTTPException(502, f"STT 服务返回 {r.status_code}: {r.text[:200]}")
    return r.json()


# ── v2.5：技能库列表（设置页展示）──
@router.get("/skills")
def list_skills():
    from . import db as _db
    rows = _db.query("SELECT name, content, use_count FROM skills ORDER BY use_count DESC, name LIMIT 50")
    return [dict(r) for r in rows]


# ── v2.6：新用户配置向导（CC Switch 式：只贴 Key，模型自动发现）──
SETUP_PRESETS = {
    "deepseek": {"label": "DeepSeek 官方", "base_url": "https://api.deepseek.com/v1"},
    "ark": {"label": "火山方舟（豆包模型）", "base_url": "https://ark.cn-beijing.volces.com/api/v3"},
    "siliconflow": {"label": "SiliconFlow（多模型聚合）", "base_url": "https://api.siliconflow.cn/v1"},
    "moonshot": {"label": "Moonshot Kimi", "base_url": "https://api.moonshot.cn/v1"},
    "custom": {"label": "自定义（OpenAI 兼容）", "base_url": ""},
}


@router.get("/setup/status")
def setup_status():
    from .credentials import get_secret
    from .config import LLM
    has_key = bool(LLM.get("api_key") or get_secret("llm.api_key"))
    return {"needs_setup": not has_key,
            "base_url": LLM.get("base_url", ""),
            "presets": SETUP_PRESETS}


class DiscoverIn(BaseModel):
    preset: str = "deepseek"
    base_url: str | None = None
    api_key: str


@router.post("/setup/discover")
def setup_discover(inp: DiscoverIn):
    """贴 Key 即测：调 /models 拉该 key 可用的全部模型 id（不落盘）。"""
    import requests as _rq
    base = (inp.base_url or SETUP_PRESETS.get(inp.preset, {}).get("base_url", "")).rstrip("/")
    if not base:
        raise HTTPException(400, "自定义服务商需要填 base_url")
    try:
        r = _rq.get(f"{base}/models", headers={"Authorization": f"Bearer {inp.api_key}"}, timeout=15)
    except Exception as e:
        raise HTTPException(502, f"连不上服务商：{e}")
    if r.status_code == 401:
        raise HTTPException(401, "API Key 无效（服务商返回 401），检查是否复制完整")
    if r.status_code != 200:
        raise HTTPException(502, f"服务商返回 {r.status_code}: {r.text[:200]}")
    try:
        ids = [m["id"] for m in r.json().get("data", []) if m.get("id")]
    except Exception:
        raise HTTPException(502, "响应不是预期格式（需要 OpenAI 兼容 /models）")
    return {"base_url": base, "models": ids}


class FinishIn(BaseModel):
    preset: str = "deepseek"
    base_url: str | None = None
    api_key: str
    models: list[dict]          # [{name, model, tier?}]
    default_model: str


@router.post("/setup/finish")
def setup_finish(inp: FinishIn):
    """向导完成：key 进系统凭据管理器（失败降级明文），模型注册表写 config 并热重载。"""
    import json as _json
    from . import config as _cfg
    from .credentials import get_secret, set_secret
    base = (inp.base_url or SETUP_PRESETS.get(inp.preset, {}).get("base_url", "")).rstrip("/")
    if not base or not inp.models:
        raise HTTPException(400, "base_url 与模型列表不能为空")
    keyring_ok = True
    try:
        set_secret("llm.api_key", inp.api_key)
    except Exception:
        keyring_ok = False
    # 读改写 config.local.json（保留其余键）
    doc = {}
    path = _cfg.os.path.join(_cfg.BASE_DIR, "config.local.json")
    if _cfg.os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            doc = _json.load(f)
    llm = doc.get("llm", {})
    llm["base_url"] = base
    llm["model"] = inp.default_model
    llm["api_key"] = "" if keyring_ok else inp.api_key  # keyring 失败才明文（诚实降级）
    doc["llm"] = llm
    doc["models"] = [{"name": m["name"], "label": m.get("label", m["name"]),
                      "model": m["model"], "tier": m.get("tier", "default"),
                      "base_url": base,
                      **({"api_key": inp.api_key} if not keyring_ok else {})}
                     for m in inp.models]
    with open(path, "w", encoding="utf-8") as f:
        _json.dump(doc, f, ensure_ascii=False, indent=2)
    # 回填 CFG（reload 前提）+ 热重载
    _cfg.CFG["llm"] = llm
    _cfg.CFG["models"] = doc["models"]
    _cfg.reload_models()
    from .llm import _clients
    _clients.clear()
    audit("ui", "setup.finished", note=f"preset={inp.preset} models={len(inp.models)} keyring={keyring_ok}")
    return {"ok": True, "keyring": keyring_ok,
            "models": [{"name": m["name"]} for m in doc["models"]],
            "default": _cfg.DEFAULT_MODEL}


# ── v2.6：Agent 中调中枢 ───────────────────────────────
@router.get("/hub")
def hub_list():
    from . import hub
    return hub.list_agents()


class HubRunIn(BaseModel):
    name: str
    prompt: str
    timeout: int = 600


@router.post("/hub/run")
def hub_run(inp: HubRunIn):
    from . import hub
    r = hub.run_agent(inp.name, inp.prompt, inp.timeout)
    audit("loop", "hub.run", tool_name=inp.name,
          note=f"ok={r['ok']} {r.get('note', '')} len={len(r['output'])}")
    return r


# ── v2.0 R3：Goal 面板（旧库 goals 表）─────────────────
@router.get("/goals")
def list_goals():
    from . import db as _db
    rows = _db.query("SELECT id,title,plan,progress,status,updated_at "
                     "FROM goals ORDER BY id DESC LIMIT 30")
    return [dict(r) for r in rows]


@router.post("/goals/{goal_id}/advance")
def advance_goal(goal_id: int):
    """手动「推进一步」：后台线程跑一轮 goal 推进（agent 自己 goal_progress 记账）。"""
    from . import db as _db
    rows = _db.query("SELECT id,title,plan,progress,status FROM goals WHERE id=?", (goal_id,))
    if not rows:
        raise HTTPException(404, "goal not found")
    g = dict(rows[0])
    if g["status"] != "active":
        raise HTTPException(409, "目标已完成或已停止")

    def _work():
        try:
            agent.run_turn(
                f"推进长期目标「{g['title']}」：{g['plan'] or '（无计划，自行拆解）'}。"
                f"当前进展：{(g['progress'] or '（无）')[-800:]}。"
                "本轮推进一小步实质进展（可查资料/写文件），完成后必须用 goal_progress 记录。",
                max_steps=10, model=None)
        except Exception as e:  # noqa: BLE001
            audit("scheduler", "goal.advance_failed", note=f"goal={goal_id} {e}")

    import threading as _th
    _th.Thread(target=_work, daemon=True).start()
    audit("ui", "goal.advance", note=f"goal={goal_id}")
    return {"started": True, "goal_id": goal_id}
