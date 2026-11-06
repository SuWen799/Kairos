# Kairos 超级智能体改造设计（基于 DeepSeek dsh-superagent）

> 日期：2026-11-02
> 上游参考：deepseek-ai/dsh-superagent-prototype（2026-10-31 开源，MIT，8.3k★，源码已存 `参考项目/dsh-superagent-repo/`）
> 关联文档：《Kairos桌面端设计文档》第 11 章（runtime/tasks）、《Kairos模式系统与多Agent设计》、《Agent全量调研与抄入清单》
> 状态：待拍板。批准后按里程碑实施，实现偏离要回写本文档。

---

## 0. 一句话

把 DeepSeek Super-agent 的「**事件驱动 + 工单制 + 授权边界 + 注意力经济**」四件套移植进 Kairos：让 Kairos 从"你问它答的聊天框"升级为"**持续在线、主动接活、关掉 App 照样干、只在需要你时打扰你**的个人智能体"。

## 1. dsh-superagent 是什么（源码级摘要）

规模：src 92 个 TS 文件 12,390 行 + 59 个测试文件 + 9 篇设计文档。不是新产品，是**架构研究原型**。

**核心数据流**：

```
Source(事件源) → Trigger(不可变事件) → Thread(时序线程)
    → Eval(评估: gate 准入 + assessment 研判) → Decision(accept/stop/ignore/unavailable)
    → Ticket(工单)+Mandate(授权) → Assignment(派工) → Executor Session(独立会话+独立目录)
    → Report(progress/waiting/ready/blocked/failed/dropped) → Channel(回写原对话)
```

**五大设计支柱**（对应官方五模块）：

| 支柱 | 内核 | Kairos 对应现状 |
|---|---|---|
| 双层架构 | 主 Session 只评估+派工（Router），Executor Session 独立会话独立目录干活；Router 直通省开销、批处理扛突发 | ❌ 单 Session 包打天下（agent.run_turn 一个循环） |
| 事件驱动 | Source 抽象：GitHub/飞书都是插件；维护读取位置防重放；Trigger 归入 Thread | 🟡 有 watcher（盯页/巡检）但是定时任务，不是事件源抽象 |
| 异步多信道 | Subscription 订阅扇出：后续消息自动送达在办工单，无需再 @；ended 工单被"人的消息"复活 | ❌ 无 |
| 存算分离 | 云端 Loop 与本地执行环境分离，设备休眠不掉线 | ❌ 任务跟着 sidecar 进程死 |
| 外部 Watchdog | 看门狗自愈、会话/上下文由系统层接管 | 🟡 Electron 有 watchdog 计划，未覆盖任务层 |

**最值得抄的四个机制**（含源码位置）：

1. **Ticket 状态机独立于执行会话**（`src/core/tickets.ts`）：工单 open→ended→被人消息复活，Executor 挂了工单还在；七态报告（等待执行/工作中/等外部事件/等人工回答/等审查/受阻/失败）。
2. **Eval 两层评估**（`src/core/evals.ts`）：gate（确定性准入：谁有权派活、白名单、强度 explicit/inferred）+ assessment（模型判断该不该接）；并发作 merge 进下一批，省 token。
3. **Mandate 不可变授权**（docs/002）：授权=Policy 版本+范围+强度+匹配规则；模型只能判断"怎么干"，**不能自我扩权**；工单恢复前按当前 Policy 重验。
4. **主动接纳三档**：off / **observe（默认）** / auto——先观察 Agent 判断对不对，再逐步放开自动执行。这与 Kairos 权限三档（semi/auto/高危强制人工）哲学同构，可直接融合。

**注意力经济原则**（docs/000）：只在「结果已可用 / 需要人决策 / 任务无法继续」时打扰用户，其余一切状态靠面板自查。人工消息/状态查询预留专用执行槽位，防止普通任务占满资源让人插不进手。

## 2. 概念映射表（superagent → Kairos 落地）

