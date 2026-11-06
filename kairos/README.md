# Kairos · 自进化个人智能体

> 命名：Kairos（希腊语 καιρός）=「恰当的时机」，区别于 Chronos（钟表时间）——主动式 Agent 的隐喻：在正确的时刻做正确的事。（曾用名 EvoAgent，2026-11-01 更名；Python 包名暂仍为 `evogent/`，M1 改造时统一改为 `kairos/`）
> 一句话：不更新模型参数，让 Agent 在真实日常任务里"做错了就改规则，做对了就沉淀技能"，越用越强。
> 定位对标：RSIAgent / Dream-RSI（自进化循环）+ Meta Muse（个人助理形态、沙箱、HITL）。

> **v0.4 更新**：定位升级为**个人全能助理**（秋招是主战场但不是全部）。
> 新增：**MCP 协议桥**（mcp.json 配 server → 工具自动桥接为 mcp_<服务>_<工具>，微信/飞书/QQ 开源实现即插即用，最小 JSON-RPC 2.0 stdio 客户端自研）、
> **多 Agent 子代理**（researcher/writer/reviewer 三角色，deepagents task 模式：派任务只回结论，对话里也能 agent_run 派发）、
> 团队/MCP 两个新视图。20 个内置工具 + MCP 动态扩展。
>
> **v0.3 更新**：多视图控制台前端（对话/控制台/巡检/台账/进化实验室/设置，抄 lobe-chat 交互 + open-webui 工作台模式）、
> SSE 流式对话（/chat/stream，工具执行过程实时可见，抄 open-webui 事件流 + Manus 过程可视化）、
> 计划闭环（create_task → complete_task，抄 deepagents write_todos）、台账/监控源直接 CRUD、历史对话回放。
> 前端保持单文件零构建，一份 index.html 可部署在任何静态服务器。

## 一、为什么做这个项目（真实需求，不是为了 RSI 而 RSI）

秋招期间我每天要：盯十几家公司的校招页有没有更新、把新 JD 整理成结构化摘要、
更新投递台账、面试完写复盘。人肉干三天就崩，交给通用 ChatBot 又有两个问题：

1. **每次从零开始**：ChatBot 不记得"我要的摘要长什么样""我对哪类公司感兴趣"，
   每天重复交待一遍偏好；
2. **质量不稳定**：模型偶尔漏字段、编造不存在的链接、格式漂移，没人兜底检查。

所以这个 Agent 的三个核心机制每个都对应一个真实痛点：

| 痛点 | 机制 | 来自哪 |
|---|---|---|
| 每次从零开始、要重复交待偏好 | **长期记忆**（写时冲突消解，偏好变了自动更新而不是堆两条矛盾记忆） | Mem0 |
| 质量不稳定、偶尔编造 | **Verifier 真实校验 + fail-closed**（链接必须真的能访问、引用必须真的出现在抓取的页面里） | RSIAgent 的防退化思想 |
| 每天重复同类任务 | **RSI 自进化循环**（失败案例生成更难任务 → 通过的经验固化为技能 → 同类任务直接命中技能） | RSIAgent / Dream-RSI |

## 二、架构（v0.2）

