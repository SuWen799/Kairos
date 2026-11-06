# RSI 与 Agent Harness 调研笔记

> 调研日期：2026-10-25。源码已落盘至 `参考项目/AetherLabsAI/RSIAgent/` 与 `参考项目/langchain-ai/deepagents/`（因网络限制无法直接 `git clone`，采用镜像 + GitHub API 逐文件下载，除 5 个空 `__init__.py` 外核心文件齐全）。
>
> - RSIAgent：AetherLabsAI/RSIAgent，论文 arXiv:2609.15364《Autonomous Exploration for Recursive Self-improvement in New Environments》（HF Daily Papers 2026-09-15 第 6 名），Apache-2.0
> - deepagents：langchain-ai/deepagents v0.7.32，"Production-ready, extensible agent harness"，MIT

---

## 一、去掉论文包装后：RSI 的工程本质是什么

RSI（递归自进化）在工程上只需要四个要素，没有任何一个是"神秘技术"：

1. **一个可重复执行的任务环境**（RSIAgent 里是 OSWorld-V2 的 VM 快照；对个人 Agent 来说就是能 reset 的工作目录/沙箱）。
2. **一份持久的经验存储**（RSIAgent 里是 `~/.memory` 文件树，随每轮 episode 同步进出 VM；对个人 Agent 就是一个 markdown/jsonl 目录）。
3. **一个诚实的判分器**（Verifier），它的 PASS/FAIL 是唯一可信信号——关键是**判分器不看 Actor 的自我陈述、也不能被 Actor 改记忆**。
4. **一个外层循环**：目标失败 → 诊断 → 出练习题 → 练习（带独立判分）→ 固化经验 → 重试目标。练习题由 Curriculum Agent 根据"哪个经验最有信息量"来生成。

**防退化的本质不是算法，而是流程纪律**（RSIAgent 的贡献主要在这）：
- 记忆只在**已验证边界**（episode 终结、verifier 出结果之后）才允许 commit；
- commit 前做**机器审计**（防基准泄漏的 8-gram shingle 扫描），审计拒绝不告知 agent；
- 每轮记忆快照做**内容哈希**（tree_sha256），快照不可变、append-only journal 可回滚；
- Curriculum 只选下一步做什么，**无权编辑或批准记忆**；Actor 自己拥有记忆；Verifier 与 Actor 的私有工件机械隔离。

一句话：**RSI = 把"练题-判分-记笔记"做成状态机，把"记笔记"的权力关进笼子。**

---

## 二、RSIAgent 机制拆解（带源码路径）

### 2.1 三角色 + Host harness 的职责划分

来源：`docs/ARCHITECTURE.md`

| 角色 | 输入与权限 |
| --- | --- |
| Actor Agent | 操作环境、读写 `~/.memory`、蒸馏自己的已验证经验 |
| Verifier Agent | 独立检查候选产物（Actor 私有工件对其隐藏），出 PASS/FAIL/UNVERIFIED；在同一 target 的多次修订间保持上下文 |
| Curriculum Agent | 复盘学习进度、选下一个练习/目标尝试；**不判分、不改记忆**，对记忆只读 |
| Host harness | 强制隔离、记录工件、只在合法边界提交记忆、调用密封的外部评测器 |

角色 prompt 全部集中在 `explore/charter.py`（约 1500 行）与 `config/roles/*.yaml`。

### 2.2 主循环状态机：`core/self_evolving_loop.py`（411 行，最值得抄）

这是去掉一切 IO 后的纯状态机，用 hooks 依赖注入，`run_self_evolving_loop()`（L166）：

