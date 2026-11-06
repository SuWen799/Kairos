# -*- coding: utf-8 -*-
"""DeerFlow /api/runs/wait 冒烟测试 v2：带 CSRF double-submit 对。"""
import json
import secrets
import urllib.request

TOKEN = secrets.token_urlsafe(64)
BODY = {
    "input": {"messages": [{"role": "user", "content": "只回复两个字：收到"}]}
}
req = urllib.request.Request(
    "http://127.0.0.1:8001/api/runs/wait",
    data=json.dumps(BODY).encode("utf-8"),
    headers={
        "Content-Type": "application/json",
        "X-CSRF-Token": TOKEN,
        "Cookie": f"csrf_token={TOKEN}",
    },
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        print("HTTP", resp.status)
        print(json.dumps(data, ensure_ascii=False)[:2000])
except urllib.error.HTTPError as e:
    print("HTTPError", e.code)
    print(e.read().decode("utf-8", "replace")[:2000])
except Exception as e:
    print("FAIL", type(e).__name__, e)
