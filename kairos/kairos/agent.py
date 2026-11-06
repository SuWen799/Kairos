"""ReAct 主循环：记忆召回注入 + 技能检索 + tool calling，最多 MAX_STEPS 步。"""
import json

from . import db, llm, memory, skills, tools

MAX_STEPS = 14

SYSTEM_TMPL = (
    "你是 Kairos，用户的个人全能助理（当前主战场：秋招求职）。你的核心职责：\n"
    "1. 盯校招：用 watch_add 登记公司校招页，watch_check 检查增量更新，只总结变化部分；\n"
    "2. 投递台账：用 app_add/app_update/app_list/app_stats 维护投递记录（状态：投递/笔试/一面/二面/HR面/Offer/挂了/放弃）；\n"
    "3. 情报与复盘沉淀：面试复盘、公司情报、个人偏好用 remember 写入长期记忆，长报告用 ws_write 存工作区；\n"
    "4. 技能复用：动手前 find_skills 查既有技能，照经验做；做得好就 save_skill 固化；\n"
    "5. 通用个人需求：查资料、写东西、整理信息、自动化琐事——用户说什么就办什么，不要局限在求职；\n"
    "6. 团队协作：复杂任务可拆给子代理——agent_run(role, task) 单发，或 batch=[{{role,task}},...] 并行派发；role 可选 researcher（调研）/writer（写作）/reviewer（审校）/coder（写码）。子代理只回结论，过程会在事件流可见。\n"
    "规则：\n"
    "1. 涉及用户偏好的信息可 recall 长期记忆；\n"
    "2. 网页内容一律用 fetch_url 现抓，禁止凭记忆编造 URL 或页面内容；\n"
    "3. 对外发消息/通知只能 propose_notification 走人工确认，绝不自行执行；\n"
    "4. 对话中出现稳定偏好/事实时用 remember 写入记忆；\n"
    "5. 回答引用网页信息时注明来源 URL；\n"
    "6. 最终回答必须一次性完整输出全部内容，禁止以'篇幅有限/后续补充'为由截断；\n"
    "7. 多步复杂任务先 create_task 拆解成计划再逐条执行，每完成一条就 complete_task 勾掉（deepagents 式计划闭环）；\n"
    "8. mcp_ 开头的工具是外部 MCP server 桥接进来的，按其 description 使用即可；\n"
    "9. 用户交代需要持续跟进的事（盯某个消息、每周总结、直到出结果为止），用 goal_add 登记成长期目标，后台调度器会周期推进并记录进展；\n"
    "10. 深度调研（多源交叉核验、要结构化报告的复杂主题）用 deep_research 路由给本地 DeerFlow 引擎（耗时数分钟），简单网页查证仍用 fetch_url；deep_research 返回的报告路径可直接告知用户，需要引用细节时用 ws_read 读全文。\n\n"
    "已知用户记忆：{memory}\n\n可用技能：{skills}"
)