```
while True:
    target_cycles += 1
    environment = hooks.fresh_target_environment(...)   # 全新环境
    verifier    = hooks.fresh_target_verifier(...)      # 新 Verifier
    actor       = hooks.fresh_target_actor(memory, ...) # 新 Actor（带当前记忆）
    output      = hooks.actor_work(actor, verifier, environment, direction)
    verification= hooks.verifier_verify(...)            # → PASS/FAIL/UNVERIFIED
    if FAIL 或 (PASS 且 learn_on_pass):
        learning = hooks.actor_learn(...)               # 同一 Actor 上下文做蒸馏
        memory   = learning.memory
    if PASS: return 结果                                 # verifier_pass 终止策略
    evolved  = hooks.evolve(direction, verdict, report, diagnosis, memory, ...)
    memory   = evolved.memory                          # Curriculum 出练习题的循环
    if PASS 且 evolved.projects == 0: return           # curriculum_ready / stalled
    final_after_stall = (evolved.status == STALLED)
```

关键数据类（都是 frozen dataclass，L52-137）：
- `TargetVerification(verdict: PASS|FAIL|UNVERIFIED, report: str)` — report 强制非空；
- `ActorLearning(memory, diagnosis)` — diagnosis 必须非空，是给 Curriculum 的因果交接；
- `EvolutionResult(status: READY_FOR_RETRY|STALLED, memory, projects, reason)`；
- `SelfEvolvingLoopResult(..., termination: str)` — 终止原因字符串化：`verifier_pass` / `curriculum_stalled_final_target_test` / `target_harness_unverified` 等，**基础设施错误永远不会被算成 PASS/FAIL**。

默认终止策略是 `CURRICULUM_REVIEW`（L38）：即使 PASS，也先让 Curriculum 复盘是否值得补一个对照练习；若补练，之后必须**在干净环境重试原目标**才能自然完成（防止"练过再考"污染成绩）。

### 2.3 Curriculum Agent 怎么出任务

`explore/charter.py:242` `self_evolving_curriculum_charter()`：

- 决策集是固定三选一（L296-311）：Phase-1 探索模式为 `PROJECT / SATURATED / STALLED`；Phase-2 复盘模式为 `PROJECT / READY_FOR_TARGET / STALLED`。决策以单行 `DECISION: X` 输出，机械可解析。
- prompt 里反复强调（L363-421）：target query 只提供方向，**不含评测器/隐藏检查/分数**，禁止 agent 寻找或模拟密封评测材料；PROJECT 必须是"前置/变体/对照/压力测试"四类练习之一，且完全落在 `~/evolution_project` 内。
- 收敛契约（L312-361）：SATURATED/READY 必须基于证据（"新鲜 Actor 上下文在显著不同的项目上展示了迁移能力"、"积累的记忆是否开始产生干扰"），STALLED 只在"提不出有生产力的新假设"时用。**没有任何固定轮数/项目数指标**——防止 agent 为了凑数而刷项目。
- 并行探索（broad-then-deep）：`explore/phase1_wave.py`——Curriculum 一批出多个练习项目（wave），每个分支从**同一记忆快照**起步独立执行，全部完成验证后才统一按顺序蒸馏经验，再进入下一 wave（`docs/ARCHITECTURE.md` Phase 1 节）。

### 2.4 Actor 怎么执行 + Verifier 怎么校验

Actor 端（`explore/charter.py:590` `self_evolving_actor_charter()`）：
- 每个 project 一个**全新 Actor 上下文**，拿到：记忆文件清单（`_listing(before_memory)`）+ Curriculum 的自然语言 handoff；
- 关键纪律写入 prompt：**项目进行期间禁止更新持久记忆**（"Do not update durable memory during project work"）；收工时写一份无损 handoff 到 `~/actor_handoff.md`，首行 `STATUS: SUBMIT`——SUBMIT 是中性的，不等于"我做对了"；
- 每集只有"一个 Actor 工作阶段 + 一个 Verifier 判定"，**同项目没有修复-重验循环**——暴露真实能力边界。

