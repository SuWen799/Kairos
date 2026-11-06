# Kairos 桌面端设计文档 v1.0（概要 + 详细设计）

> 日期：2026-11-01
> 上游：《Kairos桌面端需求文档.md》v1.5（17 章）——本文档把需求落到"表结构 + 函数签名 + 接口契约 + 目录结构"级别
> 代码基线：`kairos/`（原 evogent 包，13 模块 1723 行，v0.4）
> 状态：待审查。M1 开工按本文档实施，实现中偏离设计要回写本文档。

---

## 0. 术语表

| 术语 | 含义 |
|---|---|
| Session | 一次对话线程（Codex 的"线程"，需求 12.3），UI 左侧列表的一行 |
| Run | 一次 agent 执行（一个 ReAct 循环 queued→终态），全链路 ID |
| Task | Run 的调度壳（后台/HITL/断点恢复才建，需求 11.2 状态机） |
| RunEvent | 执行事件流条目（append-only），UI 过程可视化与回放的数据源 |
| Sidecar | PyInstaller 打包的 FastAPI 后端进程，由 Electron 拉起 |
| Channel | IM 通道适配器（feishu/wecom），需求第 6 章 |
| HITL | Human-in-the-loop，高危动作提议-确认队列 |
| Cordis / preset | DeepSeek Harness 的插件内核 / 运行模式（参考对象，非本项组件） |

---

## 1. 仓库结构与模块划分

```
Kairos/
├── docs/                      # 需求文档 + 设计文档（本文件）+ 调研
├── kairos/                    # Python 后端（sidecar，PyInstaller 打包）
│   ├── run.py                 # 入口：uvicorn 起服务（现 run.py 改造）
│   ├── pyproject.toml         # 替代 requirements.txt（uv 管理）
│   ├── alembic/               # 数据库迁移（新增，需求 12.5）
│   └── kairos/                # Python 包（原 evogent 包改名，M1 第一刀）
│       ├── config.py          # 配置加载（沿用，加 keyring 对接）
│       ├── llm.py             # LLM 调用（沿用+扩展：usage 记账/思考档位）
│       ├── db/                # 数据层（原 db.py 升级为包）
│       │   ├── conn.py        # 连接管理（WAL/foreign_keys，线程本地）
│       │   ├── models.py      # SQLAlchemy 2.x ORM 模型（12 章全表）
│       │   ├── repo/          # 仓储层（每表一个 repo：查询封装，禁裸 SQL 散落）
│       │   └── ulid.py        # ULID 生成 + 类型前缀
│       ├── agent/             # Agent 内核（原 agent.py + tools.py 升级）
│       │   ├── loop.py        # 主循环（原 run_turn 重构，需求 10.3 步法）
│       │   ├── tools/         # 工具注册表 + 内置工具（原 tools.py 拆分）
│       │   ├── context.py     # 上下文组装（KV-cache 顺序/遮蔽/目标重申）
│       │   └── compaction.py  # 压缩阶梯（需求 8.2 五级，M2 做 1-3 级）
│       ├── memory.py          # 长期记忆（沿用+user_id+加密钩子）
│       ├── skills.py          # 技能库（沿用）
│       ├── rsi.py             # RSI 循环（沿用+failure_class 字段）
│       ├── runtime/           # 持久执行（新增，需求第 11 章）
│       │   ├── tasks.py       # 任务状态机 + 租约心跳
│       │   ├── checkpoint.py  # checkpoint 读写
│       │   └── scheduler.py   # 统一调度（原 scheduler.py+watcher.py 合并管）
│       ├── channels/          # IM 通道（新增，M4）
│       │   ├── base.py / feishu.py / wecom.py / manager.py
│       ├── audit.py           # audit_events 写入 + 哈希链（新增，9.5）
│       ├── budget.py          # token 预算/熔断（新增，8.4）
│       ├── mcp_client.py      # MCP 桥（沿用+scope 过滤）
│       ├── watcher.py         # 内容盯梢（沿用，调度权移交 runtime）
│       └── api/               # FastAPI 路由（原 server.py 拆分）
│           ├── app.py         # app 工厂 + 中间件（token 鉴权/CORS/审计）
│           ├── routes_sessions.py / routes_chat.py / routes_tasks.py
│           ├── routes_memory.py / routes_kb.py / routes_confirm.py
│           ├── routes_audit.py / routes_watch.py / routes_apps.py
│           └── sse.py         # SSE 响应封装
├── desktop/                   # Electron 壳（新增，M1）
│   ├── electron/main.ts       # 主进程：spawn sidecar/watchdog/托盘/keyring
│   ├── electron/preload.ts    # contextBridge 最小 IPC
│   └── electron-builder.yml
└── web/                       # Vue3 渲染层（新增；桌面与 PWA 共用同一套）
    ├── src/views/             # ChatView / TasksView / AuditView / SettingsView ...
    ├── src/components/        # 三栏布局组件树
    ├── src/stores/            # Pinia：session/task/confirm/budget/settings
    └── src/api/               # 后端客户端（fetch 封装 + SSE 解析）
```

