"""FastAPI 服务：chat / rsi / memory / skills / confirmations + 静态首页。"""
import json
import os
import threading

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import agent, db, mcp_client, memory, rsi, scheduler, skills, tools, watcher
from .api import router as api_router

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
app = FastAPI(title="Kairos")
app.include_router(api_router)

# CORS（设计 3.4，M1 漏 Implementation，桌面壳 file:// 渲染层 fetch 被拦现补）：
# 允许 file:// 的 null 源（Electron 生产态）、自定义 app:// 协议、vite dev 端口。
# 2026-11-02：dev 端口改正则匹配任意 localhost 端口——5173 被其它项目占用时
# electron-vite 会退到 5174+，写死 5173 会导致渲染层全被 CORS 拦（"点不开"根因之二）。
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["null", "app://"],
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup():
    from .storage.conn import get_engine
    from .storage.models import Base
    Base.metadata.create_all(get_engine())  # 幂等建表（v3.0 M-S1 新增 mandates/tickets 等）
    from .storage.migrations import ensure_extra_columns
    try:
        applied = ensure_extra_columns()
        if applied:
            print(f"[migrate] v2.0 列迁移：{list(applied)}")
    except Exception as _e:
        print(f"[migrate] 列迁移失败（忽略，可能已应用）：{_e}")
    scheduler.start_scheduler()
    threading.Thread(target=mcp_client.refresh, daemon=True).start()  # MCP 桥接不阻塞启动
    try:
        from .channels import manager as _chm
        started = _chm.start_channels()
        if started:
            print(f"[channels] 已启动：{started}")
    except Exception as _e:
        print(f"[channels] 启动失败：{_e}")


class ChatIn(BaseModel):
    text: str
    session_id: int | None = None
    model: str | None = None


class RsiIn(BaseModel):
    task_family: str
    rounds: int = 3


class AppIn(BaseModel):
    company: str
    position: str
    status: str = "投递"
    source_url: str = ""
    notes: str = ""


class AppPatchIn(BaseModel):
    status: str | None = None
    notes: str | None = None


class WatchIn(BaseModel):
    name: str
    url: str


@app.get("/health")
def health():
    """sidecar 探活契约（Electron watchdog 轮询）。"""
    from . import lan as _lan
    return {"status": "ok", "app": "kairos", "lan": _lan.enabled()}


# ---- M3 重构：LAN 手机遥控（OpenDot 式配对）----
# 绑定策略：sidecar 恒绑 0.0.0.0（desktop main.ts 注入），是否放行远端由 data/lan.json 决定，
# 运行时开关/换 token 均即时生效，无需重启。默认关闭：远端一律 403。


@app.middleware("http")
async def _lan_auth(request, call_next):
    """鉴权中间件（M3 重构）：
    - loopback（本机桌面/浏览器）：全放行；管理端点天然只受本机控制
    - 非 loopback：LAN 关闭 → 403；开启 → /health 与 /app 静态豁免，其余需配对 token
      （?token= 或 Bearer；EventSource/浏览器跳转带不了 header，统一走 query）
    """
    from . import lan as _lan
    client = request.client.host if request.client else None
    loopback = _lan.is_loopback(client)
    p = request.url.path
    if not loopback:
        if not _lan.enabled():
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "lan disabled"}, status_code=403)
        _public = p == "/health" or p.startswith("/app") or p == "/favicon.ico" or p == "/"
        if not _public:
            auth = request.headers.get("authorization", "")
            supplied = auth.removeprefix("Bearer ").strip() or request.query_params.get("token", "")
            if not supplied or supplied != _lan.token():
                from fastapi.responses import JSONResponse
                return JSONResponse({"detail": "token required"}, status_code=401)
    # KAIROS_MULTIUSER=1（云端形态）保持原有 JWT 档（走到这里必为 loopback）
    if os.environ.get("KAIROS_MULTIUSER") == "1":
        if not (p == "/health" or p.startswith("/app") or p == "/favicon.ico" or p.startswith("/api/auth")):
            auth = request.headers.get("authorization", "")
            token = auth.removeprefix("Bearer ").strip() or request.query_params.get("token", "")
            from . import auth as _auth
            if not token or _auth.verify_token(token) is None:
                from fastapi.responses import JSONResponse
                return JSONResponse({"detail": "jwt required"}, status_code=401)
    return await call_next(request)