Verifier 端（`explore/charter.py:1060` 起、`core/verifier.py`）：
- Verifier 是**持久上下文**（同一 target 的多轮修订间复用），但拿不到 Actor 的记忆、handoff 文本、执行日志——`explore/target_learning.py:421-435` 机械隐藏：`verifier_hide_actor_memory=True`，`verifier_private_paths` 里显式加入 `ACTOR_HANDOFF` 和执行证据路径；
- 校验时 harness 做**三重完整性验证**（`explore/target_learning.py:466-468`）：`_verify_candidate`（候选可复现）、`_verify_actor_execution_evidence`（执行清单未被篡改）、`_verify_original_practice_fixtures`（对照组未被 Verifier 污染）；
- Verifier 的探索性操作跑在**回滚镜像**里（`env/qemu_rollback.py`，VM 先打 checkpoint，验收完回滚，"guest-local mutations are rolled back"）。

### 2.5 演化记忆/经验固化（重点）

**存储形态**：记忆就是 guest 里 `~/.memory` 下的自由格式文件树。`core/self_evolving_loop.py` 与 `explore/target_learning.py` 都不对记忆内容做任何 schema 约束——蒸馏 prompt（`explore/charter.py:1193-1202`）明说："You own the content, representation, retrieval strategy, scope... there is no required schema, length, number of files"。哈希清单见下。

**固化三阶段**（同一 Actor 上下文内，verifier 出结果后；`explore/target_learning.py:199-254` `_promote_learning`）：
1. **蒸馏**（`self_evolving_actor_memory_distillation_msg`，charter.py:1164）：喂入完整 Verifier report 原文，让 Actor 自由决定增删改 `~/.memory`；明确约束："FAIL 仍可能包含有价值证据，但**不得记为已验证的成功**"；"只在轨迹和证据支持时做因果断言，否则保留不确定性"。
2. **对账**（`self_evolving_actor_memory_reconciliation_msg`，charter.py:1205）：第一遍更新视为草稿未生效；要求 Actor 清点整个记忆树，找**相互矛盾的断言、无支撑的因果解释、过时的环境假设、超出证据范围的结论**，"不要只追加本集经验而把被反驳的旧建议继续当事实"。
3. **因果诊断**（`self_evolving_actor_learning_diagnosis_msg`，charter.py:1232）：另写一份 `~/learning_diagnosis.md` 给 Curriculum（与持久记忆分离的交接物）：试过什么、哪些可靠、哪些不确定、什么对照实验能区分竞争性假设。

**提交与审计**（host 侧，`explore/commit.py`，模块 docstring 自述 "v2.0 control split"——host 只搬运/归档/扫描，绝不代写内容）：
- `push_memory` / `pull_memory`（L24/L74）：host 目录 ↔ guest `~/.memory`，tar.gz + base64 分块传输；pull 时过滤 `__pycache__/*.pyc`（"bytecode is a build artifact, not experience"，L113，v1.1 修过的真实 bug）；
- `journal`（L139）：每集记忆做 **append-only 快照**（`ep{NNN}.tgz`）+ `journal.jsonl` 一行元数据（episode、文件数、字节数、kind、terminal_outcome），"对 agent 不可见，纯回滚/审计保险"；
- `silent_audit`（L227）：**防基准泄漏的机器审计**——把所有任务指令建 8-gram shingle 语料（`build_corpus`，L186，host-only，永不进 agent 上下文），记忆文件若与任务指令共享任何 8-gram 就被**静默拒绝**（记录到 host 侧 reject_log，不告知 agent）。这是防止 agent 把考题抄进记忆作弊的关键闸门；
- `_memory_tree_sha256`（`explore/target_learning.py:74`）+ `explore/memory_hash.py:39` `tree_sha256`：对 `{相对路径: 文件sha256}` 做 canonical JSON 排序后再哈希，得到不可变的记忆根哈希，写进 episode 工件（Phase 3 冻结评测时校验"冻结配置哈希、记忆哈希、终态"）。

