# Kairos · 自进化个人智能体

> Kairos（希腊语 καιρός）=「恰当的时机」，区别于 Chronos（钟表时间）——主动式 Agent 的隐喻：在正确的时刻做正确的事。
> 一句话：**不更新模型参数，让 Agent 在真实日常任务里"做错了就改规则，做对了就沉淀技能"，越用越强。**
> 定位对标：RSIAgent / Dream-RSI（自进化循环）+ Meta Muse（个人助理形态、沙箱、HITL）。

## 这是什么

一个完整落地的个人 AI Agent 系统，包含四个部分：

| 目录 | 组件 | 技术栈 |
|---|---|---|
| `kairos/` | 后端（Agent 运行时 + HTTP API） | Python · FastAPI · SQLite · SQLAlchemy · Alembic |
| `web/` | 多视图控制台前端 | Vue3 · Vite · Pinia |
| `desktop/` | 桌面壳（后端生命周期管理 + 托盘 + 桌宠） | Electron · TypeScript |
| `cloud/` | 云端长程 Agent（Durable Objects 事件源） | Cloudflare Workers · DO · Cron Triggers |

## 核心机制（每个都对应真实痛点）

| 痛点 | 机制 | 思路来源 |
|---|---|---|
| 每次从零开始、重复交待偏好 | **长期记忆**：写时冲突消解（偏好变了自动更新，不堆矛盾记忆）+ 写入门控 | Mem0 + DeerMem |
| 质量不稳定、偶尔编造 | **Verifier 真实校验 + fail-closed**：链接必须真的能访问、引用必须真的出现在抓取页面里 | RSIAgent 防退化思想 |
| 每天重复同类任务 | **RSI 自进化循环**：失败案例生成更难任务 → 通过经验固化为技能 → 同类任务直接命中 | RSIAgent / Dream-RSI |

其他能力：MCP 协议桥（mcp.json 即插即用，工具桥接为 `mcp_<服务>_<工具>`）、多 Agent 子代理（researcher/writer/reviewer）、SSE 流式对话（工具执行过程实时可见）、内容巡检（sha256 diff 盯页）、定时调度 + HITL 审批（待确认项先批准再执行）、多会话/项目分组、桌面桌宠状态伴侣（idle/working/waiting/ready/failed 五态联动后端）。

## 快速开始

```bash
# 1) 后端（Python 3.11+）
cd kairos
pip install -r requirements.txt
copy config.example.json config.local.json   # 填入你的 LLM API key（OpenAI 兼容）
python run.py                                # 默认 http://127.0.0.1:8626

# 2) 控制台前端
cd ../web
npm install
npm run dev

# 3) 桌面端（可选，Electron 会自动拉起后端）
cd ../desktop
npm install
npm run dev
```

MCP server、飞书遥控通道等在设置页或 `config.local.json` / `mcp.example.json` 里配置。

## 目录速览

```
kairos/
  kairos/            # Agent 核心：agent.py(ReAct 主循环) / memory.py / skills.py / rsi.py
                     #             watcher.py / scheduler.py / server.py / hub.py(多引擎) / mcp_client.py
  alembic/           # 数据库迁移
  scripts/           # 构建/测试/桥接脚本
web/                 # Vue3 控制台
desktop/electron/    # Electron 主进程：sidecar 生命周期、托盘、桌宠窗口、自动更新
cloud/worker/        # Cloudflare Worker：AgentDO + 定时巡检
docs/                # 设计文档与需求文档
```

## 免责声明 / 脱敏说明

- 本仓库为**脱敏开源版**：所有真实姓名、手机号、邮箱、密钥均已替换为示例值或占位符；运行数据（`data/`、`*.db`、`config.local.json`、`mcp.json`）不入库。
- 首次运行前请自行创建 `config.local.json`（参考 `config.example.json`），API key 只存本地。
- 本项目为个人学习与效率工具，Agent 产出的内容请自行核验（系统内建 fail-closed 校验，但不构成任何担保）。

## License

[MIT](./LICENSE) © 2026 SuWen799