# ---- LAN 管理端点：仅本机可调（中间件已放行 loopback，这里再显式校验）----

def _loopback_only(request):
    from . import lan as _lan
    client = request.client.host if request.client else None
    if not _lan.is_loopback(client):
        from fastapi.responses import JSONResponse
        raise HTTPException(status_code=403, detail="仅本机可操作")


@app.get("/api/lan/status")
def lan_status(request: Request):
    _loopback_only(request)
    from . import lan as _lan
    return _lan.status(include_secret=True)


class LanToggleIn(BaseModel):
    enabled: bool


@app.post("/api/lan/toggle")
def lan_toggle(inp: LanToggleIn, request: Request):
    _loopback_only(request)
    from . import lan as _lan
    _lan.set_enabled(inp.enabled)
    return _lan.status(include_secret=True)


@app.post("/api/lan/rotate")
def lan_rotate(request: Request):
    _loopback_only(request)
    from . import lan as _lan
    _lan.rotate()
    return _lan.status(include_secret=True)


@app.get("/app/{rest:path}")
def app_static(rest: str):
    """手机端 SPA（Electron 构建产物，KAIROS_WEB_DIST 指定；缺省回落桌面构建）。"""
    dist = _web_dist()
    target = os.path.normpath(os.path.join(dist, rest or "index.html"))
    if not target.startswith(os.path.normpath(os.path.abspath(dist))) or not os.path.isfile(target):
        target = os.path.join(dist, "index.html")  # SPA 路由回落
    if os.path.isfile(target):
        return FileResponse(target)
    return FileResponse(os.path.join(BASE_DIR, "web", "index.html"))  # 构建产物缺失时的占位


def _web_dist() -> str:
    dist = os.environ.get("KAIROS_WEB_DIST") \
        or os.path.join(BASE_DIR, "..", "desktop", "out", "renderer")
    return dist


@app.get("/")
def index():
    """根路径与 /app 同源同壳：手机扫 / 二维码、PWA start_url 均落同一 SPA。"""
    dist = _web_dist()
    target = os.path.join(dist, "index.html")
    if os.path.isfile(target):
        return FileResponse(target)
    return FileResponse(os.path.join(BASE_DIR, "web", "index.html"))


@app.post("/chat")
def chat(inp: ChatIn):
    sid = _resolve_session(inp.session_id)
    db.execute("INSERT INTO messages(role, content, created_at, session_id) VALUES(?,?,?,?)",
               ("user", inp.text, db.now(), sid))
    out = agent.run_turn(inp.text, session_id=sid, model=inp.model)
    db.execute("INSERT INTO messages(role, content, created_at, session_id) VALUES(?,?,?,?)",
               ("assistant", out.get("answer", ""), db.now(), sid))
    _autotitle(sid, inp.text)
    out["session_id"] = sid
    return out


@app.post("/chat/stream")
def chat_stream(inp: ChatIn):
    """SSE 流式对话：实时吐工具事件（抄 open-webui 的 SSE 事件流协议）。"""
    import json as _json
    import time as _time
    from collections import deque
    from fastapi.responses import StreamingResponse

    sid = _resolve_session(inp.session_id)
    db.execute("INSERT INTO messages(role, content, created_at, session_id) VALUES(?,?,?,?)",
               ("user", inp.text, db.now(), sid))

    def gen():
        q = deque()
        ev = q.append
        def on_event(d):
            ev(d)
        import threading
        box = {}
        def run():
            box["result"] = agent.run_turn(inp.text, on_event=on_event, session_id=sid, model=inp.model)
        th = threading.Thread(target=run, daemon=True)
        th.start()
        while th.is_alive() or q:
            if q:
                d = q.popleft()
                yield f"data: {_json.dumps(d, ensure_ascii=False)}\n\n"
            else:
                import time as _time
                _time.sleep(0.05)
        th.join()
        r = box.get("result") or {}
        db.execute("INSERT INTO messages(role, content, created_at, session_id) VALUES(?,?,?,?)",
                   ("assistant", r.get("answer", ""), db.now(), sid))
        _autotitle(sid, inp.text)
        yield "data: " + _json.dumps(
            {"type": "answer", "answer": r.get("answer", ""), "trace": r.get("trace", []), "session_id": sid},
            ensure_ascii=False) + "\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---- 会话管理（Open WebUI / LobeChat 抄改）----