**现状→新结构迁移映射**：`agent.py:run_turn`→`agent/loop.py`；`tools.py` 21 个 `tool_*`→`agent/tools/`（按能力族分文件）；`server.py`（428 行 40+ 路由）→`api/routes_*` 拆分；`db.py:get_conn/execute/query`→`db/` 包；`scheduler.py+watcher.py` 调度权集中到 `runtime/scheduler.py`。

---

## 2. 数据库设计（DDL 级）

> 全表通用列省略不写：`user_id TEXT NOT NULL`、`created_at TEXT`、`updated_at TEXT`（UTC ISO-8601）。主键一律 ULID 文本（需求 12.2）。建表走 Alembic migration `0001_initial`，单用户期 user_id 恒 `usr_local`。

### 2.1 对话与执行域

```sql
-- 会话线程
CREATE TABLE sessions (
  id TEXT PRIMARY KEY,              -- sess_01J...
  project TEXT DEFAULT 'default',   -- 项目分组（秋招/AskDrive/日常）
  title TEXT, title_auto BOOLEAN DEFAULT 1,
  model TEXT, archived INTEGER DEFAULT 0
);
CREATE INDEX idx_sessions_user ON sessions(user_id, updated_at DESC);

-- 对话消息（展示层）
CREATE TABLE messages (
  id TEXT PRIMARY KEY,              -- msg_...
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  seq INTEGER NOT NULL,             -- 会话内单调递增，排序键
  role TEXT CHECK(role IN ('user','assistant','tool','system')),
  content TEXT,                     -- 纯文本/markdown
  tool_calls_json TEXT,             -- assistant 的工具调用块
  tokens INTEGER
);
CREATE INDEX idx_messages_session ON messages(session_id, seq);

-- 执行记录（一次 ReAct 循环）
CREATE TABLE runs (
  id TEXT PRIMARY KEY,              -- run_...
  session_id TEXT NOT NULL REFERENCES sessions(id),
  status TEXT CHECK(status IN ('queued','running','done','failed','cancelled')),
  trigger TEXT,                     -- chat|task|automation|channel:wecom|channel:feishu
  model TEXT, steps INTEGER,
  tokens_in INTEGER DEFAULT 0, tokens_out INTEGER DEFAULT 0, cost_est REAL DEFAULT 0,
  error TEXT, started_at TEXT, ended_at TEXT
);
CREATE INDEX idx_runs_session ON runs(session_id, started_at DESC);

-- 执行事件流（append-only，禁 UPDATE/DELETE——用触发器强制）
CREATE TABLE run_events (
  id TEXT PRIMARY KEY,              -- evt_...
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  seq INTEGER NOT NULL,
  event_type TEXT NOT NULL,         -- 见 5.2 枚举
  payload_json TEXT NOT NULL,       -- 事件数据；大 payload 存文件存路径
  ctx_digest TEXT                   -- 本轮上下文注入摘要（dsh 透明原则）
);
CREATE INDEX idx_run_events_run ON run_events(run_id, seq);
```

### 2.2 调度与恢复域

```sql
CREATE TABLE tasks (
  id TEXT PRIMARY KEY,              -- task_...
  run_id TEXT REFERENCES runs(id),
  kind TEXT,                        -- background|hitl|scheduled
  status TEXT CHECK(status IN ('queued','running','waiting_approval','paused',
                               'done','failed','cancelled','interrupted')),
  checkpoint_json TEXT,             -- 11.3：步指针+结构化状态+待恢复 payload 引用
  lease_until REAL, heartbeat_at REAL,
  retry_count INTEGER DEFAULT 0, budget_limit INTEGER,
  scheduled_for TEXT                -- automation 触发时间（可空）
);
CREATE INDEX idx_tasks_dispatch ON tasks(status, lease_until);
CREATE INDEX idx_tasks_user ON tasks(user_id, status);

CREATE TABLE idempotency_keys (
  key TEXT PRIMARY KEY,             -- <sess>:<run>:<operation>
  result_hash TEXT, result_ref TEXT, created_at TEXT
);

CREATE TABLE checkpoints (          -- 与 tasks 分表：checkpoint 可独立清理
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
  step INTEGER, state_json TEXT, artifact_refs_json TEXT
);
```

### 2.3 Agent 能力域（现有 8 表的升级对照）

