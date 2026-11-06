# -*- coding: utf-8 -*-
"""Kairos ↔ WorkBuddy 桥：极简 MCP stdio server（协议最小实现）。

让 WorkBuddy 把 Kairos 当成一个 MCP server：在 WorkBuddy 的 mcp.json 里配：
  "kairos": { "command": "<python>", "args": ["<本文件路径>"],
              "env": { "KAIROS_URL": "http://127.0.0.1:8630",
                       "KAIROS_TOKEN": "<手机遥控配对码，本机可留空>" } }

工具：
  kairos_chat(text)     —— 发一句话给 Kairos 主 Agent，同步等结果（与飞书遥控同一条路由）
  kairos_approve(id)    —— 审批待确认项（等价 /ok <id>）
  kairos_pending()      —— 列待审批项
协议：JSON-RPC 2.0 over stdio（initialize / tools/list / tools/call）。
"""
import json
import os
import sys
import urllib.request

KAIROS_URL = os.environ.get("KAIROS_URL", "http://127.0.0.1:8630").rstrip("/")
KAIROS_TOKEN = os.environ.get("KAIROS_TOKEN", "")


def _call_inbox(text: str, sender: str = "workbuddy") -> str:
    body = json.dumps({"sender": sender, "text": text[:4000]}).encode("utf-8")
    req = urllib.request.Request(
        KAIROS_URL + "/api/channel/http", data=body,
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {KAIROS_TOKEN}"} if KAIROS_TOKEN else {})})
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read().decode("utf-8")).get("reply", "")


TOOLS = [
    {"name": "kairos_chat", "description": "给 Kairos 个人 Agent 发一条指令并等结果（遥控电脑上的 Agent 干活：调研/盯页/写文件/查台账）。长任务建议在指令里让它拆步。",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "kairos_pending", "description": "列出 Kairos 当前待人工审批的动作",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "kairos_approve", "description": "批准/拒绝一条待审批项（approve=true 批准）",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}, "approve": {"type": "boolean"}}, "required": ["id"]}},
]


def _tools_call(name: str, args: dict) -> dict:
    if name == "kairos_chat":
        text = str(args.get("text", "")).strip()
        if not text:
            text_out = "（空指令）"
        else:
            text_out = _call_inbox(text) or "（Kairos 空回复）"
    elif name == "kairos_pending":
        text_out = _call_inbox("/pending")
    elif name == "kairos_approve":
        text_out = _call_inbox(f"{'/ok' if args.get('approve', True) else '/reject'} {args.get('id', '')}")
    else:
        text_out = f"未知工具: {name}"
    return {"content": [{"type": "text", "text": text_out[:6000]}]}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        rid, method = msg.get("id"), msg.get("method", "")
        if method == "initialize":
            result = {"protocolVersion": "2024-11-05",
                      "capabilities": {"tools": {}},
                      "serverInfo": {"name": "kairos-bridge", "version": "0.1.0"}}
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            p = msg.get("params", {})
            try:
                result = _tools_call(p.get("name", ""), p.get("arguments", {}) or {})
            except Exception as e:  # noqa: BLE001
                result = {"content": [{"type": "text", "text": f"[Kairos 桥错误] {e}"}], "isError": True}
        else:
            if rid is not None:
                print(json.dumps({"jsonrpc": "2.0", "id": rid,
                                  "error": {"code": -32601, "message": "method not found"}}), flush=True)
            continue
        if rid is not None:
            print(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