def _resolve_session(session_id: int | None) -> int:
    """有 id 用 id；没有则用最新会话；一个会话都没有则建「新对话」。"""
    if session_id and db.query("SELECT id FROM sessions WHERE id=?", (session_id,)):
        return session_id
    row = db.query("SELECT id FROM sessions ORDER BY id DESC LIMIT 1")
    if row:
        return row[0]["id"]
    return db.execute("INSERT INTO sessions(title, created_at) VALUES(?,?)",
                      ("新对话", db.now()))


def _autotitle(sid: int, first_text: str):
    """会话自动命名（LobeChat 式：LLM 起标题，失败回落首句截断）。"""
    row = db.query("SELECT title FROM sessions WHERE id=?", (sid,))
    if row and row[0]["title"] == "新对话":
        title = ""
        try:
            from . import llm as _llm
            t = _llm.chat_text("给这段用户请求起一个不超过 10 字的标题，直接输出标题本身，不要引号：", first_text[:120])
            title = t.strip().strip('"\'「」')[:16]
        except Exception:
            pass
        title = title or first_text.strip().split("\n")[0][:24] or "新对话"
        db.execute("UPDATE sessions SET title=? WHERE id=?", (title, sid))


@app.get("/sessions")
def sessions_list():
    rows = db.query(
        """SELECT s.id, s.title, s.created_at,
                  (SELECT COUNT(*) FROM messages m WHERE m.session_id=s.id) AS msg_count
           FROM sessions s ORDER BY s.id DESC LIMIT 50""")
    return [dict(r) for r in rows]


@app.post("/sessions")
def sessions_create():
    sid = db.execute("INSERT INTO sessions(title, created_at) VALUES(?,?)", ("新对话", db.now()))
    return {"id": sid}


class SessionPatchIn(BaseModel):
    title: str


@app.patch("/sessions/{sid}")
def sessions_rename(sid: int, inp: SessionPatchIn):
    db.execute("UPDATE sessions SET title=? WHERE id=?", (inp.title.strip()[:40] or "新对话", sid))
    return {"ok": True}


@app.delete("/sessions/{sid}")
def sessions_delete(sid: int):
    db.execute("DELETE FROM sessions WHERE id=?", (sid,))
    db.execute("DELETE FROM messages WHERE session_id=?", (sid,))
    return {"ok": True}


@app.get("/messages")
def messages(limit: int = 80, session_id: int | None = None):
    """历史对话（正序返回，供前端回放）。不指定会话时回放最新会话。"""
    if session_id:
        rows = db.query(
            "SELECT id, role, content, created_at FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?",
            (session_id, limit))
    else:
        row = db.query("SELECT id FROM sessions ORDER BY id DESC LIMIT 1")
        if not row:
            return []
        rows = db.query(
            "SELECT id, role, content, created_at FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?",
            (row[0]["id"], limit))
    return [dict(r) for r in reversed(rows)]


# ---- 知识库 RAG（Open WebUI / Dify 抄改：上传→切片→检索）----

@app.get("/kb")
def kb_list():
    rows = db.query(
        "SELECT doc_name, COUNT(*) AS chunks, MAX(created_at) AS uploaded_at FROM kb GROUP BY doc_name ORDER BY uploaded_at DESC")
    return [dict(r) for r in rows]


@app.post("/kb/upload")
async def kb_upload(file: UploadFile):
    import time as _t
    raw = await file.read()
    text = raw.decode("utf-8", errors="replace")
    name = os.path.basename(file.filename or "未命名.txt")
    # 切片：约 400 字一块（中文友好），重叠 50 字
    step, size = 350, 400
    chunks = [text[i:i + size] for i in range(0, len(text), step) if text[i:i + size].strip()]
    db.execute("DELETE FROM kb WHERE doc_name=?", (name,))  # 重传覆盖
    for c in chunks:
        db.execute("INSERT INTO kb(doc_name, chunk, created_at) VALUES(?,?,?)", (name, c, _t.time()))
    return {"ok": True, "doc": name, "chunks": len(chunks)}