| 模块 | 文件 | 职责 |
|---|---|---|
| ReAct 主循环 | evogent/agent.py | LLM tool-calling，最多 14 步；工具轨迹只留摘要（≤1500 字符） |
| 持久化 | evogent/db.py | SQLite 8 张表：messages / memories / skills / tasks / failures / confirmations / sources / applications |
| 长期记忆 | evogent/memory.py | Mem0 式写时冲突消解（ADD/UPDATE/DELETE/NOOP）+ **DeerMem 式写入门控**（personal×durable×confirmed 才入库） |
| 技能库 | evogent/skills.py | 文件式 SKILL.md（deepagents 模式），data/skills/<slug>/SKILL.md，带 use_count |
| 内容巡检 | evogent/watcher.py | sha256 内容摘要 diff 盯页，抓到变化才触发下游 |
| 定时调度 | evogent/scheduler.py | 后台线程每 6h 一轮：check_all → 变化页喂 LLM 出 5 点增量简报 → 写工作区文件 + 提议通知（HITL） |
| RSI 循环 | evogent/rsi.py | Curriculum → Actor → Verifier（带 ground truth 判卷）→ 技能固化 |
| 服务 | evogent/server.py | FastAPI：/chat /rsi/run /memory /skills /watch/* /applications /workspace /confirmations |

```
                 ┌───────────────────────────────────────────────────┐
                 │                    FastAPI 服务                    │
                 │  /chat /rsi/run /watch/* /applications /workspace  │
                 └───────────────┬───────────────────────────────────┘
                                 │
                 ┌───────────────▼────────────────┐      ┌─────────────────────┐
                 │         ReAct 主循环            │      │   scheduler 线程     │
                 │  LLM tool-calling，最多 14 步   │      │   每 6h 巡检一轮     │
                 └──┬────────┬────────┬──────────┘      └──────────┬──────────┘
                    │        │        │                            │
        ┌───────────▼──┐ ┌───▼────┐ ┌─▼──────────┐    ┌──────────▼─────────┐
        │   工具集 19个 │ │长期记忆 │ │  技能库     │    │  watcher 内容巡检   │
        │ fetch/watch  │ │Mem0 式  │ │ SKILL.md   │    │  sha256 diff 盯页   │
        │ app(投递台账)│ │写时冲突  │ │ 经验固化    │    └──────────┬─────────┘
        │ ws(工作区md) │ │+写入门控 │ │ /复用      │               │ 变化页
        │ notify(HITL) │ │(DeerMem)│ └────────────┘    ┌──────────▼─────────┐
        └──────────────┘ └────────┘                    │ 增量简报 → ws 文件  │
                                                       │ + propose HITL     │
                                                       └────────────────────┘

  RSI 循环（/rsi/run）：
    Curriculum（看失败案例，出更难任务）
        → Actor（ReAct 执行，可检索复用技能）
        → Verifier（规则校验：链接真可达、引用真出现、结构完整；LLM judge 拿 ground truth 判卷）
        → 通过：经验固化为 skill / 失败：进 failures 供下轮出题
```

## 三、四大真实工作流（每个对应秋招里的一件真事）

| 工作流 | 真实场景 | 用到的机制 | 验证方式 |
|---|---|---|---|
| **盯校招** | 腾讯/字节校招页是 JS 渲染 SPA，人肉刷页确认有没有新岗位 | watcher sha256 diff + Playwright(msedge) 渲染抓取 + 调度器定时巡检 | `/watch/run` 篡改页面对象 → `changed: 1` → 5 点增量简报落盘 |
| **投递台账** | 投了 30+ 家，Excel 手记容易漏状态 | app_add/app_update/app_stats 结构化台账，SQLite 持久化 | `/chat` 一句话"记一下投了腾讯 AI 应用开发"→ 台账 + 统计一条龙 |
| **面试复盘沉淀** | 每次面试被问倒的题，复盘一次就忘 | 对话提取经验 → Verifier 通过后固化为 SKILL.md → 下次同类任务直接命中 | `/rsi/run` 对 join.qq.com 一轮通过，技能落库复用 |
| **主动简报** | 不想主动打开系统，希望它自己来汇报 | scheduler 每 6h → 简报写 `workspace/简报/` + propose_notification 进 HITL 确认队列 | 确认队列落库，人工批准才真正发通知 |

## 四、跑起来

```bash
pip install -r requirements.txt
# 如需抓取 JS 渲染的招聘页（腾讯/字节等 SPA），再装 playwright 并用系统 Edge：
pip install playwright        # 无需下载浏览器内核，直接用 channel="msedge"
cp config.example.json config.local.json   # 填你的 LLM key（OpenAI 兼容）
python run.py                              # 默认 http://127.0.0.1:8626
```

- 对话：打开首页输入"帮我总结 https://xxx 的内容" 之类
- 自进化：`POST /rsi/run {"task_family": "总结一个公司校招页", "rounds": 3}`
- 高危动作（发通知）：Agent 只能"提议"，进 `/confirmations` 队列，人工批准才执行

实测：对 join.qq.com（腾讯校招，JS 渲染 SPA）一轮 RSI 通过并固化技能；
换静态页则 requests 直抓即可。

## 五、从前沿项目抄了什么（调研对照）

> 完整调研见 `../docs/OpenMuse调研.md`、`../docs/RSI与Harness调研.md`、`../docs/前沿动态调研-2026Q4.md`（含源码路径级拆解）；桌面端改造规划见 `../docs/Kairos桌面端需求文档.md`。

| 来源 | 抄了什么 | 怎么用的 |
|---|---|---|
| RSIAgent（自进化） | Curriculum→Actor→Verifier 三角色分离；**记忆只能被 Verifier 结果驱动更新**（防退化纪律） | rsi.py 三个角色是三次独立 LLM 调用；技能只在 Verifier 通过后固化，失败只进 failures 供下轮出题 |
| RSIAgent（防编造） | Verifier 必须 hold ground truth，不凭直觉判对错 | judge 的输入里强制带上 Actor 实际抓取的页面原文，"回答中的事实必须能在 ground truth 找到出处" |
| OpenMuse（Muse 平替） | 高危动作 HITL 提议-确认模式；浏览器渲染抓取 | propose_notification 只进确认队列，批准才执行；fetch 用 requests + Playwright(msedge) 兜底渲染 JS 页面 |
| deepagents（LangChain harness） | 技能=名字+正文的结构（类 SKILL.md 渐进披露）；子代理只回结论 | skills 表 (name, content, use_count)；工具轨迹只留摘要 |
| DeerMem（2026Q4 前沿） | 记忆写入门控：只存 personal × durable × confirmed 的事实，防记忆库被一次性信息污染 | memory.py `_EXTRACT_SYS` 三重门控，不满足门槛的对话内容直接不落库 |
| OpenMuse（worker 租约） | 多 worker 抢任务用 SQL 租约防重复执行 | 本项目单机单线程调度暂不需要，记入路线图（多用户扩展时用） |
| OpenMuse（提案-哈希 HITL） | 高危动作"提案 + 内容哈希"防 TOCTOU：批准时执行的和提议时看到的是同一份 | notify 提议进 confirmations 表带完整内容快照，路线图：批准时校验哈希一致 |
| Hermes / 助手主动式（2026Q4 趋势） | 助理从"被动应答"转向"定时主动汇报" | scheduler 简报即此模式的最小实现；nudge 打断重排记入路线图 |

## 六、诚实声明（面试可能被追问的点）

- v0 的沙箱是"受控工具白名单"级别（fetch 只出不进、命令不执行、工作区 `_safe_path` 防目录穿越），
  不是 Muse 级别的每用户一台 VM；沙箱强化在路线图里。
- RSI 循环是迷你诚实版：Curriculum/Actor/Verifier 三角色是真实存在的独立 LLM 调用，
  技能固化是真实落库复用；没有做的事（reward model、分布式探索）README 不吹。
- 长任务/评测等能力在实习项目里有落地，本项目的差异化在记忆与自进化，刻意不重复造。
- v0.2 的调度器是单机 SQLite 单线程版：6h 巡检够用，但多用户/分布式要换成
  SQL 租约 + 消息队列（记入路线图，不假装已经做了）。
- 记忆写入门控（DeerMem 式）目前是提示词级约束，不是结构化校验器；诚实说这是
  "工程上够用的第一版"。

## 七、M5 发布口径诚实声明（2026-11-01）

- **自动更新**：`electron-updater` 脚手架已接入（发布态动态加载 + GitHub Releases 源），但当前安装环境无法拉包（npm 进程被系统终止），依赖缺失时**优雅降级为托盘"检查更新"跳转 Releases 页**——不假装更新器已可用。
- **崩溃上报**：crashReporter 只做**本地转储**（userData/logs 可诊断），无接收服务器；遥测开关（telemetry.json，默认关）就绪但上传恒关——零遥测是现状，不是宣传语。
- **签名/公证**：Windows NSIS / macOS DMG 产物**未签名未公证**（无证书），SmartScreen/Gatekeeper 会告警；CI（.github/workflows/release.yml）已预留证书 secret 槽位，配了即签。
- **CI**：三平台矩阵构建（windows/macos/ubuntu）+ tag 触发发布，backend 走 PyInstaller onedir（requirements.txt 已补齐真实依赖清单）。
- **压缩阶梯**：tier1-5 全通（tier4 结构化保留清单+注入防护+原文落盘归档；tier5 机械裁剪无 LLM 依赖）；tier4/5 删除消息不可撤销但原文永久归档在 workspace/_compact/。
