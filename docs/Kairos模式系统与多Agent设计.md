# Kairos 模式系统与多 Agent 设计（M7）

> 状态：**待拍板**。本文档对齐市面主流 Agent 的模式设计，结合 Kairos「中道」内核哲学（主循环克制、能力层插件化、权限硬约束）给出落地方案。拍板后按 M7.x 里程碑开工。
> 2026-11-02 凌晨 · 前置调研：docs/ 四份开源 Agent 调研 + DeerFlow 实际部署经验

---

## 1. 市面对照：各家的"模式"怎么做

| 产品 | 模式体系 | 多 Agent | Goal/长期任务 | 值得吸收的点 |
|------|---------|----------|--------------|-------------|
| **Claude Code** | Plan Mode（只读调研→出计划→用户批准→转执行）；default / acceptEdits / bypassPermissions 权限三档 | Subagents：可自定义角色（名字+提示词+工具白名单），主对话 Task 工具派发，**并行**执行，只回结论 | 无（靠外部 TODO） | 计划批准是硬门槛：批准前 agent 摸不到写工具 |
| **Codex 桌面版** | Approve 三档：每步问 / 编辑免审 / 全自动；无显式 plan 模式（计划内嵌在回复里） | 单 agent，无子代理 | 无 | 审批粒度与 UI 呈现（diff 卡片） |
| **DeerFlow 2.3** | is_plan_mode 标志位；Plan 模式下 lead agent 只产出计划文档 | subagent_enabled，max_concurrent_subagents=3、max_total_subagents=6，LangGraph 图内并行 | 无 | 并发上限参数化（防 token 失控） |
| **deepagents** | 无模式 | `task` 工具派发子代理，**子代理只回结论不回过程**（上下文隔离） | 无 | "只回结论"防主上下文被过程污染——Kairos 已吸收 |
| **Goose** | 权限档（approve on request / always approve）；Plan 即普通 markdown | 无原生子代理 | 无 | lead/worker 提示词分离 |
| **OpenMuse / Muse** | 无显式模式 | 无 | **goals 模式**：登记长期目标，系统周期性自动推进一小步 | Kairos goal 雏形即源于此，需补进度可视 |
| **AutoGen / CAMEL** | 无 | 多角色**群聊**（轮流发言） | 无 | ❌ 不吸收：群聊上下文爆炸，违背克制哲学 |
| **Manus** | 无显式模式 | 虚拟机内单 agent 多工具 | 有任务回放 | 事件流回放（Kairos run_events 已有） |

**结论**：市面上没有一家把"模式系统"做全。最合理的拼法 = **Claude Code 的 Plan 批准流 + deepagents 的结论隔离子代理 + Muse 的 goals 自动推进 + DeerFlow 的并发上限**。

---

## 2. 设计原则（中道约束）

1. **模式是会话属性，不是全局开关**——不同会话可处于不同模式，互不干扰。
2. **模式只改两件事：系统提示词注入 + 工具白名单**。主循环 `run_turn` 结构零改动（克制）。
3. **子代理无审批权**——写操作审批永远回到主对话/用户，子代理是"没有手的工人"。
4. **一切并行有上限**——并发子代理默认 3（对齐 DeerFlow），防 token 失控。
5. **所有模式转换都是事件**——进/出 plan 模式、派发/回收子代理，全部进 run_events，InfoPane 事件页可见（透明注入原则）。

---

## 3. 模式系统：三种会话模式

```
┌─────────────────────────────────────────────────────────┐
│  session.mode = chat（默认）｜ plan ｜ goal              │
│                                                         │
│  chat：常规对话，全工具可用，写操作按既有审批流           │
│  plan：只读模式——禁用一切写工具（ws_write/run_python/    │
│        remember/save_skill/agent_run…），系统提示词追加   │
│        "你是规划者，产出结构化计划待用户批准"；           │
│        产出落 plans 表，UI 出批准卡片                     │
│  goal：绑定一个 active goal 的推进模式——每轮对话先注入    │
│        目标+进度时间线，产出自动 goal_progress 记账       │
└─────────────────────────────────────────────────────────┘
```

### 3.1 Plan 模式（对标 Claude Code，M7.3）

**进入**：① UI 模式切换器点"规划"；② `/plan` 斜杠命令；③ 主 agent 遇到大型任务可**建议**用户切 plan 模式（只在回复里提示，不自动切——保留人工控制）。

**流程状态机**：

```
plan_draft ──产出计划──▶ pending_review ──用户批准──▶ approved
     ▲                         │                        │
     └────── 用户要求改 ◀──────┘ 拒绝                   ▼
                              (会话转回 chat)    计划内容注入执行上下文
                                               （chat 模式按计划干活）
```

- 计划结构（强约束）：目标 / 拆解步骤（每步标注用哪些工具、动哪些文件）/ 风险与回滚 / 预计步数。
- 批准动作走**既有审批机制**（confirmations 表 + UI 卡片，与通知审批同构）。
- 批准后计划不自动执行——注入上下文由用户发令"按计划开始"，或勾选"批准即开始"。

### 3.2 Goal 模式（增强已有雏形，M7.4）

已有：goals 表、goal_* 四工具、scheduler 定时轮 `_advance_goals()`。
补齐：

1. **goal 明细表** `goal_steps`：每次推进的时间、产出摘要、落盘文件路径——进度从"一个百分比数字"变成**时间线**。
2. **goal 会话绑定**：会话切到 goal 模式时选一个 active goal，每轮先注入「目标+plan+最近 3 步进度」。
3. **推进产出落盘**：每步产出写 `workspace/goals/<goal_id>/step_<n>.md`。
4. **Goal 面板**（UI）：active 目标卡片 + 步骤时间线 + 手动"推进一步"按钮（调 scheduler.run_once(scheduled=True)）。

