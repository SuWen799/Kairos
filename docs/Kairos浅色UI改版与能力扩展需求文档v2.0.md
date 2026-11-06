# Kairos 浅色 UI 改版 + 渲染与能力扩展需求文档 v2.0

> 日期：2026-11-02
> 性质：需求 + 设计合并文档（用户审完再开发）
> 上游：《Kairos桌面端需求文档》v1.5、《Kairos桌面端设计文档》v1.0（M1-M6 已交付）、《Kairos模式系统与多Agent设计》（M7，待拍板——本文档将其并入为 R3/R7）
> 触发：用户 2026-11-02 反馈——① UI 棕黑配色丑，要求换成正常白/浅蓝浅色风格（对齐 Codex / WorkBuddy）；② AI 回复的 Markdown 源码（`**`、`-`、`##`）没有渲染直接裸露；③ 需要模式系统 / 多模型路由 / 权限分级 / 沙箱 / 多 Agent 等能力开关。

---

## 1. 现状盘点（证据）

| # | 现状 | 证据 | 问题 |
|---|------|------|------|
| 1 | 深暖墨棕主题 | `web/src/styles.css` 星芒 v3 色板（#171110 底 / #E87762 橙） | 用户明确否决：要白/浅蓝 |
| 2 | Markdown 不渲染 | `ThreadPane.vue:70` `{{ m.content }}` 纯文本插值；2026-11-02 09:08 对话实测，`**盯校招**` 等源码裸露 | 必修 |
| 3 | 单模型 | config.py `MODELS` 槽位已预留但默认只配 `deepseek-chat`；llm.py `chat(model=)` 已支持运行时切换 | 地基在，缺配置+UI+路由 |
| 4 | 无模式系统 | sessions 表无 mode 字段；agent.py 单一 SYSTEM_TMPL | M7 文档已设计未实施 |
| 5 | 子代理同步阻塞 | `tools.py:128 tool_agent_run` 干等子代理跑完；固定 3 角色（researcher/writer/reviewer）硬编码 | M7.2 已设计未实施 |
| 6 | 轻沙箱 | `tool_run_python`：子进程 + 10s 超时 + 沙盒 cwd，无禁网、无 Job Object、无文件系统隔离 | 需分级 |
| 7 | 权限单一 | 风险分级（高→confirmations 审批）已有，但用户无法选"全自动/每步问"档位 | 需分档 |
| 8 | CORS 端口写死 | 已修（allow_origin_regex）——本次排查副产品，已生效 | ✅ 已闭环 |

---

## 2. 需求总表（R1-R7，按用户感知排序）

| 编号 | 需求 | 优先级 | 依赖 |
|------|------|--------|------|
| R1 | 浅色主题改版（白/浅蓝，Codex 风） | P0 | 无 |
| R2 | Markdown 渲染（含代码高亮、流式安全） | P0 | 无 |
| R3 | 模式系统：chat / plan / goal | P1 | 无 |
| R4 | 多模型：多模型配置 + 会话级切换器 + 任务路由 | P1 | 无 |
| R5 | 权限分级：普通 / 高级（审批三档） | P1 | 无 |
| R6 | 沙箱分级：基础 / 严格 | P2 | 无 |
| R7 | 多 Agent：并行编排 + 设置开关 | P2 | R3（复用模式地基） |

建议实施顺序：**R2 → R1 → R3 → R4 → R5 → R6 → R7**（R2 最小最痛先修；R1 纯前端；R3 是 R7 的地基；R4/R5 相互独立穿插）。

---

## 3. R1 浅色主题改版（P0）

### 3.1 设计方向

对齐 Codex 桌面版 / WorkBuddy 的浅色仪器风：**纯白底 + 浅蓝灰面板 + 一点蓝**。不做暗色模式（用户就要浅色），但保留 CSS 变量结构，将来加暗色只换变量。

### 3.2 浅色色板（v4 白昼版，替换 styles.css 全部变量）