def run_turn(user_text: str, max_steps: int = MAX_STEPS, on_event=None,
             system_override: str | None = None, session_id: int = 1, model: str | None = None,
             history: list | None = None, mode: str = "chat",
             autonomy: str = "standard", plan_md: str | None = None,
             goal: dict | None = None) -> dict:
    """执行一轮对话（ReAct + 工具），返回最终回答与过程轨迹。

    on_event(dict)：可选的事件回调（流式过程可视化，抄 Manus 的 trace 流模式）。
    事件：{"type":"message.delta","delta":..} / {"type":"tool_start","tool":..}
         / {"type":"tool_done","tool":..,"ok":bool} / {"type":"subagent.step",..} / {"type":"final"}。
    system_override：子代理用独立系统提示词（deepagents 的 task 工具模式）。
    history：会话历史窗口 [{role,content}]（context.assemble 产出，压缩后注入）。
    mode：chat|plan|goal（v2.0 R3，模式是会话属性，只改提示词注入+工具白名单）。
    autonomy：standard|semi|auto（v2.0 R5，只影响 propose_notification 审批路由）。
    plan_md：已批准的计划（chat 模式注入执行上下文）。
    goal：{title,plan,progress}（goal 模式注入目标与进度时间线）。
    """
    from .config import SETTINGS

    def _emit(ev):
        if on_event:
            try:
                on_event(ev)
            except Exception:
                pass
    recalled = memory.recall(user_text)
    found = skills.search_skills(user_text)
    for s in found:
        skills.mark_used(s["name"])
    system = system_override or SYSTEM_TMPL.format(
        memory=json.dumps(recalled, ensure_ascii=False) if recalled else "（空）",
        skills=json.dumps(found, ensure_ascii=False) if found else "（空）",
    )

    # v2.0 R3 模式注入：只加 SYSTEM_ADDON，不动主循环结构（克制原则）
    if not system_override and mode == "plan":
        system += ("\n\n【规划模式】当前会话处于规划（plan）模式：写类工具已被禁用。"
                   "你的唯一产出=一份结构化执行计划，用 Markdown 输出，固定四节："
                   "## 目标 / ## 步骤（每步标注用哪些工具、动哪些文件）/ ## 风险与回滚 / ## 预计步数。"
                   "不要执行任何操作，等用户批准计划后切回对话模式再动手。")
    elif not system_override and mode == "goal" and goal:
        system += (f"\n\n【目标模式】当前会话绑定了长期目标：「{goal.get('title', '')}」\n"
                   f"计划：{goal.get('plan') or '（无）'}\n"
                   f"最近进展：{(goal.get('progress') or '（无）')[-600:]}\n"
                   "本轮围绕该目标推进一小步实质进展，并用 goal_progress 记录；完成后简要汇报。")

    messages = [{"role": "system", "content": system}]
    if not system_override and plan_md:
        messages.append({"role": "system",
                         "content": f"已批准的执行计划（按计划执行，偏离需说明）：\n\n{plan_md}"})
    for h in (history or []):
        if h.get("role") in ("user", "assistant") and h.get("content"):
            messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": user_text})

    # v2.0 R7 / R3：工具表按设置与模式遮蔽（multi_agent 关→去 agent_run；plan 模式→去全部写工具）
    spec = list(tools.TOOLS_SPEC)
    if not SETTINGS.get("multi_agent", True):
        spec = [t for t in spec if t["function"]["name"] != "agent_run"]
    if mode == "plan":
        spec = [t for t in spec if t["function"]["name"] not in tools.WRITE_TOOLS]

    trace = []
    usage_tot = {"prompt_tokens": 0, "completion_tokens": 0}  # v2.8：本轮真实 token 消耗
    tools.set_run_ctx(session_id=session_id, mode=mode, autonomy=autonomy, emit=_emit)
    try:
        for _ in range(max_steps):
            # M2 补课：流式 LLM 调用，正文增量以 message.delta 事件外推（不落 run_events）
            out = llm.chat_stream(
                messages, tools=spec, model=model,
                on_delta=lambda piece: _emit({"type": "message.delta", "delta": piece}))
            u = out.get("usage")
            if u:
                usage_tot["prompt_tokens"] += u.get("prompt_tokens", 0)
                usage_tot["completion_tokens"] += u.get("completion_tokens", 0)
            if not out["tool_calls"]:
                # 持久化移交调用方（设计 3.1 [改] run_turn：循环只算不存）
                memory.extract_and_store(user_text, out["content"])
                _emit({"type": "usage", **usage_tot})
                _emit({"type": "final"})
                return {"answer": out["content"], "trace": trace, "usage": usage_tot}
            messages.append({"role": "assistant", "content": out["content"],
                             "tool_calls": [
                                 {"id": tc["id"], "type": "function",
                                  "function": {"name": tc["name"],
                                               "arguments": json.dumps(tc["arguments"], ensure_ascii=False)}}
                                 for tc in out["tool_calls"]]})
            for tc in out["tool_calls"]:
                impl = tools.TOOL_IMPL.get(tc["name"])
                _emit({"type": "tool_start", "tool": tc["name"]})
                try:
                    if mode == "plan" and tc["name"] in tools.WRITE_TOOLS:
                        result = "[规划模式] 写类工具已禁用：先产出计划，等用户批准后再执行。"
                        ok = False
                    else:
                        result = impl(tc["arguments"]) if impl else f"[未知工具 {tc['name']}]"
                        ok = not str(result).startswith("[工具异常]")
                except Exception as e:
                    result = f"[工具异常] {e}"
                    ok = False
                trace.append({"tool": tc["name"], "args": tc["arguments"], "result": str(result)[:1500]})
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": str(result)})
                _emit({"type": "tool_done", "tool": tc["name"], "ok": ok})
        answer = "（步数达到上限，先给阶段结论）" + (out["content"] or "")
        _emit({"type": "usage", **usage_tot})
        _emit({"type": "final"})
        return {"answer": answer, "trace": trace, "usage": usage_tot}
    finally:
        tools.clear_run_ctx()