**复用**：下一轮 Actor 启动时，prompt 里注入记忆文件**清单**（不是全文，`self_evolving_actor_charter` L605-608 "DURABLE MEMORY FILES:" + listing），记忆挂载在 `~/.memory`，prompt 明确告诉 Actor："它可能有用、错误、过时或不相关，自己决定查什么，并与活环境核对"。检索策略完全交给 Actor 自己（grep/读文件），host 不做向量化召回。

**防退化**的完整链条：记忆只能被"同 Actor 在拿到 Verifier 判定后"更新 → 更新要过两阶段（蒸馏+对账）→ 提交要过静默泄漏审计 → 每版有不可变快照可回滚 → 下一 wave 从统一快照分叉 → 练习全过验证才 commit → STALLED/预算退出与成功收敛在终止字符串上强制可区分。

---

## 三、deepagents 可借鉴点

deepagents 的架构是"**一个 LangGraph agent + 一摞 middleware**"，`libs/deepagents/deepagents/graph.py:216` `_apply_custom_middleware` 按名字把自定义 middleware 替换/追加进基础栈。对个人 Agent 有用的点：

### 3.1 Harness 结构
- 主入口 `create_deep_agent`（`graph.py`）：基础 middleware 栈 = 文件系统 + 子代理 + 规划 + 摘要 + 技能 + 记忆 + 权限（HIL）+ prompt caching；模型专属 harness profile（`profiles/harness/`）做 per-model 微调。
- 状态优化：`DeepAgentState`（graph.py:77）用 `DeltaChannel` 让 messages checkpoint 从 O(N²) 降到 O(N)——长会话工程细节，值得知道。

### 3.2 子代理 spawn（`middleware/subagents.py`）
- 子代理就是一个 `SubAgent TypedDict`（L84）：`{name, description, system_prompt, model, tools, middleware}`；`_build_task_tool`（L647）把它编译成一个 `task` 工具供主 agent 调用；父 agent 能看到的只是每个子代理的 name+description（L673-680 拼成工具描述）。
- 子代理结果回传只保留 `messages` 里**最后一条有文本的 AIMessage**（L770-777），其余状态丢弃——上下文隔离的核心就是"子代理只交回结论"。
- 支持 `mode: fork`（fork 模式继承父对话，L380 `_fork_messages`）与 compiled runnable 两种。

### 3.3 文件系统工作区（`backends/`）
- `BackendProtocol`（`backends/protocol.py`，1006 行）抽象了 ls/read/write/edit/glob/grep，实现有 FilesystemBackend（真磁盘）、StateBackend（存在 graph state 里、随 checkpointer 持久化）、SandboxBackend 等。同一套 agent 代码可以跑在本地或沙箱。
- 个人 Agent 借鉴：**把"工作区"抽象成协议**，初期只实现本地目录，未来换沙箱零改动。

### 3.4 技能机制（`middleware/skills.py`）
- 技能 = 目录 + `SKILL.md`（YAML frontmatter: `name`(≤64 字符) + `description`(≤1024 字符)）+ 可选辅助脚本（L23-44）。
- **渐进披露**：system prompt 里只注入技能的 name/description/来源标签；agent 通过 `read_file` 打开某个 SKILL.md 后，其工具才被解锁（L141 `_skill_tools` 的 `_SKILL_TOOLS_DISCLOSED_KEY` 机制）。
- 多来源分层：`base → user → project → team`，同名技能后载者覆盖（L8-11）。
- 安全上限：单文件 10MB、名字 64 字符、描述 1024 字符（L162-174）。

### 3.5 记忆（`middleware/memory.py`）
- 就是 AGENTS.md：多来源拼接注入 system prompt 的 `<agent_memory>` 块（L104-121），并附一段"信任与验证"守则：记忆是磁盘上的文件数据，可能过时/错误，与用户请求或工具证据冲突时以后者为准；学到新知识时**主动调用 `edit_file` 更新记忆文件**（"capture WHY and encode it as a pattern"）。
- 个人 Agent 借鉴：记忆 = "永远加载的上下文（AGENTS.md）" 与 "按需加载的工作流（skill）" 分两层，比单一大记忆文件干净。

