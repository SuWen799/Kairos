# Kairos 云上长程 Agent 部署设计（superagent 架构 × pi 框架 × Cloudflare）

> 日期：2026-11-03
> 上游：《Kairos超级智能体改造设计.md》（领域模型篇）、《Kairos桌面端设计文档》
> 参考：deepseek-ai/dsh-superagent-prototype（源码已存）、earendil-works/pi（pi-mono，110k★，MIT）
> 状态：待拍板。

---

## 0. 一句话

**用 pi 的干净内核 + Cloudflare Durable Objects 承载 dsh-superagent 的领域模型**，把 Kairos 的"大脑stem"（事件源/评估/工单）搬上云端 7×24 常驻，本机 Kairos 降级为"执行器 + 面板"——电脑关机，Agent 照样盯校招、接活、干完等你验收。这正是 dsh-superagent「存算分离」方向的落地版。

## 1. 框架选型：pi，不是 DSH（明确结论）

| 维度 | DSH（DeepSeek Harness） | **pi（选定）** |
|---|---|---|
| 运行形态 | 本地常驻 daemon：tmux + gh CLI + lark-cli + 本地状态目录锁 + danger-full-access OS 用户 | 纯 TS 库，无 daemon 假设；SDK 模式被 OpenClaw 验证可嵌入 |
| Workers 适配 | Cordis 内核期待长活 Node 进程 + 文件系统，移植=对抗框架设计 | pi-ai（20+ 提供商统一 API）+ pi-agent-core（工具循环/状态）都是库级代码，fetch 即用 |
| 部署文档口径 | 官方部署指南全是本机操作，云上无路径 | 无平台绑定 |
| 与 Kairos 关系 | 领域模型已提取（上一篇文档），框架本身只服务本地场景 | **Hub 里 pi 本就是注册执行器**，技术栈连续 |
| 成熟度 | 研究原型（官方自述非生产级） | 110k★、OpenClaw/MiniMax 生产在用 |

**结论：DSH 只借领域模型（已完成），运行内核选 pi。**

## 2. Cloudflare 组件映射（superagent 概念 → CF 原语）

| superagent 概念 | Cloudflare 原语 | 说明 |
|---|---|---|
| Engine + Tickets/Policy/存储 | **Durable Object（每用户一个 Agent DO）+ 内置 SQLite** | triggers/tickets/mandates/decisions 全落 DO SQLite；强一致、单点串行，天然匹配 Engine"唯一写者"设计 |
| Passes（每 2 秒巡一遍） | **DO Alarms**（闹钟唤醒） | 休眠零成本，醒来处理到期 Eval/工单，比常驻轮询便宜得多 |
| Source：web 巡检/cron | **Cron Triggers** → 打 DO | 定时盯校招官网、投递进度 |
| Source：GitHub/IM | **Webhooks + Email Workers + 飞书/微信回调** → DO | 事件驱动接活 |
| Eval（gate+assessment） | DO 内 gate 纯代码 + assessment 调 **DeepSeek API**（fetch，Flash 档省钱） | 批处理合并触发照抄 |
| Executor（要文件系统/bash 的活） | 三档：①**Workflows**（持久步骤、自动重试）跑纯 API 型任务 ②**Sandboxes/Containers** 跑要 bash/文件的任务 ③**中继到本机 Kairos sidecar**（WebSocket 出站连接，无需公网 IP） | 本机 executor = Kairos 现有 agent/Hub 外挂 CLI |
| Watchdog 自愈 | DO 重启恢复 + Workflows 步骤重试 + 租约（OpenMuse 式 CAS）| 平台自带大半 |
| Panel（待人工面板） | DO WebSocket Hibernation + 静态面板（Workers Assets） | Kairos 桌面端直连同一 DO，手机浏览器也能看 |
| 长会话上下文 | 会话 JSONL → DO SQLite/R2；压缩沿用 Kairos 阶梯 | pi 会话格式可借鉴 |

## 3. 架构图

```
                 ┌─────────────── Cloudflare（7×24 大脑stem）───────────────┐
 校招官网 ◀─Cron─┤  Agent DO（每用户）                Workflows / Sandboxes  │
 GitHub ─Webhook─▶  ├ Sources 收 Trigger                      ▲           │
 飞书/微信 ─回调──▶  ├ Eval: gate(代码)+assessment(DeepSeek)    │ 派工       │
 邮件 ─Email Wk─▶  ├ Tickets/Mandates（SQLite）───────────────┘           │
                 │  └ Alarms 自愈/到期唤醒                中继通道 ▼        │
                 └──────────────────────────────────────────┬─────────────┘
                                        WebSocket 出站（无需公网IP）
                                                            ▼
                              ┌──────────── 本机 Kairos（执行器+面板）────────────┐
                              │ sidecar Executor：agent 循环 / Hub 外挂 CLI       │
                              │ 工作区文件 / OCR / 浏览器 / 本地凭据               │
                              └──────────────────────────────────────────────────┘
```

## 4. 分阶段实施

| 阶段 | 内容 | 验收 |
|---|---|---|
| C-S1 云上骨架 | Worker + Agent DO + SQLite 建表（triggers/tickets/mandates）+ Cron 盯一个网页 + 极简面板 | 网页变化 → DO 开单 → 面板看到待评估 |
| C-S2 Eval + 通知 | gate 规则 + DeepSeek assessment + observe 档 + 飞书/微信通知（只在 waiting_human/ready 打扰） | 它主动说"发现腾讯校招更新了，要整理吗" |
| C-S3 执行器接入 | Workflows 跑 API 型任务；本机 sidecar 出站 WebSocket 注册为 Executor，租约心跳 | 关机测试：任务挂起，开机续跑 |
| C-S4 面板合体 | Kairos 桌面端右栏「动态」直连云端 DO（同一套工单数据）；本地/云端双执行器可配 | 桌面端与手机浏览器看到同一份工单状态 |

## 5. 诚实边界（面试口径）

- Workers CPU 限制（默认 30s/可配 5min）：**长任务靠 Workflows 分步持久执行，不是单进程死扛**——这本身就是考点；
- workers.dev 域名国内可达性一般，绑定自定义域解决；
- DeepSeek API 从 CF 出网没问题，但凭据只能放 Workers Secrets（正好呼应 Mandate"凭据代持"）；
- 本机 Executor 不在线时任务挂起不丢失（租约过期重新调度）——诚实告知"关机=延迟，不是失败"；
- DSH 框架本体不上云：它是本地 daemon 设计，硬搬是反模式；**借它的领域模型，用 pi 的内核，租 Cloudflare 的机房**——三家公司各取所长。

## 6. 成本估算（个人档）

Workers Paid $5/月起；DO + Workflows + Cron 个人量级约 $5-10/月；DeepSeek token 按现有用量。合计 ≈ 一顿外卖钱，换 7×24 常驻。