| 角色 | 色值 | 用途 |
|------|------|------|
| Background | `#FFFFFF` | 主区底色 |
| Panel | `#F6F8FB` | 侧栏 / 输入区 / 面板 |
| 浮起 / 悬停 | `#FFFFFF` / `#EEF3F9` | 卡片浮起 / hover |
| 边框 | `#E3E9F1` / `#D0DAE6` | line / line-hi |
| Text | `#1B2733` | 正文（近黑蓝） |
| 次级文字 | `#5B6B80` / `#8B98A9` | tx2 / tx3 |
| Primary | `#3E7BFA` | 主动作按钮 / 进度 / 用户消息竖线（浅蓝） |
| Accent | `#2F6FE4` | 链接 / 焦点态 |
| 品牌渐变 | `linear-gradient(90deg,#3E7BFA,#6AA6FF)` | 主按钮（白底上淡蓝渐变） |
| ok / err | `#189A4A` / `#D5484A` | 成功 / 错误 |
| 代码块底 | `#F3F6FA` | md 代码背景 |

同步硬编码点：`desktop/electron/main.ts` `backgroundColor: '#171110'` → `#FFFFFF`；`titleBarOverlay` color/symbolColor 换白底深字；PWA `manifest.webmanifest` theme_color/background_color；托盘/启动页 logo 若带深底需换白底版（用现有单色反白版的反向）。

### 3.3 改动范围

只动 `styles.css` 变量层 + 上述 4 处硬编码 + 个别组件内写死的 rgba 深色（`App.vue` scrim、`main.ts` overlay）。组件布局、星芒铭牌、相位点语义全部保留。预计纯前端半天量。

### 3.4 验收

- Electron 冷启动白屏无闪黑（backgroundColor 已同步）。
- 三栏 / 启动页 / 移动抽屉 / PWA 登录门全浅色无残留深色块。
- 用户消息右栏批注、kairos 文档流、工具时间线在白底下对比度达标（正文 ≥ 4.5:1）。

---

## 4. R2 Markdown 渲染（P0）

### 4.1 方案

- 选型：`markdown-it`（稳、快、生态熟）+ `highlight.js`（代码高亮）。XSS：`html: false`，链接统一 `target=_blank rel=noopener`。
- 落点：`ThreadPane.vue` 的 `.doc-body` 从 `{{ m.content }}` 改为 `<div v-html="renderMd(m.content)">`；新增 `web/src/lib/md.js` 封装（单例 markdown-it 实例 + hljs 配置 + 安全钩子）。
- 流式安全：`message.delta` 期间每次增量对当前 content 重跑 markdown-it（几百字成本可忽略）；尾部未闭合的 ``` 代码块在渲染前临时补闭合，防止流式中整段闪烁。

### 4.2 渲染范围

标题 / 粗斜体 / 列表 / 表格 / 引用 / 行内代码 / 代码块（带语言标签+高亮）/ 链接 / 分隔线。用户消息仍纯文本（批注样式）。

### 4.3 验收

- 用 2026-11-02 09:08 会话回放：`**盯校招**` 渲染为粗体、列表成列表。
- 流式输出中途无闪烁、无 HTML 注入执行（`<script>` 以文本显示）。
- 代码块高亮 + 一键复制按钮。

---

## 5. R3 模式系统：chat / plan / goal（P1）

> 全部设计沿用《Kairos模式系统与多Agent设计》第 3 章，此处只列本文档新增/拍板项，不重复。

### 5.1 拍板采纳（对 M7 文档待拍板问题的回复建议）

| 问题 | 建议 |
|------|------|
| 并行子代理上限 | 维持默认 3，settings 可调 1-6 |
| Plan 批准后行为 | B 案：批准卡片带复选框"批准即开始"，默认不勾（=A 案行为） |
| 内置角色 | 四角色 + coder，共 5 个 |
| goal 推进频率 | goal 表加 `cadence` 字段，默认每天，可按 goal 覆盖 |

### 5.2 UI 落点（浅色版）

- 输入框左下模式切换器三档 chip：`对话` / `规划` / `目标`（当前档高亮浅蓝）。
- Plan 批准卡：计划结构（目标/步骤/风险回滚/预计步数）+ 批准/拒绝按钮 + "批准即开始"复选框。
- Goal 面板进右栏：active 目标卡 + 步骤时间线 + "推进一步"按钮。

### 5.3 验收

- plan 模式下 LLM 调用写工具被服务端拒绝并回提示（不是靠提示词自觉）。
- 计划批准落 confirmations 表留痕；goal 每步推进落 goal_steps 且可回放。

---

## 6. R4 多模型：配置 + 切换器 + 路由（P1）

### 6.1 设计

```
config.local.json
"models": [
  {"name": "deepseek-chat",  "base_url": ..., "api_key": ..., "label": "DeepSeek V4", "tier": "default"},
  {"name": "deepseek-reasoner", ..., "label": "DeepSeek R", "tier": "reasoning"},
  {"name": "longcat-2.0",    ..., "label": "LongCat",     "tier": "fast"}
]