### 3.6 上下文管理（`middleware/summarization.py`）
- 双通道压缩：`SummarizationMiddleware`（token 超阈值自动摘要旧消息，`trigger=("fraction",0.85)`/`keep=("fraction",0.10)`）+ `SummarizationToolMiddleware`（给 agent 一个 `compact_conversation` 工具主动触发）。
- 被挤出的完整历史 offload 到 `/conversation_history/{session_id}.md` 追加保存，可随时回查——"压缩但不丢失"。

### 3.7 两者在"经验/技能固化"上的数据结构对比

| | RSIAgent memory | deepagents skills/memory |
| --- | --- | --- |
| 表示 | 自由文件树 `~/.memory/**`（无 schema） | `SKILL.md`（name+description frontmatter） / `AGENTS.md`（纯 markdown） |
| 谁写 | Actor 自己（harness 只搬运/审计） | 模型通过 `edit_file`/`write_file` 工具写 |
| 注入 | 启动时只给文件清单，内容按需 grep/read | memory 全文进 system prompt；skill 只给目录，读 SKILL.md 才解锁 |
| 校验 | 8-gram 泄漏审计 + tree_sha256 快照 + append-only journal | 无（信任模型自觉） |
| 粒度 | 整树快照（episode 级） | 单技能目录 |

共同结论：**"经验"的最小数据结构就是"带描述的 markdown 文件 + 一段注入 prompt 的清单"**，复杂度都在流程（何时写、怎么审）而非格式。

---

## 四、给 Python 个人 Agent 的落地建议：<500 行的诚实迷你 RSI 循环

参考两家的骨架，建议如下分层（总计约 400-500 行）：

### 4.1 核心状态机（~80 行，抄 `core/self_evolving_loop.py` 的结构）

```python
@dataclass(frozen=True)
class Verdict:
    outcome: str      # "PASS" | "FAIL" | "UNVERIFIED"
    report: str       # 强制非空，Verifier 原文

def run_rsi_loop(task: str, memory_dir: Path, hooks, max_cycles=5):
    for cycle in range(1, max_cycles + 1):
        workdir = hooks.fresh_env()                      # 干净工作目录（可 git clean / tempdir）
        result, handoff = hooks.actor(task, memory_dir, workdir)   # 新会话，注入记忆清单
        verdict = hooks.verifier(task, result, workdir)  # 独立会话：不给 actor 的自我陈述
        if verdict.outcome == "PASS":
            if hooks.review_after_pass(task, verdict, memory_dir):  # 可选 Curriculum 复盘
                continue
            return result
        memory_dir = hooks.distill(memory_dir, handoff, verdict)   # 蒸馏+对账（同一 Actor 会话）
        hooks.practice(task, verdict, memory_dir)                  # Curriculum 出练习题再练一轮
    raise StallError("curriculum stalled")             # 与成功可区分
```

要点：`PASS`/`FAIL`/`STALLED` 永远用不同的终止路径表达；基础设施异常直接 raise，不折算成 FAIL。

### 4.2 记忆层（~120 行，抄 `explore/commit.py`）

- 记忆就是 `memory/` 下的自由 markdown 文件，**无 schema**；但强制一个索引文件 `INDEX.md`（名 + 一行描述），对应 RSIAgent 的 "listing 注入"——启动时只把 `INDEX.md` 塞进 prompt，正文按需读。
- 每轮固化两步走：**蒸馏**（"根据 verifier report 更新记忆，FAIL 不得记为成功，因果断言必须有证据"）+ **对账**（"清点全部记忆文件，消除与本次证据矛盾的旧结论，不要只追加"）。两个 prompt 直接改编 RSIAgent charter.py:1164/1205 的措辞。
- 提交闸门（三件套，全部 host 侧，不给 agent 反馈）：
  1. **哈希快照**：`sha256(sorted({rel: sha256(file)}))` 存为 `snapshot_ep{N}.json`，append-only（`memory_hash.py` 的 tree_sha256）；
  2. **对账日志**：`journal.jsonl` 每行 `{episode, files, bytes, kind, outcome}`；
  3. **静默审计**：维护一个"不许抄进记忆"的语料（你的测试用例/验收标准原文），对记忆文件做 8-gram shingle 交集检测，命中即剔除该文件（`commit.py:227`）。个人场景可简化为 n-gram 或子串匹配。

