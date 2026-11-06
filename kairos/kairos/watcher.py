"""盯校招：对关注的公司校招页做增量 diff 检测。

每次 check：抓页面 → 归一化文本哈希 → 与 last_digest 比对。
变化则返回变更摘要素材（新旧内容的关键差异），无变化返回 None。
首次登记只建基线。这是"只报增量、不刷屏"的底座。
"""
import hashlib
import json

from . import db, tools

_MIN_TEXT = 300


def _digest(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()


def add_source(name: str, url: str) -> str:
    row = db.query("SELECT id FROM sources WHERE url=?", (url,))
    if row:
        return f"已在盯：{name}"
    db.execute("INSERT INTO sources(name, url) VALUES(?,?)", (name, url))
    # 建基线（同步抓一次）
    check_source(url)
    return f"开始盯：{name}（{url}），基线已建立"


def check_source(url: str) -> dict:
    """抓单源并 diff。返回 {changed, summary}。"""
    row = db.query("SELECT * FROM sources WHERE url=?", (url,))
    if not row:
        return {"changed": False, "summary": f"未登记的源：{url}"}
    src = row[0]
    text = tools.fetch_url(url)
    if len(text) < _MIN_TEXT:
        return {"changed": False, "summary": f"[{src['name']}] 页面抓取异常或内容过短，跳过本轮比对"}
    digest = _digest(text)
    changed = bool(src["last_digest"]) and digest != src["last_digest"]
    db.execute("UPDATE sources SET last_digest=?, last_check_at=? WHERE id=?",
               (digest, db.now(), src["id"]))
    if src["last_digest"] and changed:
        return {"changed": True, "name": src["name"], "url": url,
                "old": (src["last_digest"] or "")[:8],
                "new": digest[:8], "page_text": text[:4000]}
    return {"changed": False, "summary": f"[{src['name']}] 无更新"}


def check_all() -> list:
    """检查全部启用源，返回有变化的结果列表。"""
    out = []
    for r in db.query("SELECT url FROM sources WHERE status='active'"):
        res = check_source(r["url"])
        if res.get("changed"):
            out.append(res)
    return out


def list_sources() -> list:
    return [dict(r) for r in db.query("SELECT id,name,url,status,last_check_at FROM sources ORDER BY id")]
