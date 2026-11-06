"""技能库 v2：SKILL.md 文件格式（deepagents 渐进披露模式）。

每个技能 = data/skills/<slug>/SKILL.md：
    ---
    name: 总结一个公司校招页
    description: 抓页面→提取板块→按固定结构输出
    uses: 3
    ---
    正文（这类任务的标准做法）。

好处：技能随项目进 git 可追溯、可直接人工编辑、结构对齐行业规范。
"""
import os
import re

from .config import DATA_DIR

SKILLS_DIR = os.path.join(DATA_DIR, "skills")


def _slug(name: str) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", name).strip("-")
    return s[:40] or "skill"


def _parse(md_path: str) -> dict:
    with open(md_path, "r", encoding="utf-8") as f:
        raw = f.read()
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", raw, re.S)
    meta, body = {}, raw
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                meta[k.strip()] = v.strip()
        body = m.group(2).strip()
    return {"name": meta.get("name", os.path.basename(os.path.dirname(md_path))),
            "description": meta.get("description", ""),
            "uses": int(meta.get("uses", "0") or 0),
            "content": body, "path": md_path}


def save_skill(name: str, content: str, description: str = "") -> str:
    """同名技能覆盖更新（技能随经验迭代，use_count 保留）。"""
    d = os.path.join(SKILLS_DIR, _slug(name))
    os.makedirs(d, exist_ok=True)
    md = os.path.join(d, "SKILL.md")
    uses = 0
    if os.path.exists(md):
        old = _parse(md)
        if old["name"] == name:
            uses = old["uses"]
    desc_line = description or content.splitlines()[0][:60] if content else ""
    with open(md, "w", encoding="utf-8") as f:
        f.write(f"---\nname: {name}\ndescription: {desc_line}\nuses: {uses}\n---\n\n{content}\n")
    return md


def search_skills(query: str, limit: int = 3) -> list:
    """关键词检索：扫所有 SKILL.md，name/description/content 命中即返回。"""
    out = []
    if not os.path.isdir(SKILLS_DIR):
        return out
    words = [w for w in re.split(r"[\s，。？?/]+", query) if len(w) >= 2][:6]
    for d in sorted(os.listdir(SKILLS_DIR)):
        md = os.path.join(SKILLS_DIR, d, "SKILL.md")
        if not os.path.exists(md):
            continue
        s = _parse(md)
        blob = s["name"] + s["description"] + s["content"]
        if any(w in blob for w in words):
            out.append(s)
    return out[:limit]


def mark_used(name: str):
    """记录技能被复用次数——自进化效果的可量化指标。"""
    for d in os.listdir(SKILLS_DIR) if os.path.isdir(SKILLS_DIR) else []:
        md = os.path.join(SKILLS_DIR, d, "SKILL.md")
        if os.path.exists(md):
            s = _parse(md)
            if s["name"] == name:
                s["uses"] += 1
                with open(md, "w", encoding="utf-8") as f:
                    f.write(f"---\nname: {s['name']}\ndescription: {s['description']}\n"
                            f"uses: {s['uses']}\n---\n\n{s['content']}\n")
                return


def all_skills() -> list:
    out = []
    if os.path.isdir(SKILLS_DIR):
        for d in sorted(os.listdir(SKILLS_DIR)):
            md = os.path.join(SKILLS_DIR, d, "SKILL.md")
            if os.path.exists(md):
                out.append(_parse(md))
    return out