| 现表（v0.4） | 新表 | 变更点 |
|---|---|---|
| messages | messages | 加 seq/ULID/外键；原 session_id INTEGER→TEXT |
| memories | memories | 加 embedding_ref（sqlite-vec 预留）、confidence、forgettable |
| skills | skills | 加 path（SKILL.md 文件路径）、last_used |
| tasks(旧 goals) | 拆为 tasks + automations | 旧"任务"实为 todo，并入 sessions 流；goal 概念并入 automations |
| failures | failures | 加 failure_class（五分类，需求 10.6） |
| confirmations | confirmations | 加 content_hash（防 TOCTOU）、expires_at、scope |
| sources | sources | 加 content_hash/file_ref |
| applications | applications | 结构不变，加 ULID/外键 |
| —— | watch_targets | 原 watch_sources 改名，加 cadence/next_check |
| —— | channel_bindings / budgets / audit_events / idempotency_keys / checkpoints / schema_version | 全新 |

### 2.4 其余域（要点）

```sql
-- HITL 确认队列（9.3）
CREATE TABLE confirmations (
  id TEXT PRIMARY KEY, run_id TEXT,
  action TEXT,                      -- notify|wecom_send|feishu_send|tool:<name>
  content_json TEXT, content_hash TEXT,   -- 批准时校验哈希一致
  scope TEXT DEFAULT 'once',        -- once|session|task|timed|permanent
  expires_at TEXT, decision TEXT,   -- pending|approved|rejected|expired
  approver TEXT, decided_at TEXT
);

-- 审计（9.5：append-only + 简化哈希链）
CREATE TABLE audit_events (
  id TEXT PRIMARY KEY, ts TEXT, actor TEXT, event_type TEXT,
  risk_level TEXT, policy_decision TEXT, tool_name TEXT,
  params_hash TEXT, result_hash TEXT, tokens_in INTEGER, tokens_out INTEGER,
  approver TEXT, prev_hash TEXT, note TEXT
);

-- 预算（8.4：period 形如 2026-11 / 2026-11-01）
CREATE TABLE budgets (
  id TEXT PRIMARY KEY, period TEXT, scope TEXT,   -- global|channel:wecom|session:<id>
  tokens_used INTEGER DEFAULT 0, tokens_limit INTEGER, cost_limit REAL
);
CREATE UNIQUE INDEX idx_budgets_scope ON budgets(scope, period);
```

其余：`users / memories / skills / kb_docs / kb_chunks / sources / applications / watch_targets / automations / channel_bindings` 按需求 12.4 清单同构展开，列已在需求文档定案，此处不重复。**M1 验收物之一：`alembic upgrade head` 一次建全 20 表 + 回填 v0.4 旧数据脚本。**

---

## 3. 后端函数设计（Python）

> 约定：函数签名写"模块.函数(参数) -> 返回"；`[新]`=新增，`[改]`=现有函数改造，`[沿]`=沿用不动。

### 3.1 Agent 内核

```python
# agent/loop.py
[改] run_turn(session_id, user_text, on_event, max_steps=14)
     -> RunResult(final_text, run_id, steps, tokens)
     # 重构点：① 创建 run 行并全程写 run_events；② 上下文组装移交 context.py；
     #        ③ 步数上限改为 reportStepLimit 语义（需求 10.3）；④ token 记账进 run 行

[新] context.assemble(session_id, run_id) -> ContextBundle
     # 上下文组装四段：system(小而稳) + 规则文件(按需) + 记忆摘要 + 最近消息
     # 返回 ctx_digest（写入 run_events，UI 可见"本轮注入了什么"）

[新] context.mask_tools(tools, phase) -> tools
     # 工具遮蔽：全量 schema 常驻，按执行阶段标记 unavailable（不删，保 KV-cache）

[新] compaction.maybe_compact(session_id) -> CompactionResult | None
     # 四级状态机判定（8.4）；M2 实现 tier1 微压缩/tier2 工具结果收缩/tier3 会话记忆压缩

# agent/tools/（原 tools.py 21 个 tool_* 全部 [沿]，改造点）
[改] register(tool_fn, name, description, params_model, risk_level, scope)
     # 新注册协议：Pydantic params（9.4 强类型）+ 风险分级 + capability scope
     # execute_tool 统一走：参数校验 → 风险路由（低/中自动，高转 HITL）→ 超时 → 审计
[新] tools.agent_tools_run_python(code)     # 原 tool_run_python 加 Job Object 预留位
```

### 3.2 Runtime（全 [新]，需求第 11 章）

```python
# runtime/tasks.py
create_task(run_id, kind, budget_limit=None) -> task_id
claim_next(worker_id) -> Task | None          # 租约认领（status+lease_until 索引）
heartbeat(task_id, worker_id)                  # 续租；三分离 liveness
reap_expired() -> list                         # 租约过期→interrupted/重排队（沉默→结构化失败）
transition(task_id, new_status)                # 状态机校验（非法迁移抛错+审计）

# runtime/checkpoint.py
save_checkpoint(run_id, step, state, artifacts) -> checkpoint_id
load_latest(run_id) -> Checkpoint | None       # 崩溃恢复入口

# runtime/scheduler.py（合并原 scheduler.py+watcher.py 调度权）
[改] start_scheduler()                          # 单线程统一轮：automations→watch→预算重置→审计清理
[新] register_automation(cron, session_ref, prompt) -> auto_id
[新] scheduler_tick() -> None                   # cron 随机抖动错峰（11.5）
```

