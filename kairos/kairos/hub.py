"""Agent 中调中枢（v2.6）：把外部 Agent CLI 变成 Kairos 可派发的执行器。
- CLI 型（claude/codex/pi…）：subprocess 受控执行，prompt 默认走 stdin（避开
  Windows 8191 字符命令行上限），超时受控，输出截断返回。
- manual 型（豆包/WorkBuddy 等 GUI 应用）：诚实标注"无 CLI 通道"，只登记不执行。
- 注册表 = 内置 preset + config.local.json "agents" 数组按 name 合并覆盖（用户可配）。
诚实边界：外部 CLI 的行为 Kairos 无法审计到步骤级，只留调用记录与输出。"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time

from .config import BASE_DIR, CFG

# install：CLI 型给一键安装命令。setup_hint：接入后怎么用（向导/设置里展示）。
# 诚实原则：注册表里只放"真执行器"——能被 subprocess 实际调起来的。
# 豆包不在这里：它走火山方舟 API 进模型注册表（配置向导选"火山方舟"），
# 是 Kairos 主循环可直接用的模型，比外挂派发更彻底。
PRESET_AGENTS = [
    {"name": "claude", "label": "Claude Code", "kind": "cli", "engine": "claude-sdk",
     "command": "claude", "args": ["-p"], "prompt_mode": "stdin",
     "install": "npm install -g @anthropic-ai/claude-code",
     "note": "Anthropic 官方 CLI（claude-agent-sdk 结构化对接，CLI 兜底）",
     "setup_hint": "安装后终端跑一次 claude 完成登录授权，Kairos 即可派活。用你自己的 Claude 订阅/Key 计费，无需在 Kairos 里配 Key。"},
    {"name": "codex", "label": "Codex CLI", "kind": "cli", "engine": "codex-json",
     "command": "codex", "args": ["exec", "--json", "-", "--skip-git-repo-check"], "prompt_mode": "stdin",
     "install": "npm install -g @openai/codex",
     "note": "OpenAI Codex CLI（exec --json JSONL 事件流；--skip-git-repo-check 免 git 目录信任确认）",
     "setup_hint": "已检测到本机安装。但未登录：终端跑一次 codex login 完成授权后 Kairos 才能派活（未登录时派发会挂起）。"},
    {"name": "codebuddy", "label": "CodeBuddy CLI", "kind": "cli",
     "command": "codebuddy", "args": ["-p", "--port", "64201"], "prompt_mode": "stdin",
     "install": "npm install -g @tencent-ai/codebuddy-code",
     "note": "腾讯云 CodeBuddy Code（-p 非交互；--port 64201 避开 WorkBuddy 同引擎端口冲突）",
     "setup_hint": "已安装。终端跑一次 codebuddy 完成登录（选国内站，浏览器授权），之后 Kairos 直接派活。"},
    {"name": "pi", "label": "Pi Agent", "kind": "cli",
     "command": "pi", "args": [], "prompt_mode": "stdin",
     "install": "npm install -g @badlogic/pi-monorepo  # 若包名不同以官方文档为准",
     "note": "pi CLI（可按实际安装改 config agents.command）",
     "setup_hint": "安装后在 config.local.json 的 agents 段核对 command/args。"},
    {"name": "harness", "label": "DeepSeek Harness", "kind": "custom",
     "command": "", "args": [], "prompt_mode": "stdin",
     "install": "",
     "note": "本地引擎：在 config.local.json agents 里配 command/args",
     "setup_hint": "进阶项：有本地 harness 可执行文件时，在 config.local.json agents 段填 name=harness、command=完整路径。没有就跳过。"},
]

_lock = threading.Lock()


def _load_registry() -> list[dict]:
    """内置 preset + config "agents" 按 name 合并（config 优先）。"""
    reg = {a["name"]: dict(a) for a in PRESET_AGENTS}
    for a in CFG.get("agents", []):
        name = (a.get("name") or "").strip()
        if not name:
            continue
        row = reg.setdefault(name, {"name": name, "kind": "cli",
                                    "args": [], "prompt_mode": "stdin",
                                    "label": name})
        row.update({k: v for k, v in a.items() if v is not None})
        reg[name] = row
    return list(reg.values())


def _resolve_cmd(agent: dict) -> str:
    """which 解析出完整路径（npm 全局命令是 .cmd shim）。"""
    cmd = agent.get("command") or ""
    if not cmd:
        return ""
    if any(ch in cmd for ch in "/\\"):
        return cmd
    return shutil.which(cmd) or cmd


def _available(agent: dict) -> bool:
    if agent.get("kind") == "manual":
        return True  # manual 型永远"可用"（复制粘贴通道）
    cmd = agent.get("command") or ""
    if not cmd:
        return False
    if any(ch in cmd for ch in "/\\") or cmd.endswith(".exe"):
        from pathlib import Path
        return Path(cmd).exists()
    return shutil.which(cmd) is not None


def list_agents() -> list[dict]:
    return [{**a, "available": _available(a)}
            for a in _load_registry()]


def _child_env() -> dict:
    """宿主环境可能带 ANTHROPIC_MODEL 等变量污染外部 CLI，过滤后传。"""
    import os
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC_")}
    env["PYTHONUTF8"] = "1"
    return env


def _run_claude_sdk(agent: dict, prompt: str, timeout: int) -> dict | None:
    """claude-agent-sdk 结构化通道：流式收 AssistantMessage 文本，聚合返回。
    SDK 未安装 / 起不动时返回 None → 上层回退 CLI。"""
    try:
        import anyio
        from claude_agent_sdk import ClaudeAgentOptions, query
        from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock
    except Exception:
        return None

    opts = ClaudeAgentOptions(
        permission_mode="bypassPermissions",
        env=_child_env(),
    )
    resolved = _resolve_cmd(agent)
    # .cmd/.bat shim 不能直接 CreateProcess（SDK 不经 cmd /c），让 SDK 自行发现 CLI
    if resolved and not resolved.lower().endswith((".cmd", ".bat")):
        opts.cli_path = resolved

    async def _collect():
        parts, notes = [], []
        async for msg in query(prompt=prompt, options=opts):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock) and block.text:
                        parts.append(block.text)
            elif isinstance(msg, ResultMessage):
                notes.append(f"subtype={msg.subtype} turns={msg.num_turns}")
        return "\n".join(p.strip() for p in parts if p.strip()), "; ".join(notes)

    t0 = time.time()
    try:
        with _lock:
            text, note = anyio.run(_collect)
        elapsed = round(time.time() - t0, 1)
        if not text:
            return None  # 无正文视为失败，回退 CLI（诚实优先）
        return {"ok": True, "elapsed": elapsed,
                "output": text[:8000], "note": f"sdk {note} {elapsed}s"}
    except Exception:  # noqa: BLE001
        # SDK 通道失败 → 返回 None，上层回退 CLI
        return None


def _run_codex_json(agent: dict, prompt: str, timeout: int) -> dict:
    """codex exec --json JSONL 事件流：聚合 agent_message；解析不出再原样返回。"""
    result = _run_cli(agent, prompt, timeout)
    if not result.get("ok"):
        return result
    out = result.get("output") or ""
    texts = []
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        # 新版事件：{"type":"item.completed","item":{"type":"agent_message","text":"..."}}
        item = ev.get("item") or {}
        if ev.get("type") == "item.completed" and item.get("type") == "agent_message":
            texts.append(item.get("text") or "")
        # 旧版事件：{"msg":{"type":"agent_message","message":"..."}}
        msg = ev.get("msg") or {}
        if msg.get("type") == "agent_message":
            texts.append(msg.get("message") or "")
    if texts:
        result["output"] = "\n".join(t.strip() for t in texts if t.strip())[:8000]
        result["note"] = f"{result.get('note','')} json-parsed"
    return result


def run_agent(name: str, prompt: str, timeout: int = 600) -> dict:
    """派发任务给外部 Agent。返回 {ok, output, elapsed, note}。"""
    agent = next((a for a in _load_registry() if a["name"] == name), None)
    if agent is None:
        return {"ok": False, "output": f"[未知 Agent {name}]", "elapsed": 0, "note": ""}
    if agent.get("kind") == "manual":
        return {"ok": False, "elapsed": 0, "output":
                f"[{agent['label']}] 是 GUI 应用，无 CLI 通道：请复制以下任务到{agent['label']}执行。\n{prompt[:2000]}",
                "note": "manual"}
    if not _available(agent):
        return {"ok": False, "elapsed": 0, "output":
                f"[{agent['label']}] 未检测到可执行文件（{agent.get('command')}）。请安装 CLI 或在 config.local.json agents 里修正 command。",
                "note": "not-found"}
    if not agent.get("enabled", True):
        return {"ok": False, "elapsed": 0, "output": f"[{agent['label']}] 已在设置里停用。", "note": "disabled"}

    engine = agent.get("engine") or ""
    if engine == "claude-sdk":
        sdk_result = _run_claude_sdk(agent, prompt, timeout)
        if sdk_result is not None:
            return sdk_result
    if engine == "codex-json":
        return _run_codex_json(agent, prompt, timeout)
    return _run_cli(agent, prompt, timeout)


def _run_cli(agent: dict, prompt: str, timeout: int) -> dict:
    resolved = _resolve_cmd(agent)
    argv = [resolved] + [a for a in (agent.get("args") or [])]
    # npm 全局命令是 .cmd shim：CreateProcess 不认，需经 cmd /c
    if resolved.lower().endswith((".cmd", ".bat")):
        argv = ["cmd", "/c", *argv]
    t0 = time.time()
    try:
        stdin_data = None
        if agent.get("prompt_mode", "stdin") == "stdin":
            stdin_data = prompt
        elif "{prompt}" in argv:
            argv[argv.index("{prompt}")] = prompt
        with _lock:  # 同一时刻只跑一个外部 CLI（防资源打架）
            child_env = _child_env()
            r = subprocess.run(
                argv, input=stdin_data, capture_output=True,
                timeout=max(10, min(int(timeout), 1800)),
                encoding="utf-8", errors="replace", env=child_env)
        out = (r.stdout or "")
        if r.stderr:
            out += (f"\n[stderr] {r.stderr[-600:]}")
        elapsed = round(time.time() - t0, 1)
        note = f"exit={r.returncode} {elapsed}s"
        return {"ok": r.returncode == 0, "elapsed": elapsed,
                "output": (out or "(无输出)")[:8000], "note": note}
    except subprocess.TimeoutExpired:
        return {"ok": False, "elapsed": round(time.time() - t0, 1),
                "output": f"[超时] {agent['label']} 执行超过 {timeout}s 被终止", "note": "timeout"}
    except FileNotFoundError as e:
        return {"ok": False, "elapsed": 0, "output": f"[启动失败] {e}", "note": "spawn-error"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "elapsed": round(time.time() - t0, 1),
                "output": f"[执行异常] {e}", "note": "error"}
