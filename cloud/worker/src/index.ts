/**
 * Kairos 云上长程 Agent（C-S1 骨架）
 * 架构：dsh-superagent 领域模型的 Cloudflare 落地——
 *   Source(cron/webhook) → Trigger → Eval(gate 代码层；assessment 待接 LLM) → Ticket(+Mandate)
 * 状态全部落 Agent DO 内置 SQLite；Alarms 做 Watchdog（到期评估/工单唤醒）。
 * 本机 Kairos sidecar 后续作为 Executor 经中继接入（C-S3）。
 */
import { DurableObject } from "cloudflare:workers";

export interface Env {
  AGENT: DurableObjectNamespace;
  EVAL_BASE_URL?: string;
  EVAL_MODEL?: string;
  DEEPSEEK_API_KEY?: string;
}

const SCHEMA = `
CREATE TABLE IF NOT EXISTS triggers(
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}',
  thread TEXT NOT NULL DEFAULT 'default', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS mandates(
  id TEXT PRIMARY KEY, basis TEXT NOT NULL, scope TEXT NOT NULL DEFAULT '',
  strength TEXT NOT NULL DEFAULT 'explicit', ref TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tickets(
  id TEXT PRIMARY KEY, mandate_id TEXT NOT NULL REFERENCES mandates(id),
  title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
  -- queued|working|waiting_human|ready|failed|cancelled
  instruction TEXT, result TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status, created_at);
`;

const uid = (p: string) => `${p}_${crypto.randomUUID().replaceAll("-", "").slice(0, 20)}`;
const now = () => new Date().toISOString();

export class AgentDO extends DurableObject {
  sql: SqlStorage;

  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    this.sql = ctx.storage.sql;
    this.sql.exec(SCHEMA);
  }

  override async alarm(): Promise<void> {
    // Watchdog：唤醒到期工单（M-S3 租约执行接入前的占位推进）
    this.sql.exec(
      `UPDATE tickets SET status='working', updated_at=? WHERE status='queued'`, now());
  }

  private rows<T = Record<string, unknown>>(q: string, ...args: unknown[]): T[] {
    return [...this.sql.exec(q, ...args)] as T[];
  }

  private json(data: unknown, status = 200): Response {
    return new Response(JSON.stringify(data, null, 1),
      { status, headers: { "content-type": "application/json" } });
  }

  override async fetch(req: Request): Promise<Response> {
    const url = new URL(req.url);
    const p = url.pathname;

    if (req.method === "GET" && p === "/tickets") {
      return this.json(this.rows(`SELECT * FROM tickets ORDER BY created_at DESC LIMIT 100`));
    }

    // 人直接开单：explicit 授权（M-S1 与本机同构）
    if (req.method === "POST" && p === "/tickets") {
      const body = await req.json<{ title: string; instruction?: string }>() as any;
      if (!body?.title) return this.json({ error: "title required" }, 400);
      const mid = uid("mdl"), tid = uid("tkt"), t = now();
      this.sql.exec(`INSERT INTO mandates VALUES (?,?,?,?,?,?)`,
        mid, "explicit", body.title, "explicit", null, t);
      this.sql.exec(`INSERT INTO tickets VALUES (?,?,?,?,?,?,?,?,?)`,
        tid, mid, body.title, "queued", body.instruction ?? null, null, t, t, null);
      await this.ctx.storage.setAlarm(Date.now() + 1000); // Watchdog 唤醒
      return this.json({ id: tid, status: "queued", mandate_id: mid }, 201);
    }

    // Source 入口（webhook/cron 统一收 Trigger；observe 档：只记录不自动开单）
    if (req.method === "POST" && p === "/triggers") {
      const body = await req.json<{ kind: string; payload?: object; thread?: string }>() as any;
      const tid = uid("trg"), t = now();
      this.sql.exec(`INSERT INTO triggers VALUES (?,?,?,?,?)`,
        tid, body?.kind ?? "webhook", JSON.stringify(body?.payload ?? {}),
        body?.thread ?? "default", t);
      return this.json({ id: tid, recorded: true, note: "observe 档：Trigger 已入库，待评估" }, 201);
    }

    // Cron 巡检示例 Source：占位（接入真实盯页在 C-S2）
    if (req.method === "POST" && p === "/patrol") {
      const tid = uid("trg"), t = now();
      this.sql.exec(`INSERT INTO triggers VALUES (?,?,?,?,?)`,
        tid, "cron_patrol", JSON.stringify({ note: "巡检一轮" }), "patrol", t);
      return this.json({ id: tid, ok: true });
    }

    if (req.method === "GET" && p === "/health") return this.json({ ok: true, at: now() });
    return this.json({ error: "not found" }, 404);
  }
}

function panel(): Response {
  const html = `<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>Kairos Cloud Agent</title>
<style>body{font-family:ui-monospace,monospace;max-width:860px;margin:40px auto;padding:0 16px;color:#1b2733}
h1{font-size:18px}.t{border:1px solid #d9e0ea;border-radius:8px;padding:10px 12px;margin:8px 0}
.badge{font-size:12px;border:1px solid #c9d4e3;border-radius:999px;padding:1px 8px;margin-left:8px}
input,button{font:inherit;padding:6px 10px;border:1px solid #c9d4e3;border-radius:8px}
button{background:#3e7bfa;color:#fff;border:none;cursor:pointer}</style></head><body>
<h1>☁ Kairos Cloud Agent <span class="badge">C-S1 骨架</span></h1>
<p>工单（Ticket = 授权工作单元；云端 7×24 常驻，本机 Kairos 为执行器）</p>
<div><input id="title" placeholder="要办的事，如：盯腾讯校招官网"><button onclick="add()">开单</button></div>
<div id="list">加载中…</div>
<script>
async function load(){
  const r = await fetch('/tickets'); const ts = await r.json();
  document.getElementById('list').innerHTML = ts.length ? ts.map(t =>
    '<div class="t"><b>'+t.title+'</b><span class="badge">'+t.status+'</span><br><small>'+t.created_at+'</small></div>'
  ).join('') : '<p>暂无工单</p>';
}
async function add(){
  const title = document.getElementById('title').value.trim(); if(!title) return;
  await fetch('/tickets',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({title})});
  document.getElementById('title').value=''; load();
}
load(); setInterval(load, 5000);
</script></body></html>`;
  return new Response(html, { headers: { "content-type": "text/html; charset=utf-8" } });
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const url = new URL(req.url);
    if (url.pathname === "/" && req.method === "GET") return panel();
    const stub = env.AGENT.get(env.AGENT.idFromName("primary"));
    return stub.fetch(req);
  },

  // Cron Source：每次巡检投递一条 Trigger 到 DO
  async scheduled(_event: ScheduledController, env: Env, ctx: ExecutionContext): Promise<void> {
    const stub = env.AGENT.get(env.AGENT.idFromName("primary"));
    ctx.waitUntil(stub.fetch("https://do/patrol", { method: "POST" }));
  },
};