### 3.3 模式实现的代码落点（改动极小）

| 改动点 | 内容 |
|--------|------|
| db：sessions 表 | 加 `mode TEXT DEFAULT 'chat'`、`goal_id INTEGER` |
| agent.py | `run_turn` 开头按 mode 取 SYSTEM_ADDON + WRITE_TOOLS 黑名单；约 +15 行 |
| api.py | `POST /api/sessions/{id}/mode {mode, goal_id}`；`/plans` CRUD；plan 批准复用 confirmations |
| tools.py | 工具调用入口加一行白名单校验（mode=plan 时写工具直接返回"规划模式下禁用"） |
| 前端 | 顶栏模式切换器（chat/plan/goal 三档，子夜天文台相位点风格）+ plan 批准卡 + goal 面板 |

---

## 4. 多 Agent：从"同步阻塞"到"并行编排"（M7.2）

### 4.1 现状的问题

`agent_run` 是**同步阻塞**的：主对话发 tool_call → 干等子代理跑完 → 拿到结论继续。三个子任务 = 三倍等待，且子代理执行过程对用户完全不可见（黑盒）。

### 4.2 目标形态（对标 Claude Code Task + DeerFlow 并行）

```
主 agent（orchestrator）
  │ 一次 tool_call：agent_run({role, task, parallel:[{role,task},...]})
  ▼
编排器（新 runtime/subagents.py，复用 durable task 状态机）
  ├─ worker① researcher ──┐
  ├─ worker② analyst ─────┼── 并行执行（线程池，max 3）
  └─ worker③ writer ──────┘
  │ 各自 run_turn 独立会话（上下文隔离），事件转主 SSE
  ▼
全部完成 → 聚合结论 → 一次返回主对话（只回结论）
```

### 4.3 关键设计

1. **角色注册表升级**：SUBAGENT_ROLES 从硬编码 dict 变 `subagents` 表——内置 researcher/analyst/writer/reviewer 四角色 + **用户自定义**（名字/职责提示词/工具白名单/max_steps），UI 可管理（对标 Claude Code subagents 配置）。
2. **并行派发**：`agent_run` 增加 `batch` 参数（一次最多 3 个子任务），编排器线程池并行；单个任务仍支持同步模式（简单场景零开销）。
3. **子代理事件流**：子代理的 tool_start/tool_done 以 `subagent.step` 事件转主 SSE——用户能看到"调研员：正在抓 3 个网页"，不再是黑盒（透明原则）。
4. **独立会话落库**：每个子代理跑在隐藏 session（`kind='subagent'`），可回放（复用 run_events），但不出现在会话列表。
5. **上下文防污染**：子代理返回给主对话的**只有结论**（≤2000 字），过程全部留在子会话。主对话可发 `agent_recall(run_id)` 取细节。
6. **并发上限**：全局同时 running 子代理 ≤3（对齐 DeerFlow），超出排队。

### 4.4 代码落点

| 改动点 | 内容 |
|--------|------|
| 新增 runtime/subagents.py | 编排器：线程池 + 队列 + 并发闸 + 事件桥接（~150 行） |
| db：subagents 表 | 角色注册表（内置四角色 seed 进去） |
| tools.py | agent_run 支持 batch；新增 agent_recall、agent_list（查子代理状态） |
| api.py | `/api/sessions/{id}/subagents` 查询子代理运行态 |
| 前端 | 线程内子代理卡（角色头像+实时步数+折叠结论） |

---

## 5. 不做什么（诚实边界）

- ❌ 多角色群聊（AutoGen 式）——上下文爆炸，违背克制哲学。
- ❌ 层级子代理（子代理再生子代理）——一层足够，两层开始失控，Claude Code 也不允许。
- ❌ 子代理自动批准写操作——审批权永远在用户手里。
- ❌ 跨设备分布式执行——单机多线程，多用户沙箱是 Phase B 的事。

---

## 6. 里程碑拆解

| 里程碑 | 内容 | 依赖 |
|--------|------|------|
| **M7.1 模式地基** | sessions.mode/goal_id 字段、mode 切换 API、工具白名单校验、SYSTEM_ADDON | 无 |
| **M7.2 并行子代理** | subagents 表+角色管理、runtime/subagents.py 编排器、batch 派发、subagent.step 事件、agent_recall | M7.1 |
| **M7.3 Plan 模式** | plans 表、plan 状态机、批准卡片、批准后计划注入执行 | M7.1 |
| **M7.4 Goal 增强** | goal_steps 表+时间线、goal 会话绑定、产出落盘、Goal 面板 | M7.1 |
| **M7.5 UI 收口** | 模式切换器、子代理卡、plan 批准卡、goal 面板、子夜天文台风格 | M7.2-4 |

建议顺序：M7.1 → M7.3（plan 是用户感知最强的）→ M7.2 → M7.4 → M7.5。

---

## 7. 待拍板问题

1. **并行上限**：默认 3 个并发子代理，够不够？（DeerFlow 也是 3；开大了 token 烧得快）
2. **Plan 批准后行为**：A. 批准后用户手动发令才执行（更稳） B. 批准即自动执行（更顺）——我倾向 B 但批准卡片上给复选框，默认 A。
3. **内置角色**：researcher / analyst（数据分析）/ writer / reviewer 四个够吗？要不要加 coder（写代码子代理，配合 run_python）？
4. **goal 自动推进频率**：现在是定时轮（默认每天一次巡检）顺带推进，要不要给每个 goal 单独配频率（如"每天两步"/"每小时一步"）？
