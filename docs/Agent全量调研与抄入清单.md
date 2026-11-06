# Agent 全量调研：12+ 项目技术清单与全量抄入映射（2026-10-26）

> 原则：不是挑着抄，是能抄的全抄。每一条都要落到 Kairos 的代码里。

## 一、调研对象（高星/前沿/最新）

| # | 项目 | 星数(约) | 核心技术 | 可抄点 |
|---|---|---|---|---|
| 1 | **Meta Muse**（Meta 官方，2026-09-08 发布） | 闭源标杆 | 每用户独立 Linux VM（systemd-nspawn + eBPF）；Sentinel 审批 agent（唯一网络出网权威）；凭据代理化（agent 只见 surrogate token）；目标制（goal 后台跑，关 App 继续干）；子代理 swarm；Artifacts 富产物；记忆文件可读可编辑+Forget；审批粒度（单次/会话/任务/限时/永久）；活动审计 | 全部（VM 除外，本地版用工作区目录+白名单替代） |
| 2 | **AutoGPT** | 170k+ | 目标分解自主循环、Forge 插件生态 | 目标驱动的多轮自主执行循环 |
| 3 | **LangGraph** | 90k+ | 状态图工作流、checkpointer 断点恢复、HITL checkpoint | 任务状态持久化+断点恢复 |
| 4 | **Open WebUI** | 149k | 多会话、文档 RAG、多模型管理、代码执行 | 会话管理、知识库、模型切换 |
| 5 | **LobeChat** | 60k+ | 分支对话、话题聚类自动命名、插件市场、多模型网关 | 会话/话题、技能广场形态 |
| 6 | **OpenHands**(原OpenDevin) | 70k | Docker 沙箱代码执行、浏览器操作、事件流 | 受控代码执行工具、事件流可视化 |
| 7 | **MetaGPT** | 50k+ | SOP 多角色流水线（PM→架构→工程→QA），产物文档化 | 角色流水线（已有三角色，扩产物落盘） |
| 8 | **CrewAI** | 57k | 角色制 crew、任务分配编排 | 角色名册+任务分配（已有） |
| 9 | **AutoGen/AG2** | 160k | 多 agent 会话协作、代码执行、事件驱动运行时 | agent 间协作对话 |
| 10 | **Dify** | 152k | 可视化编排、RAG pipeline、提示词管理、多模型 | 知识库管道、提示词模板 |
| 11 | **Mem0** | 63k | 记忆层：写时冲突消解、作用域召回 | 已抄（v0.1） |
| 12 | **Browser Use** | 50k+ | 浏览器 accessibility tree 操作、无 JS 注入 | Playwright 渲染抓取（已有 fetch 兜底） |
| 13 | **E2B** | 13k+ | 安全云沙箱跑 AI 生成代码 | 受限 Python 执行（本地版：工作区目录+超时） |
| 14 | **GPT Researcher** | 29k | 自主调研生成带引用报告 | 调研子代理（已有 researcher） |
| 15 | **Composio** | 20k+ | 200+ 服务预置工具集成 | MCP 桥接（已有，同类思路） |

## 二、Muse 功能对照表（能抄的全抄）

| Muse 功能 | Kairos 落地 | 状态 |
|---|---|---|
| Secure VM（隔离开机环境） | 工作区目录 `_safe_path` 防穿越 + 工具白名单（本地版诚实替代，不吹 VM） | ✅ v0.2 |
| Sentinel 审批 agent | HITL 确认队列（propose→approve） | ✅ v0.2 |
| 审批粒度（单次/会话/永久） | confirmations 表 + 授权 scope | 🔧 本轮 |
| 子代理 swarm | researcher/writer/reviewer + agent_run | ✅ v0.4 |
| 目标制（后台长目标） | goals：goal 表 + scheduler 周期推进 | 🔧 本轮(简化为任务) |
| 记忆可读可编辑+Forget | GET /memory + **DELETE /memory/{id}** | 🔧 本轮 |
| Artifacts 富产物 | 工作区简报文件 + 工作区视图全文查看 | ✅ v0.3 |
| 活动审计 | 消息/轨迹/确认 全落库 + 时间戳 | ✅ |
| 多入口（WhatsApp/App） | **MCP 桥接**（微信/飞书/QQ server 即插即用） | ✅ v0.4 |
| Ideas 主动建议 | 定时巡检简报 + HITL 提议 | ✅ v0.2 |
| 凭据代理化 | config.local 独立文件 + gitignore（简化版） | ✅ |

## 三、本轮全量抄入清单（Open WebUI / LobeChat / OpenHands）

| 功能 | 来源 | 落地 |
|---|---|---|
| 多会话管理（新建/切换/删除/自动命名） | Open WebUI + LobeChat | sessions 表 + /chat 带 session_id + 会话切换条 |
| 知识库 RAG（上传文档→切片→检索问答） | Open WebUI / Dify | kb 表 + 上传接口 + kb_search 工具 + 知识库视图 |
| 受控代码执行 | OpenHands / E2B | run_python 工具（工作区沙盒目录 + 10s 超时） |
| 记忆 Forget | Muse | DELETE /memory/{id} + 前端删除按钮 |

## 四、诚实声明（面试口径）

- VM 沙箱没做：本地白名单 + 目录防穿越是简化替代，Muse 级 systemd-nspawn + eBPF 在路线图
- KB 检索是关键词匹配 + SQLite，不是向量库（pgvector 在秋招通侧有，接入在路线图）
- 代码执行无网络隔离（单机自用版），Docker 隔离在路线图
- 会话自动命名用首句截断，非 LLM 总结（省调用；LLM 命名在路线图）