### 3.3 通道层（全 [新]，M4，需求第 6 章）

```python
# channels/base.py
class Channel(Protocol):
    name: str
    def start(self) -> None: ...                 # feishu: ws 长连接；wecom: 注册回调路由
    def stop(self) -> None: ...
    def send(self, binding_id, text, card=None) -> str: ...   # 返回 message_id

# channels/feishu.py
class FeishuChannel:
    [新] on_message(event)                       # lark-oapi 回调：去重(event_id)→白名单→入队
    [新] send_card_with_buttons(proposal)        # HITL 卡片；按钮回调→approve/reject

# channels/manager.py
[新] dispatch_inbound(channel, external_id, text)
     # 白名单校验→channel_bindings 查绑定→合并缓冲(5s)→创建 run(trigger=channel:*)→回执
[新] channel_loop()                              # 独立线程，消费 inbound 队列
```

### 3.4 横切模块

```python
# audit.py [新]
audit(actor, event_type, *, risk=None, tool=None, tokens=(0,0),
      policy=None, approver=None, note=None) -> None
# 写 audit_events 并链 prev_hash；actor ∈ {loop, subagent:<name>, scheduler, channel:*}

# budget.py [新]
check(session_id, channel=None) -> BudgetVerdict   # 熔断判定：四维聚合
consume(run_id, tokens_in, tokens_out) -> None     # runs/budgets 双记
circuit_break(tool_name) / is_open(tool_name)      # 连续失败冷却（9.6）

# api/app.py [新]
create_app() -> FastAPI
# 中间件链：TokenAuth（动态端口 token，除 /health）→ 审计计时 → 异常统一格式
# CORS 仅允许 app:// 与 http://localhost:<vite端口>
```

### 3.5 REST/SSE 接口清单（对照现有 40+ 路由的升级）

| 分组 | 端点 | 变更 |
|---|---|---|
| 系统 | `GET /health` · `GET /api/meta` | [改] meta 返回版本/schema_version/sandbox_level |
| 会话 | `GET/POST /api/sessions` · `PATCH/DELETE /api/sessions/{id}` · `GET /api/sessions/{id}/messages` | [改] 全部带 ULID + project 分组参数 |
| 对话 | `POST /api/chat` · `POST /api/chat/stream`(SSE) | [改] 请求带 session_id；SSE 事件协议见 5.2；响应带 run_id |
| 任务 | `GET /api/tasks?status=` · `POST /api/tasks/{id}/pause|resume|cancel` · `GET /api/runs/{id}/events`(SSE 回放) | [新] |
| 确认 | `GET /api/confirmations` · `POST /api/confirmations/{id}/approve|reject` + `?scope=once|session|timed&ttl=` | [改] 加 scope 五档 + 哈希校验（现 approve(cid, scope) 扩展） |
| 记忆/技能 | `GET/DELETE /api/memory/{id}` · `GET /api/skills` | [沿] |
| KB/台账/盯梢 | `kb_* / apps_* / watch_*` 现有路由 | [沿]（换 ULID） |
| 审计/预算 | `GET /api/audit?user&from&to` · `GET /api/budgets` · `POST /api/budgets/{scope}` | [新] |
| 通道 | `GET /api/channels/status` · `POST /api/channels/{name}/test` | [新]（M4） |
| MCP | `GET /api/mcp/status` · `POST /api/mcp/refresh` | [沿] |

---

## 4. 前端设计（Electron + Vue3）

### 4.1 Electron 主进程（desktop/electron/main.ts）

```typescript
[新] spawnSidecar(): Promise<ChildProcess>
  // 开发态 spawn python run.py；发布态拉 resources/backend.exe
  // ① 端口 0（系统分配）→ sidecar stdout 回报实际端口；② 生成一次性 token 传入
[新] waitHealthy(port, token, timeout=30s)     // 轮询 /health，失败重试 1 次后报错
[新] watchdog(): setInterval                    // 30s 探活，连续 3 次失败→宽限→拉活（11.6）
[新] createWindow()                             // contextIsolation:true / sandbox:true / nodeIntegration:false
[新] createTray()                               // 托盘：打开主控台/暂停全部任务(kill switch)/退出
[新] killSwitch()                               // POST /api/runtime/stop → 托盘态标记
[沿-经验] keyring 存 token；关闭窗口=优雅停机（SIGTERM→等 sidecar 退出→强杀）
```

### 4.2 渲染层组件树（三栏，需求第 2 节）

