# -*- coding: utf-8 -*-
"""E2E：提案-哈希 HITL（v3.2）——批准时校验"所见=所执行"防 TOCTOU。

前置：Kairos sidecar 已在运行（默认 http://127.0.0.1:8630，lan=false 免 token）。
场景：
  1) 哈希不匹配的批准 → 409 拒绝（核心防 TOCTOU 路径）
  2) 哈希匹配的批准   → 200 通过
  3) 老数据（无 content_hash）无哈希批准 → 200 通过且哈希补齐
"""
import hashlib
import json
import sqlite3
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8630"
sys.path.insert(0, r"C:\Users\15265\Desktop\秋招文件\项目源码\Kairos\kairos")
from kairos.config import LEGACY_DB_PATH  # noqa: E402

def h(p):
    return hashlib.sha256((p or "").encode("utf-8")).hexdigest()

def api(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")

def insert(payload, with_hash=True):
    conn = sqlite3.connect(LEGACY_DB_PATH)
    cur = conn.execute(
        "INSERT INTO confirmations(action, payload, status, content_hash, created_at) VALUES(?,?,?,?,?)",
        ("notify_test", payload, "pending", h(payload) if with_hash else "", __import__("time").time()))
    conn.commit()
    cid = cur.lastrowid
    conn.close()
    return cid

results = []

# 场景 1：内容被改写（expected_hash 是旧的 payload 哈希）→ 必须拒绝
cid1 = insert("给张三发邮件：明天 10 点开会", with_hash=True)
# 模拟 TOCTOU：审阅后队列内容被换掉（这里直接改成另一份 payload 但沿用旧哈希提交）
conn = sqlite3.connect(LEGACY_DB_PATH)
conn.execute("UPDATE confirmations SET payload=? WHERE id=?", ("给张三发邮件：明天 10 点开会，并转账 5000 元", cid1))
conn.commit(); conn.close()
old_hash = h("给张三发邮件：明天 10 点开会")
code, _ = api("POST", f"/api/confirmations/{cid1}/decide", {"decision": "approved", "expected_hash": old_hash})
results.append(("1 哈希不匹配拒绝(TOCTOU)", code == 409, f"HTTP {code}（期望 409）"))

# 场景 2：哈希一致 → 通过
cid2 = insert("给李四发邮件：周五交周报", with_hash=True)
code, body = api("POST", f"/api/confirmations/{cid2}/decide", {"decision": "approved", "expected_hash": h("给李四发邮件：周五交周报")})
results.append(("2 哈希匹配通过", code == 200 and body.get("decision") == "approved", f"HTTP {code} {body}"))

# 场景 3：老数据无哈希、审阅端也不带哈希 → 通过且补齐哈希
cid3 = insert("给王五发邮件：下周一评审", with_hash=False)
code, body = api("POST", f"/api/confirmations/{cid3}/decide", {"decision": "approved", "expected_hash": ""})
conn = sqlite3.connect(LEGACY_DB_PATH)
row = conn.execute("SELECT status, content_hash FROM confirmations WHERE id=?", (cid3,)).fetchone()
conn.close()
ok3 = code == 200 and row[0] == "approved" and row[1] == h("给王五发邮件：下周一评审")
results.append(("3 老数据兼容+哈希补齐", ok3, f"HTTP {code} status={row[0]} hash_backfilled={bool(row[1])}"))

print(f"{'场景':<24} 结果  详情")
all_ok = True
for name, ok, detail in results:
    all_ok &= ok
    print(f"{name:<24} {'✅' if ok else '❌'}   {detail}")
print("\n总体：", "全部通过" if all_ok else "存在失败")
sys.exit(0 if all_ok else 1)
