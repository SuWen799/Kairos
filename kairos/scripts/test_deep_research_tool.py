# -*- coding: utf-8 -*-
"""单测 tool_deep_research：真实走 DeerFlow 引擎一次。"""
import sys
sys.path.insert(0, r"C:/Users/<user>\Desktop\秋招文件\项目源码\Kairos\kairos")

from kairos.tools import tool_deep_research

result = tool_deep_research("什么是 LangGraph？一句话说明即可，不要搜索网页", depth="quick")
print(result[:600])
