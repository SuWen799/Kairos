# -*- coding: utf-8 -*-
"""SSE 端到端验证：message.delta 流式事件（临时脚本，跑完即删）。"""
import json
import urllib.request

BASE = "http://127.0.0.1:8631"


def req(method, path, body=None, token=None):
    r = urllib.request.Request(BASE + path, method=method)
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    data = json.dumps(body).encode() if body else None
    if data:
        r.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(r, data=data, timeout=15) as resp:
        return json.loads(resp.read() or b"{}")


req("POST", "/api/auth/register", {"email": "sse@kairos.dev", "password": "Passw0rd!234"})
tok = req("POST", "/api/auth/login", {"email": "sse@kairos.dev", "password": "Passw0rd!234"})["token"]
sess = req("POST", "/api/sessions", {"title": None}, token=tok)

# SSE 流式对话
r = urllib.request.Request(BASE + "/api/chat/stream", method="POST")
r.add_header("Authorization", f"Bearer {tok}")
r.add_header("Content-Type", "application/json")
body = json.dumps({"session_id": sess["id"], "text": "只回复四个字：收到即可"}).encode()

counts = {}
final_answer = ""
with urllib.request.urlopen(r, body, timeout=120) as resp:
    buf = ""
    while True:
        chunk = resp.read(1024)
        if not chunk:
            break
        buf += chunk.decode("utf-8", errors="replace")
        while "\n\n" in buf:
            frame, buf = buf.split("\n\n", 1)
            etype, data = None, {}
            for line in frame.split("\n"):
                if line.startswith("event: "):
                    etype = line[7:].strip()
                elif line.startswith("data: "):
                    try:
                        data = json.loads(line[6:])
                    except Exception:
                        pass
            if etype:
                counts[etype] = counts.get(etype, 0) + 1
                if etype == "message_final":
                    final_answer = data.get("answer", "")

print("事件统计:", counts)
print("final:", final_answer[:60])
deltas = counts.get("message.delta", 0)
assert deltas > 0, "没有收到 message.delta"
assert counts.get("message_final", 0) >= 1
print(f"\n=== SSE 端到端过：{deltas} 个 message.delta + message_final ===")