```
AppLayout
├── Sidebar（左栏）
│   ├── ProjectGroup（项目分组：秋招/AskDrive/日常）
│   ├── SessionList（会话线程列表 + 新建/归档）
│   ├── AutomationList（定时任务，M2）
│   └── ConfirmBadge（待确认数红点 → 点击开确认队列）
├── ThreadView（中栏，路由主区）
│   ├── MessageList（气泡流 + ToolCallCard 折叠展示工具过程）
│   ├── CompactionBanner（"已压缩，查看摘要"条，M2）
│   ├── ContextMeter（token 四态：正常/黄/红/阻塞，8.4）
│   ├── Composer（输入框 + 模型选择 + 技能 @ 引用）
│   └── StepLimitNotice（"说 continue 接着干"提示条）
├── SummaryPane（右栏）
│   ├── PlanPanel（todos，读 /api/tasks + todo 工具产物）
│   ├── SourcesPanel（本次 run 抓取的 sources 引用回链）
│   ├── ArtifactsPanel（workspace 产物预览：md 渲染/PDF/图片）
│   └── BudgetPanel（本轮/本会话/本日 token 消耗）
└── Modals
    ├── ConfirmDialog（HITL 审批：完整动作内容+diff+回滚方案，禁纯 Continue）
    ├── DiffViewer（工作区文件改动对比）
    └── SettingsView（模型/key/通道/沙箱级别标注/审计查询）
```

### 4.3 Pinia stores（关键 state 与 action）

```typescript
// stores/session.ts
state: sessions[], currentId, messages[], streaming
[新] openSession(id) / newSession(project) / sendMessage(text)
     // sendMessage: POST /api/chat/stream → 逐事件更新（见 5.2 SSE 协议）
// stores/task.ts
state: tasks[], runEvents: Map<run_id, Event[]>
[新] pollTasks() / pauseTask(id) / resumeTask(id) / replayRun(runId)  // 回放读 events
// stores/confirm.ts
state: pending[], scope
[新] approve(id, scope, ttl?) / reject(id)    // 双端同步：IM 批准后本端轮询刷新
// stores/budget.ts  [新] refresh(period) —— 驱动 ContextMeter 与 BudgetPanel
// stores/settings.ts [沿-扩展] model / channels / sandboxLevel(只读展示)
```

移动端（M3）：同一 `web/` 代码，`AppLayout` 按 `matchMedia` 折叠为单栏 + 底部 Tab（对话/任务/确认/我的），lan_server 模式下加 Bearer token 登录页。构建入口：`vite build --mode desktop`（Electron 加载本地文件）与 `--mode web`（sidecar 静态托管，PWA manifest）。

---

## 5. 关键时序与事件协议

### 5.1 对话主流程（发消息 → 流式回复）

```
用户输入 → Composer → POST /api/chat/stream {session_id, text}
  → api 层: 建 sess?(复用) → msg(user) → run(queued→running)
  → agent/loop.run_turn(on_event=写入 run_events + 推 SSE)
      ├ evt: run.started / llm.call / tool.call / tool.result / run.compacted ...
      ├ 遇高风险工具 → 建 confirmation → evt: approval.required → SSE 透出 → 循环挂起
      └ 终态 → msg(assistant) 落库 → run.status=done + token 聚合 → evt: run.done
Renderer: fetchEventSource 逐事件 → MessageList 增量渲染 / ToolCallCard 更新 / ContextMeter 变色
```

### 5.2 SSE 事件协议（run_event.event_type 枚举 + 线格式）

| event_type | payload 要点 | UI 反应 |
|---|---|---|
| run.started / run.done / run.failed | run_id, status, tokens | 状态条更新 |
| llm.call | model, step, prompt_tokens | ContextMeter |
| tool.call / tool.result | tool, params_digest, risk, elapsed, ok | ToolCallCard 折叠卡 |
| approval.required / approval.decided | confirmation_id, action, content_hash | ConfirmDialog 弹出/消账 |
| compaction.performed | tier, tokens_saved | CompactionBanner |
| budget.blocked | scope, used, limit | 阻塞提示 + 追加预算入口 |
| message.delta | text 增量 | 气泡流式渲染 |
| run.step_limit | steps, hint="说 continue" | StepLimitNotice |

线格式（SSE）：`event: <event_type>\ndata: {"run_id":"run_..","seq":42,...}\n\n`。
**M2 定案（偏离回写 2026-11-01）**：当前实现的事件名与上表简化对齐为 `run_started / message.delta / tool_start / tool_done / message_final / run_done / error`；其中 `message.delta` 只走 SSE **不落 run_events**（高频增量防表膨胀，设计 8 预留取舍已定），其余事件与 run_events 表 1:1，回放接口（`GET /api/runs/{id}/events`）用同一协议。

### 5.2b HITL 审批时序（IM 端批准为例）

