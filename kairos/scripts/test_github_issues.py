# -*- coding: utf-8 -*-
"""E2E：社区 issue #1 / #2 修复验证（本地直调，不依赖 LLM）。

#1 semi 档"提议入队即记账"→ 记账时机改为真实批准后
#2 单角色 agent_run 子代理清空父轮运行上下文 → set/clear 保存/恢复父级
"""
import sys, threading
sys.path.insert(0, r"C:\Users\15265\Desktop\秋招文件\项目源码\Kairos\kairos")
from kairos import tools  # noqa: E402

results = []
def check(name, ok, detail=""):
    results.append((name, ok, detail))

# ── #2：父级上下文在子代理轮结束（clear_run_ctx）后恢复 ──
def t2():
    sent = []
    tools.set_run_ctx(session_id="parent-sess", mode="chat", autonomy="semi",
                      emit=lambda e: sent.append(e))
    # 模拟单角色 agent_run：子代理 run_turn 在同线程 set → finally clear
    tools.set_run_ctx(session_id=None, mode="chat", autonomy="standard", emit=None)
    tools.clear_run_ctx()
    ctx = tools.get_run_ctx()
    check("#2 父级 session_id 恢复", ctx["session_id"] == "parent-sess", f"got={ctx['session_id']}")
    check("#2 父级 autonomy 恢复", ctx["autonomy"] == "semi", f"got={ctx['autonomy']}")
    check("#2 父级 emit 恢复", ctx["emit"] is not None, f"got={type(ctx['emit']).__name__}")
    tools.clear_run_ctx()  # 顶层收尾 → 应回默认值
    ctx = tools.get_run_ctx()
    check("#2 顶层收尾回默认", ctx["session_id"] is None and ctx["autonomy"] == "standard" and ctx["emit"] is None,
          f"got={ctx}")
th = threading.Thread(target=t2); th.start(); th.join()

# ── #1：semi 档记账时机 = 真实批准 ──
SID = "issue1-test-sess"
tools.set_run_ctx(session_id=SID, autonomy="semi")
ACT = "notify_issue1_test"
PAYLOAD = "记录日志：周五交周报"   # 无高危关键词

r1 = tools.tool_propose_notification(ACT, PAYLOAD)
check("#1 首次提议进人工队列", "人工确认队列" in r1, r1[:60])
check("#1 提议时不再记账", (SID, ACT) not in tools._semi_approved, f"set={tools._semi_approved}")

r2 = tools.tool_propose_notification(ACT, PAYLOAD)
check("#1 二次提议仍进队列（未经批准不放行）", "人工确认队列" in r2, r2[:60])

# 模拟用户真实批准（decide 端点批准后调用 mark_session_approved）
tools.mark_session_approved(SID, ACT)
r3 = tools.tool_propose_notification(ACT, PAYLOAD)
check("#1 真实批准后同类动作自动放行", "自动放行" in r3, r3[:60])

# 高危动作即使已批准过同类也强制人工（记忆键不豁免高危）
r4 = tools.tool_propose_notification("send_email_issue1_test", "例行周报归档")
check("#1 高危动作永不自动放行", "人工确认队列" in r4, r4[:60])

tools.clear_run_ctx()

# 清理测试数据
from kairos import db as _db
conn = _db.get_conn()
n = conn.execute("DELETE FROM confirmations WHERE action IN (?, ?)", (ACT, "send_email_issue1_test")).rowcount
conn.commit()
check("清理测试数据", n >= 4, f"deleted={n}")

print(f"{'场景':<36} 结果  详情")
all_ok = True
for name, ok, detail in results:
    all_ok &= ok
    print(f"{name:<36} {'✅' if ok else '❌'}   {detail}")
print("\n总体：", "全部通过" if all_ok else "存在失败")
sys.exit(0 if all_ok else 1)