路由规则（M1 期只做①②，②③为自动路由预留）：
① 会话级手动切换：composer 模型下拉，写 sessions.model
② 工具级路由：deep_research 类重任务走 reasoning 档（tools.py 声明 preferred_tier）
③ 上下文级自动路由（预留）：compaction 摘要走 fast 档省钱
```

### 6.2 改动点

| 落点 | 内容 |
|------|------|
| config.py | MODELS 解析为列表（已预留），每模型独立 OpenAI client（懒建） |
| llm.py | `chat(model_name=)` 按名字查 client+model；usage 记账带 model |
| agent.py | run_turn 读 session.model 传给 llm；subagent 按 tier 取模型 |
| api.py | `GET /api/models`；sessions PATCH 支持 model |
| 前端 | composer 右下模型 chip 下拉；模型名显示在 doc-head |

### 6.3 验收

- 切模型后新 run 的 runs.model 正确落库；多模型各自 api_key 隔离；无 key 的模型置灰。

---

## 7. R5 权限分级：普通 / 高级（P1）

### 7.1 三档审批（对齐 Codex approve 三档 + Claude Code 权限三档）

| 档位 | 行为 | 落点 |
|------|------|------|
| 普通权限（默认） | 写工具逐个审批（现状 confirmations 流） | 不变 |
| 高级权限·半自动 | 同会话内同类低危写操作批准一次后免审（scope=session，已有 expires/scope 字段直接用） | confirmations scope |
| 高级权限·全自动 | 全部写操作免审批，但每次执行加强审计（audit 风险标记）+ 预算熔断照常 | session.autonomy 字段 |

### 7.2 约束（硬边界）

- 全自动档**仅限单会话**可开，不给全局开关；InfoPane 事件页常驻"全自动运行中"横幅。
- 高危工具（删除文件、对外发送）即使全自动也强制审批——高危清单硬编码，档位不能豁免。
- 档位切换写 audit_events（谁何时放的权）。

### 7.3 UI

设置页权限三选一 + composer 旁当前档位小标（`审批` / `半自动` / `自动`）。

---

## 8. R6 沙箱分级：基础 / 严格（P2）

| 档位 | run_python 行为 |
|------|----------------|
| 基础（现状） | 子进程 + 10s 超时 + 沙盒 cwd |
| 严格（新增） | Windows Job Object 限制子进程树（防 fork 炸弹）+ 网络禁用（QoS/防火墙规则或 sockets 拦截）+ 内存上限 + workspace 外路径拒绝 |

设置页"沙箱：基础 / 严格"二选一，`/api/meta` 回显当前档（诚实原则）。严格档实施按 Windows Job Object 优先（pywin32 或 ctypes），失败降级基础档并审计留痕。

---

## 9. R7 多 Agent：并行编排 + 开关（P2）

> 沿用 M7 文档第 4 章（编排器 / batch / subagent.step 事件 / 结论隔离 / 并发 3）。本文档补充：

- **设置开关**：`多 Agent：开/关`（关=主 agent 不派发子代理，LLM 工具表按开关遮蔽，对齐 KV-cache 遮蔽方案）。
- 子代理卡（浅色版）：角色名 + 实时步数 + 折叠结论，事件接 `subagent.step`。
- 子代理自动用 fast 档模型（与 R4 联动），可按角色覆盖。

---

## 10. 里程碑与工作量口径

| 里程碑 | 内容 | 包含 |
|--------|------|------|
| **V2.1** | R2 Markdown 渲染 + R1 浅色主题 | 纯前端 + 2 处 Electron 常量 |
| **V2.2** | R3 模式系统（M7.1+M7.3） | db + agent + api + 前端 |
| **V2.3** | R4 多模型 + R5 权限分级 | config/llm/api + 设置页 |
| **V2.4** | R6 沙箱分级 + R7 并行子代理（M7.2）+ 设置开关收口 | runtime + tools + 设置页 |

每个里程碑交付后实测验收再进下一个；V2.1 可先行单独开工（不等审完全文档——用户点头 R1/R2 即可动手）。

---

## 11. 待拍板问题（审文档时顺手定）

1. **浅色色板**：3.2 的白+浅蓝方向对不对？要不要把 Primary 换成更深的蓝（#2563EB）？
2. **Markdown 范围**：要不要支持数学公式（KaTeX）和 Mermaid 图？秋招场景暂时用不上，建议 M 期后再说。
3. **全自动权限**：接受"仅单会话可开 + 高危工具不可豁免"这个约束吗？
4. **M7 文档四个拍板项**：按 5.1 的建议表定了吗？
5. **旧星芒视觉**：Logo（星芒图形标）保留不动，只换配色——确认？（Logo 与主题色无耦合，白底下用现有 1x1 图即可）

—— 审完本文档即按第 10 章顺序开工，V2.1 先行。

---

## 12. 实现记录（2026-11-02 傍晚回写，V2.1-V2.4 全部交付）

**V2.1 渲染+浅色**：`web/src/lib/md.js`（markdown-it+hljs common，html:false 安全，fence 包头部栏带复制钮，流式未闭合 ``` 补闭合）；ThreadPane `.doc-body` 改 `v-html=renderMd()`；styles.css 换 v4 白昼色板（白底/浅蓝灰面板/主蓝 #3E7BFA）+ md 排版/代码块/chip/弹窗全套样式；main.ts backgroundColor #ffffff + titleBarOverlay #f6f8fb/#1b2733；index.html/manifest theme_color 同步。标题栏工具钮：收起侧栏（localStorage 记忆）/设置/帮助/关于；浏览器桌面宽度也有同款顶栏。**产物预览**：`/api/workspace/files|file|open|reveal` 四端点 + InfoPane「产物」tab + 预览大窗（md 渲染/html iframe sandbox/txt pre/其它给打开钮；复制内容/复制路径/在文件夹中显示/打开文件）。