```
loop 挂起(confirmation pending, task=waiting_approval, worker 释放)
  → channels/feishu.send_card_with_buttons(提议+哈希)
  → 用户点"批准" → 飞书回调 → manager: 校验白名单→confirmations 哈希比对→decision=approved
  → runtime 唤醒 task(queued) → worker 认领 → loop 从 checkpoint 续跑 → run.resumed
  → 桌面端 ConfirmBadge 下次轮询消账（双端最终一致，轮询 10s）
```

---

## 6. 安全实现映射（需求第 9 章 → 代码落点）

| 需求条款 | 实现落点 |
|---|---|
| token 鉴权 / 动态端口 | api/app.py TokenAuth 中间件；main.ts spawnSidecar 传 token |
| 参数强类型 | Pydantic params_model 即工具 schema 即 API schema（一份定义三处用） |
| 风险路由 | tools/register.execute_tool 的 risk_level 路由（高→confirmations） |
| 内容哈希防 TOCTOU | confirmations.content_hash；approve 时比对，不一致拒并审计 |
| 审计哈希链 | audit.py：`prev_hash = sha256(上一行 id+ts+event_type)` |
| keyring | desktop: keytar；python 侧 config.py 启动时从 keyring 读 LLM key（缺失则回退 config.local 并提示迁移） |
| IM 白名单/脱敏 | channels/manager 白名单校验；出站正则脱敏 key 形态字符串 |
| 沙箱级别标注 | /api/meta 返回 sandbox_level；SettingsView 只读展示（14.3 诚实原则） |

---

## 7. M1 交付清单（设计→实施对照）

| # | 交付物 | 验收 |
|---|---|---|
| 1 | 包改名 evogent→kairos + 仓库结构调整（第 1 节目录树） | `python run.py` 起服务全绿 |
| 2 | Alembic 0001：20 表建齐 + v0.4 数据回填脚本 | 旧 db 数据无损迁移 |
| 3 | ULID/前缀/幂等键工具层 + repo 层 | 单测覆盖 ID 格式与并发 |
| 4 | Electron 壳：spawn/watchdog/托盘/token | 双击起全栈；关窗口全退 |
| 5 | Vue3 三栏 UI + 会话/对话/SSE 流 | 现有功能全可用；审计页可见全部工具调用 |
| 6 | audit.py + 工具风险分级 + confirmations 哈希 | 伪造审批/过期审批被拒且留痕 |
| 7 | user_id 全表 + 目录分桶 + config 迁移 keyring | 建表脚本带 user_id；config.local 明文 key 迁出 |

M2（压缩阶梯/tasks 状态机/automations/diff 视图）、M3（lan_server/PWA）、M4（channels）待 M1 验收后出对应细化设计（预计各自半页时序 + 若干函数签名，在本文件追加"第 8 章 M2-M4 详细设计"分节）。

---

## 8. 诚实声明

- 本文是**概要+关键详细设计**，不是逐行实现规格：repo 层具体 SQL、组件 props 细节留白，按 M1 实施时以"最小可跑"补齐并回写。
- 现有 1723 行代码是真实资产：`tool_*` 21 个工具、rsi/watcher/skills 逻辑 [沿]，设计不重写它们，只换壳（包结构/ID/审计/风险路由）。
- SSE 与 run_events 1:1 是设计目标，`message.delta` 类高频事件可能采样落库（防表膨胀），取舍在 M1 实施时定并回写。
- 端口 0 动态分配 + stdout 回报在 Windows 下有一个已知坑（缓冲），备选方案：固定高位端口 + 探测占用，实施时二选一回写。

---

## 9. M2-M6 实现记录（2026-11-01 回写）

**M1（全部交付）**：端口策略定案=固定高位 8630 起探测占用（诚实声明二选一已定）；`message.delta` 未做（LLM 流式接入后补，SSE 事件协议其余 6 类 1:1 落地）。

**M2 内核**：
- `context.py`：assemble(keep_last=20) + CJK 保守 token 估算 + ctx_digest（run_done 事件带回）。
- `compaction.py`：tier1 窗口裁剪 + tier3 LLM 摘要（Memory kind=summary，每会话上限 3 条防退化；软限 60K tokens）。tier2 天然满足（run_events 只存元数据）。tier4/5 M3+ 按需。
- `runtime/tasks.py`：worker 认领→每 15s 心跳→执行→transition；reap_expired 收租约过期→interrupted 可重认领；kill switch（GraphDrained：跑完手头就停）。
- `runtime/cron.py`：5 段简化 cron + 0-120s 随机抖动；scheduler 改统一轮（60s tick：automations→reap→watch 间隔点）。
- API：`/api/audit`、`/api/audit/verify`、`/api/budgets`、`/api/runtime/stop|status`、`/api/automations` CRUD+toggle；UI InfoPane 事件实时轮询/审计链校验/预算条。