### 4.3 判分器隔离（~60 行，RSIAgent 最值钱的纪律）

- Verifier 是**独立会话**：输入只有任务描述 + 产出物路径，**绝不**输入 Actor 的总结/handoff（想给就先想清楚这是不是作弊）。
- 若判分可用代码表达（单元测试/断言脚本），优先用代码判分，LLM Verifier 只处理无法机械判定的部分（对应 RSIAgent 的 "agentic verifier 跑在 effect-isolated namespace"）。
- 每轮判分在**只读快照**上做（拷贝 workdir 或 `git stash`），判完即弃，Verifier 的副作用不回流。

### 4.4 Curriculum（~80 行，可选，砍掉也能跑）

- 最小版：FAIL 后固定问模型一次："这次失败的 verifier report 里，哪个能力缺口值得出一道 5 分钟小练习？出题 + 验收标准（写成可执行检查）。"
- 练习的 verdict 同样走 4.3 的判分器；练习产出的经验走 4.2 的蒸馏+对账。
- 决策输出约束成单行 `DECISION: PRACTICE|RETRY|STALLED`，正则解析，解析失败重试——学 RSIAgent 的机械 token。

### 4.5 直接从 deepagents 抄的两件小事

1. **技能目录**：把稳定下来的经验升级为 `skills/<name>/SKILL.md`（frontmatter 只要 name+description），prompt 里永远只列目录；这比让记忆无限膨胀便宜得多。等记忆文件被蒸馏对账三轮没变化，就"晋升"为技能。
2. **AGENTS.md 式常驻记忆** + "学到就写回"守则（`middleware/memory.py:104-121` 的一段话几乎可以原样搬进你的 system prompt）。

### 4.6 诚实的度量

学 RSIAgent Phase 3：每 N 轮用**冻结记忆**、**全新会话**、**未见过的工作目录**重跑一次固定测试集，记录通过率曲线和当时的记忆哈希。没有这条曲线，"进化了"就只是感觉。退出码区分 `CONVERGED / BUDGET_EXHAUSTED / STALLED / INFRA_ERROR`。

---

## 附：本笔记引用的关键源码文件清单

| 文件 | 行数 | 作用 |
| --- | --- | --- |
| `AetherLabsAI/RSIAgent/core/self_evolving_loop.py` | 411 | RSI 主循环状态机（最值得通读） |
| `AetherLabsAI/RSIAgent/explore/charter.py` | ~1500 | 全部角色 prompt（Curriculum/Actor/Verifier/蒸馏/对账/诊断） |
| `AetherLabsAI/RSIAgent/explore/commit.py` | 269 | 记忆搬运、journal、8-gram 静默审计 |
| `AetherLabsAI/RSIAgent/explore/target_learning.py` | 479 | 经验固化三阶段 + 判分器隔离 |
| `AetherLabsAI/RSIAgent/explore/memory_hash.py` | 54 | 记忆树内容哈希 |
| `AetherLabsAI/RSIAgent/docs/ARCHITECTURE.md` | 93 | 官方架构说明（三阶段协议） |
| `langchain-ai/deepagents/libs/deepagents/deepagents/graph.py` | 1030 | harness 装配入口 |
| `.../middleware/subagents.py` | 1060 | 子代理 spawn 与结果回传 |
| `.../middleware/skills.py` | 1432 | 技能渐进披露 |
| `.../middleware/memory.py` | 416 | AGENTS.md 常驻记忆 |
| `.../middleware/summarization.py` | 2420 | 上下文压缩与历史 offload |