**V2.2 模式系统**：sessions 加列 mode/plan_md/autonomy/goal_ref（storage/migrations.py 启动时 ALTER，幂等）；`PATCH /api/sessions/{id}`（exclude_unset 支持显式清空）；run_turn 加 mode/autonomy/plan_md/goal 注入；plan 模式=系统提示词 + **写工具 spec 过滤 + 执行时双保险硬禁**；计划送审 `POST /api/plans`（复用旧库 confirmations）→ InfoPane「确认」tab 批准 → plan_md 写回会话切回 chat 注入执行。goal 模式绑定旧库 goals 表，`POST /api/goals/{id}/advance` 后台线程推进一步。

**V2.3 多模型+权限**：config._norm_models 注册表（实测 config.local.json 已配 DeepSeek Flash/Chat/Reasoner 三模型）；llm.py 按名解析独立 client（懒建，未知回落默认）；`GET /api/models`；会话级模型切换（composer 下拉 → sessions.model）。权限三档走 sessions.autonomy：semi=本会话同类动作批准一次放行（内存记账）；auto=非高危自动放行；**高危关键词（发送/删除类）任何档强制人工**；全自动档的放行与拦截全落 audit。

**V2.4 沙箱+多Agent**：`sandbox.py` Windows Job Object（512MB 内存 + 进程≤32 + KILL_ON_JOB_CLOSE；踩坑：BASIC_LIMIT_INFORMATION 忘了 SchedulingClass 字段 → BAD_LENGTH，Affinity 用 c_size_t）；tool_run_python 双档（strict 失败降级 basic 留痕）；`runtime/subagents.py` 线程池编排（单次上限 settings.max_subagents，全局硬顶 6）；agent_run 支持 batch 并行 + subagent.start/step/done 事件桥到主 SSE（thread-local run ctx）；多 Agent 关闭=spec 遮蔽 agent_run；设置页沙箱档/开关/上限全接 `GET|PUT /api/settings`（写回 config.local.json + audit）。

**踩过的三个雷（与 M2 轮 audit 遮蔽同族）**：① 新增端点局部 `from . import audit` 又导成模块（已删，顶部函数导入够用）；② audit 签名是 `tool_name`/`risk_level` 不是 `tool`/`risk`；③ SYSTEM_TMPL 里 `[{role,task},...]` 被 str.format 当占位符 → KeyError('role,task')，花括号转义修复。

**端到端验证**：electron-vite build 绿（92 模块）；Job Object 沙箱 basic/strict 双档实测输出正确；模式 PATCH、设置 PUT、计划送审→批准→plan_md 注入→清空全闭环；真实 DeepSeek SSE 全链路 `run_started→message.delta→final→message_final→run_done→done` 通过。