**M3 遥控**：`KAIROS_LAN=1` 绑 0.0.0.0 + Bearer token（run.py 生成落 data/lan_token.txt；/health 与 /app 静态豁免，实测 401/200 矩阵全对）；`/app/{path}` 托管 Electron 渲染层构建产物（KAIROS_WEB_DIST 可覆盖）；PWA manifest+SW（shell cache-first）+ token 登录门。

**M4 通道**：`channels/manager.py`（绑定路由 channel+external_id→session；命令 /bind /pending /ok /reject——HITL 先用文本命令替代卡片，M4.1 补交互卡片）+ `channels/feishu.py`（lark-oapi WS 长连接，免公网；config.local.json channels.feishu 凭据门控）。wecom=P1 占位（需 frp 公网回调）。

**M5 打包**：`scripts/build_backend.py`（PyInstaller onedir→dist/backend/backend.exe，hidden-import 已列全）；electron-builder.yml extraResources 已配（签名/公证需证书，发布时执行）。

**M6 多用户地基**：`auth.py`（PBKDF2 10万轮注册/校验，KAIROS_MULTIUSER=1 门控，未开启时拒绝；session token 层 M6.2 接 JWT）。全表 user_id/目录分桶/审计链即账单源已在 M1 落地。

---

## 10. M2 补课 + M4.1 + M6.2 实现记录（2026-11-01 深夜第二轮）

**message.delta 流式（M2 补课完成）**：`llm.chat_stream()`（openai stream=True，tool_calls 按 index 分片聚合，异常自动回落非流式且关闭增量回调防双份正文）；`agent.run_turn` 每步 LLM 调用走流式并外推 `message.delta`；api.py on_event 对 delta 只推 SSE 不落 run_events；前端 session store 实时长到占位消息 + ThreadPane 追尾滚动（用户回看 >120px 不打断）。实测真实 LLM：28 delta 片拼合 == 非流式 content，端到端事件链 `run_started → message.delta → final → message_final → run_done → done` 全过。

**⚠️ 修复一颗大雷（audit 模块名遮蔽）**：api.py / channels/manager.py / runtime/tasks.py 三处 `from . import audit` 导入的是**模块**而非 `audit()` 函数，`audit("loop","run.done",...)` 直接 TypeError——表现为**每次成功对话的 SSE 流在 final 事件后被掐断**（message_final/run_done/done 永远发不出去）。已统一改为 `from .audit import audit`（budget.py 本来就是对的写法）。历史 E2E 未发现是因为此前测的是旧路由 server.py /chat/stream。

**M4.1 飞书 HITL 卡片（完成）**：`feishu.send_card()`（interactive 卡：批准=primary / 拒绝=danger，按钮 value 带 cfm_id+act）+ 同一 WS 长连接 `register_p2_card_action_trigger` 收按钮回调 → `manager.decide()` 落库（与 /ok 文本命令同路径）→ toast 回执。`tools.tool_propose_notification` 产生新审批时 daemon 线程调 `manager.push_approval_card()` 推给所有绑定用户（无卡片能力通道文本兜底）。真实凭据未配置，卡片实际收发待飞书应用上线后验证。