@app.delete("/kb/{doc_name}")
def kb_delete(doc_name: str):
    db.execute("DELETE FROM kb WHERE doc_name=?", (doc_name,))
    return {"ok": True}


@app.delete("/memory/{mid}")
def memory_forget(mid: int):
    """Muse 的 Forget 能力：用户有权删掉任何一条记忆。"""
    db.execute("DELETE FROM memories WHERE id=?", (mid,))
    return {"ok": True}


# ---- 长期目标（Muse goals 模式）----

@app.get("/goals")
def goals_list():
    rows = db.query("SELECT * FROM goals ORDER BY status='done', id DESC LIMIT 30")
    return [dict(r) for r in rows]


class GoalIn(BaseModel):
    title: str
    plan: str = ""


@app.post("/goals")
def goals_create(inp: GoalIn):
    msg = tools.tool_goal_add(inp.title, inp.plan)
    return {"ok": "已登记" in msg, "msg": msg}


class GoalPatchIn(BaseModel):
    status: str  # active | done | paused


@app.patch("/goals/{gid}")
def goals_patch(gid: int, inp: GoalPatchIn):
    db.execute("UPDATE goals SET status=?, updated_at=? WHERE id=?", (inp.status, db.now(), gid))
    return {"ok": True}


@app.delete("/goals/{gid}")
def goals_delete(gid: int):
    db.execute("DELETE FROM goals WHERE id=?", (gid,))
    return {"ok": True}


@app.post("/rsi/run")
def run_rsi(inp: RsiIn):
    return rsi.run_rsi(inp.task_family, inp.rounds)


@app.get("/memory")
def get_memory():
    return memory.all_memories()


@app.get("/skills")
def get_skills():
    return skills.all_skills()


@app.get("/failures")
def get_failures():
    return [dict(r) for r in db.query("SELECT * FROM failures ORDER BY id DESC LIMIT 20")]


@app.get("/confirmations")
def get_confirmations():
    return [dict(r) for r in db.query("SELECT * FROM confirmations ORDER BY id DESC LIMIT 20")]


@app.post("/confirmations/{cid}/approve")
def approve(cid: int, scope: str = "once", expected_hash: str = ""):
    """scope: once=仅此一次 / forever=永久授权同类动作（Muse Sentinel 审批粒度）。
    v3.2 提案-哈希 HITL：expected_hash 与队列当前 payload 不一致时拒绝批准（防 TOCTOU）。"""
    import hashlib as _hl
    row = db.query("SELECT * FROM confirmations WHERE id=?", (cid,))
    if not row or row[0]["status"] != "pending":
        return {"ok": False, "msg": "不存在或已处理"}
    payload = row[0]["payload"]
    current = _hl.sha256((payload or "").encode("utf-8")).hexdigest()
    if expected_hash and expected_hash != current:
        audit("ui", "approval.hash_mismatch", policy_decision="blocked", risk_level="high",
              tool_name=row[0]["action"], note=f"cfm={cid}")
        return {"ok": False, "msg": "内容哈希不一致：你审阅的内容与队列当前内容不同（可能已被改写），请刷新后重新审阅",
                "current_hash": current}
    scope_label = "永久授权" if scope == "forever" else "单次"
    db.execute("UPDATE confirmations SET status='approved', scope=?, "
               "content_hash=CASE WHEN content_hash IS NULL OR content_hash='' THEN ? ELSE content_hash END WHERE id=?",
               (scope_label, current, cid))
    # v3.2(#1)：semi 档记账时机 = 真实批准（与 api.decide_confirmation 同口径）
    if scope == "once" and row[0]["action"] != "plan" and row[0].get("session_id"):
        try:
            from .storage import repo as _repo
            sess = _repo.get_session(row[0]["session_id"])
            aut = (getattr(sess, "autonomy", "standard") or "standard") if sess else "standard"
            if aut == "semi" and not tools.is_high_risk(row[0]["action"], row[0]["payload"]):
                tools.mark_session_approved(row[0]["session_id"], row[0]["action"])
        except Exception:
            pass
    if scope == "forever":
        sig = row[0]["payload"][:60]
        db.execute("INSERT INTO allowlist(action, sig, created_at) VALUES(?,?,?)",
                   (row[0]["action"], sig, db.now()))
    return {"ok": True, "msg": f"已批准（{scope_label}）：{row[0]['payload'][:80]}"}