| superagent 概念 | Kairos 落地 | 复用/新建 |
|---|---|---|
| Source（事件源） | `sources` 表已有（盯页 URL）→ 升级为统一 Source 接口：web 巡检 / cron 定时 / IM 消息(飞书·微信) / 本地文件 | 改造 watcher + scheduler |
| Trigger | `trigger` 新表：不可变事件 + 来源元数据 + 幂等 ID（去重） | 新建 |
| Thread | 复用 `sessions`（一个会话线程即一个 Thread）+ 订阅关系列 | 扩展 |
| Eval（gate+assessment） | gate=规则引擎（白名单/关键词/频率限制，纯代码）；assessment=LLM 判该不该接（用 DeepSeek Flash 省钱） | 新建 `agent/evals.py` |
| Decision | accept→创建工单；ignore→记原因；stop→停工单 | 新建 |
| **Ticket** | `tickets` 表：七态（queued/working/waiting_event/waiting_human/review/blocked/failed）+ 订阅 + 输入流 | 新建（替换空置 tasks 表的定位） |
| **Mandate** | 工单授权：来源+范围+强度+**计划送审记录**（plan 模式的批准即 Mandate 授予！） | 融合 confirmations |
| Assignment | 派工记录：工单 × 执行器（内置 agent / Hub 外挂 CLI claude·codex·codebuddy） | 新建，接 Agent Hub |
| Executor Session | 每工单独立会话 + 独立工作目录（workspace/<ticket>/） | 复用 agent.run_turn + subagents |
| Report | 工单状态事件 → run_events 已有基建 | 复用 |
| Panel（待人工面板） | 右栏「动态」页升级：待人工置顶、其余按状态收敛、通知去重 | 改 InfoPane |
| Watchdog | sidecar 任务层看门狗：租约过期重认领 + 崩溃恢复 | 融合 OpenMuse 租约 worker 设计 |

## 3. 分阶段实施（按价值排序，每阶段独立可用）

### M-S1 工单制内核（最高优先级）
- 新表 `triggers / tickets / mandates / assignments`（migration 启动 ALTER 风格沿用）
- `agent/evals.py`：gate 规则引擎 + LLM assessment（Flash 模型，批处理合并触发）
- 工单七态流转 + 独立工作目录；会话内 plan 批准 = Mandate 授予（打通现有 plan 流）
- 验收：在对话里说"盯一下腾讯校招官网，有更新就整理成简报"→ 自动开单 → 执行 → 完成后工单变 ready，全过程右栏可见

### M-S2 事件源统一 + 主动接活
- watcher/巡检改造为 Source 插件（web/cron/文件三个内置 kind），维护读取位置防重放
- 主动接纳三档 off/observe/auto 融合进权限设置（默认 observe：只记录"我发现了这个活"，不真干，用户点一下才开单）
- 验收：官网变化自动出现在「动态」待评估列表，一键转工单

### M-S3 持久执行 + Watchdog
- OpenMuse 式租约 worker（CAS 认领 + 心跳续租 + guard/checkpoint），**关 App 任务照跑**
- 崩溃恢复：sidecar 重启后扫描租约过期工单重新认领
- 验收：跑一个 5 分钟任务中途杀掉 sidecar，重启后自动续跑

### M-S4 信道回写 + IM 入口
- 工单结果回写原对话（Channel）；飞书/微信 Source 接入（MCP 桥已有地基）
- 注意力规则：只推 waiting_human/ready/blocked，通知去重 key
- 验收：在飞书里给 Bot 发"帮我查XX"→ 开单 → 干完回帖

## 4. 面试口径（可讲性）

- "我参考了 DeepSeek 10 月底开源的 Super-agent 架构，把**任务作为系统组织单位**而不是会话；借鉴了它的 Eval 两层评估（确定性 gate + 模型研判）和不可变 Mandate 授权——模型不能自我扩权，这条是 Agent 安全的核心。"
- 数据点：官方实验 P50 12.4min / P95 15.7min vs 单会话 29.5min 长尾；Router 批处理把单决定推理 6.4s→1.6s。
- 诚实声明：superagent 是研究原型（官方明示非生产级），Executor 以部署者身份跑命令无完整隔离；Kairos 只取其领域模型，沙箱沿用 Kairos Job Object 方案。

## 5. 诚实边界

- 不抄它的 TypeScript 实现（我们是 Python），只抄**领域模型与机制**；
- 双层 Router/Executor 的"存算分离"（云端 loop + 本地环境）本地单机版无意义，退化为"sidecar 常驻 + 租约保护"；
- 它的 GitHub/飞书插件面向团队协作场景，Kairos 先做个人场景（web 盯梢/cron/本地文件），IM 源放 M-S4。