**M6.2 session token（完成）**：`auth.issue_token/verify_token`（紧凑 JWT HS256，stdlib hmac 实现零新依赖；签名密钥 data/auth_secret 自动生成 0600）；`/api/auth/register|login`（KAIROS_MULTIUSER=1 门控）；server.py 中间件两档：多用户模式全 API 要 Bearer JWT（/health、/app 静态、/api/auth/* 豁免），否则回落 M3 LAN token。实测矩阵：/health 200、无 token 401、JWT 200、伪造 401、篡改 401、错密码 401、重复注册 409、带 JWT 会话读写 200——全对。

**仍开放**：飞书真实凭据实测；旧 web 单文件退役（需求 16 章问题 2 待拍板）。

---

## 11. M3 收尾 + M5 补齐 + tier4/5 实现记录（2026-11-01 深夜第三轮）

**PWA 单列移动布局（M3 收尾）**：App.vue 增加 `matchMedia(max-width:900px)` 移动态——44px 顶栏（汉堡=会话抽屉 / Kairos 铭牌+相位点 / ◍=仪表面板抽屉）、ThreadPane 全宽、SidePane/InfoPane 变 fixed 抽屉（非 scoped 样式穿透 + `.drawer-open` 控制 transform）、scrim 点击收起、用户回看不打断规则同桌面。桌面三栏零影响（全部变更包在 media query 与 `.mobile` 类内）。electron-vite 构建绿（renderer 234.77KB）。诚实标注：**未在真机浏览器实际预览**，断端行为以 CSS 逻辑与构建为准，待手机同网段实测。

**tier4/5 压缩兜底（需求 8.2 五级阶梯全通）**：
- tier4 全文压缩：超 72K 硬限触发，LLM 按**结构化保留清单**（任务目标/文件路径/最近工具结果/生效规则/计划进度）压成一条 `〔压缩摘要〕` 消息；原文 Write 落盘 `workspace/_compact/<session>.md` 后删除（有损+可归档找回）；压缩 prompt 内置注入声明 + 产出正则校验（请执行/请调用/忽略之前/ignore previous → 判失败回落机械拼接）。
- tier5 紧急裁剪：超 76K 紧急线触发，**不走 LLM** 机械保留最近 6 条其余归档删除，保证会话不死。
- 防退化三件套全部落地：重试上限 3（连续失败熔断 raise，不静默重试）；时机前置（60K 软限 tier1+3 → 72K tier4 → 76K tier5 逐级复核估算）；注入防护见上。
- `compaction.performed` 事件接入 SSE + run_events（InfoPane 事件页可见）。
- **实测**：180 条消息 / 85,036 tokens → tier1+3 + tier4 → 1 条消息 / 582 tokens，归档 513KB，摘要消息在位。

**M5 补齐**：crashReporter（本地转储恒开，上传恒关——无接收端，诚实）；遥测开关 `telemetry.json`（默认关）；electron-updater 动态加载脚手架（包缺失优雅降级托盘"检查更新"跳 Releases；本机 npm install 被系统 SIGTERM 无法装包，降级是现状）；electron-builder.yml 定 `publish: github`；`.github/workflows/release.yml` 三平台矩阵（tag 触发，证书 secret 槽位已留，未配则产物未签名）；requirements.txt 补齐真实依赖（sqlalchemy/alembic/keyring/lark-oapi/pyinstaller）；README 增"M5 发布口径诚实声明"章。

---

## 12. DeerFlow 集成（方案 C）实现记录（2026-11-02 凌晨第四轮）

**定案**：用户拍板方案 C——Kairos 内核保留自研（pi 级克制主循环），DeerFlow 2.3.0-dev 部署为**本地深度研究引擎**，Kairos 以 `deep_research` 工具路由（子代理模式），不做全量迁移（DeerFlow 缺 RSI/自演化、HITL 粒度、审计预算、IM 通道）。源码 tarball 落 `Kairos/参考项目/deer-flow/`（GitHub 拉取失败，codeload tarball 兜底成功）。

**部署**：独立 venv `envs/deerflow`（pip 装 packages/extension-api + packages/harness + app 依赖，`pip install -e` 因 flat-layout 报错弃用）；启动器 `kairos/scripts/start_deerflow.py`（前台 subprocess.call 常驻，PYTHONPATH=backend）。**三个启动坑与修法**：
1. 路径层级算错（scripts 下三层 dirname 才到 Kairos 根，`参考项目` 在 Kairos 内部不需 `..`）；
2. config.yaml 找不到 → 加 `DEER_FLOW_CONFIG_PATH=<Kairos>/参考项目/deer-flow/config.yaml`；
3. 认证 401 → 加 `DEER_FLOW_AUTH_DISABLED=1`（网关仅绑 127.0.0.1，本地引擎场景合理）。

**API 实测协议（`POST /api/runs/wait`）**：请求体 `{"input":{"messages":[{"role":"user","content":...}]}}`；CSRF 为 double-submit 模式（cookie `csrf_token` 与 header `X-CSRF-Token` 同值即可，服务端不校验值来源，机器对机器自签一对即可）；响应 `messages` 数组，最后一条 `type:"ai"` 的 `content` 即最终答案；DeepSeek (deepseek-flash) 真实回复验证通过（token_usage 5705）。

**Kairos 侧接入**：`tools.py` 增 `tool_deep_research(topic, depth)`——/health 预检（未启动优雅降级提示改用 fetch_url/web_search）→ POST runs/wait（CSRF 自签对，timeout 900s）→ 取最后 AI 消息 → `tool_ws_write` 落 `reports/deep_research_<ts>.md` → 返回路径+摘要；TOOLS_SPEC + TOOL_IMPL 注册（第 24 个工具）；agent.py SYSTEM_TMPL 规则 10 引导 LLM 路由（复杂多源调研 → deep_research，简单自查 → fetch_url/web_search）。

**E2E 验证全过**：冒烟（"只回复两个字：收到"）→ HTTP 200；工具单测（LangGraph 一句话调研）→ 2593 字报告落 `kairos/data/workspace/reports/deep_research_20261102_015000.md`；重启 Kairos 桌面后 sidecar 重载，TOOLS_SPEC 27 工具含 deep_research；双服务健康（DeerFlow 8001 / Kairos 8630 全 200）。

**遗留**：① DeerFlow 深档（多轮 web 搜索）真实耗时未测（DuckDuckGo 免 key）；② DeerFlow 自身 /setup 管理员未创建（AUTH_DISABLED 模式不需要）；③ 网关进程需与 Kairos 同起同停（start_deerflow.py 未挂进 Electron sidecar 生命周期，手动后台任务管理）。