# ---- 盯校招 / 投递台账 / 工作区 ----

@app.get("/watch/sources")
def watch_sources():
    return watcher.list_sources()


@app.post("/watch/sources")
def watch_add(inp: WatchIn):
    msg = watcher.add_source(inp.name, inp.url)
    return {"ok": ("开始盯" in msg) or ("已在盯" in msg), "msg": msg}


@app.delete("/watch/sources/{sid}")
def watch_delete(sid: int):
    db.execute("DELETE FROM sources WHERE id=?", (sid,))
    return {"ok": True}


@app.post("/watch/run")
def watch_run():
    return scheduler.run_once()


@app.get("/applications")
def applications():
    rows = db.query("SELECT * FROM applications ORDER BY updated_at DESC LIMIT 50")
    return [dict(r) for r in rows]


@app.post("/applications")
def app_add(inp: AppIn):
    import time as _t
    db.execute(
        "INSERT INTO applications(company,position,status,source_url,notes,updated_at) VALUES(?,?,?,?,?,?)",
        (inp.company, inp.position, inp.status, inp.source_url, inp.notes, _t.time()))
    return {"ok": True}


@app.patch("/applications/{aid}")
def app_patch(aid: int, inp: AppPatchIn):
    import time as _t
    row = db.query("SELECT id FROM applications WHERE id=?", (aid,))
    if not row:
        return {"ok": False, "msg": "记录不存在"}
    if inp.status is not None:
        db.execute("UPDATE applications SET status=?, updated_at=? WHERE id=?", (inp.status, _t.time(), aid))
    if inp.notes is not None:
        db.execute("UPDATE applications SET notes=?, updated_at=? WHERE id=?", (inp.notes, _t.time(), aid))
    return {"ok": True}


@app.delete("/applications/{aid}")
def app_delete(aid: int):
    db.execute("DELETE FROM applications WHERE id=?", (aid,))
    return {"ok": True}


@app.get("/workspace")
def workspace_list():
    return json.loads(agent.tools.tool_ws_list())


@app.get("/workspace/content")
def workspace_content(name: str):
    return {"name": name, "content": agent.tools.tool_ws_read(name)}


# ---- 计划任务（deepagents write_todos 抄改）----

@app.get("/todos")
def todos():
    rows = db.query("SELECT id,title,status,created_at FROM tasks ORDER BY id DESC LIMIT 30")
    return [dict(r) for r in rows]


class TodoPatchIn(BaseModel):
    status: str  # open | done


@app.patch("/todos/{tid}")
def todo_patch(tid: int, inp: TodoPatchIn):
    db.execute("UPDATE tasks SET status=? WHERE id=?", (inp.status, tid))
    return {"ok": True}


@app.delete("/todos/{tid}")
def todo_delete(tid: int):
    db.execute("DELETE FROM tasks WHERE id=?", (tid,))
    return {"ok": True}


# ---- 多 Agent 团队 / MCP ----

class AgentRunIn(BaseModel):
    role: str
    task: str


@app.get("/agents")
def agents_roster():
    """子代理名册 + 当前工具总量（含 MCP 桥接的）。"""
    return {
        "roles": [{"role": k, "label": v["label"], "desc": v.get("desc", "")} for k, v in tools.SUBAGENT_ROLES.items()],
        "builtin_tools": len(tools.TOOL_IMPL),
        "mcp_tools": sorted(t["function"]["name"] for t in tools.TOOLS_SPEC if t["function"]["name"].startswith("mcp_")),
    }


@app.post("/agents/run")
def agents_run(inp: AgentRunIn):
    out = agent.run_turn(inp.task, max_steps=8,
                         system_override=tools.SUBAGENT_ROLES.get(inp.role, {}).get("prompt", "").format(task=inp.task)
                         if inp.role in tools.SUBAGENT_ROLES else None)
    return {"role": inp.role, "answer": out["answer"], "steps": len(out["trace"])}


@app.get("/models")
def models_list():
    """多模型列表（比 Muse 强：不锁单一模型，前端运行时切换）。"""
    from . import config as _cfg
    return _cfg.MODELS


@app.get("/mcp/status")
def mcp_status():
    return mcp_client.get_status()


@app.post("/mcp/refresh")
def mcp_refresh():
    return mcp_client.refresh()
