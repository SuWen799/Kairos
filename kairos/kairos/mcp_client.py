"""MCP (Model Context Protocol) 客户端桥接层。

把外部 MCP server 的工具动态桥接进本 Agent 的工具集：
  mcp.json 里配一个 server → 启动时 initialize + tools/list →
  每个远端工具变成 mcp_<server>_<tool> 加入 TOOLS_SPEC/TOOL_IMPL。
微信 / QQ / 飞书等开源 MCP server 实现填进配置即自动接入，无需改代码。

协议：JSON-RPC 2.0 over stdio（MCP 2024-11-05 规范的最小实现）。
"""
import json
import os
import threading

from .config import BASE_DIR, DATA_DIR

# 打包后 BASE_DIR 只读 → mcp.json 统一落 DATA_DIR（可写）；开发态老位置自动迁移
CONFIG_PATH = os.path.join(DATA_DIR, "mcp.json")

# 运行时状态：{server_name: {"client": McpStdioClient, "tools": [...], "error": str|None}}
_REGISTRY: dict = {}
_LOCK = threading.Lock()


class McpStdioClient:
    """最小 MCP stdio 客户端：initialize → tools/list → tools/call。"""

    def __init__(self, name: str, command: str, args: list | None = None, env: dict | None = None):
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.proc = None
        self._id = 0
        self._lock = threading.Lock()

    def _send(self, obj: dict):
        self.proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def _recv(self, want_id: int, timeout: float = 30.0):
        """读行直到拿到匹配 id 的响应（跳过通知）。"""
        import time
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError(f"[{self.name}] server 关闭了 stdout")
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == want_id:
                if "error" in msg:
                    raise RuntimeError(f"[{self.name}] {msg['error']}")
                return msg.get("result", {})
        raise TimeoutError(f"[{self.name}] 响应超时({timeout}s)")

    def _rpc(self, method: str, params: dict, timeout: float = 30.0) -> dict:
        with self._lock:
            self._id += 1
            rid = self._id
            self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
            return self._recv(rid, timeout)

    def start(self) -> dict:
        """启动进程并完成 MCP 握手，返回 tools/list。"""
        import os as _os
        import shutil as _shutil
        # Windows 坑：npx/uvx 等是 .cmd 脚本，Popen 直接调会 WinError 2，
        # 用 shutil.which 解析出真实可执行路径（找不到就诚实报错）
        cmd = self.command
        resolved = _shutil.which(cmd)
        if resolved:
            cmd = resolved
        else:
            if _os.name == "nt" and not _os.path.splitext(cmd)[1]:
                alt = _shutil.which(cmd + ".cmd") or _shutil.which(cmd + ".exe") or _shutil.which(cmd + ".bat")
                if alt:
                    cmd = alt
                else:
                    raise RuntimeError(f"命令找不到：{cmd}（PATH 里没有，也没找到 .cmd/.exe 变体）")
        env = {**_os.environ, **self.env, "PYTHONUTF8": "1"}
        self.proc = __import__("subprocess").Popen(
            [cmd, *self.args],
            stdin=__import__("subprocess").PIPE, stdout=__import__("subprocess").PIPE,
            stderr=__import__("subprocess").DEVNULL, text=True, encoding="utf-8", env=env)
        init = self._rpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "EvoAgent", "version": "0.4"},
        }, timeout=20)
        # initialized 通知（无 id，不等待响应）
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        tools = self._rpc("tools/list", {}, timeout=20).get("tools", [])
        return {"serverInfo": init.get("serverInfo", {}), "tools": tools}

    def call(self, tool: str, arguments: dict, timeout: float = 120.0) -> str:
        res = self._rpc("tools/call", {"name": tool, "arguments": arguments}, timeout=timeout)
        parts = res.get("content", [])
        texts = [p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "text"]
        return "\n".join(texts) or json.dumps(res, ensure_ascii=False)

    def stop(self):
        if self.proc:
            try:
                self.proc.kill()
            except Exception:
                pass
            self.proc = None


def _load_config() -> list:
    """读 mcp.json；首次从老位置/BASE_DIR 迁移，再不行从 example 拷骨架。"""
    if not os.path.exists(CONFIG_PATH):
        import shutil
        legacy = os.path.join(BASE_DIR, "mcp.json")
        if os.path.exists(legacy):
            shutil.copy(legacy, CONFIG_PATH)  # 老配置迁移，dev 无感
    if not os.path.exists(CONFIG_PATH):
        example = os.path.join(BASE_DIR, "mcp.example.json")
        if os.path.exists(example):
            import shutil
            shutil.copy(example, CONFIG_PATH)
    if not os.path.exists(CONFIG_PATH):
        return []
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f).get("servers", [])
    except Exception:
        return []


def refresh() -> dict:
    """（重）连接所有 enabled 的 MCP server，桥接其工具。返回汇总状态。"""
    status = {}
    with _LOCK:
        # 先停掉旧的
        for name, entry in _REGISTRY.items():
            entry["client"].stop()
        _REGISTRY.clear()
        for cfg in _load_config():
            name = cfg.get("name", "")
            if not name or not cfg.get("enabled"):
                continue
            entry = {"tools": [], "error": None, "server_info": {}}
            try:
                cli = McpStdioClient(name, cfg["command"], cfg.get("args"), cfg.get("env"))
                info = cli.start()
                entry["client"] = cli
                entry["tools"] = info["tools"]
                entry["server_info"] = info["serverInfo"]
                # 桥接进全局工具表
                from . import tools
                for t in info["tools"]:
                    full = f"mcp_{name}_{t['name']}"
                    schema = t.get("inputSchema", {"type": "object", "properties": {}})
                    spec = {"type": "function", "function": {
                        "name": full,
                        "description": f"[MCP:{name}] {t.get('description', t['name'])}",
                        "parameters": schema}}
                    tools.TOOLS_SPEC = [s for s in tools.TOOLS_SPEC if s["function"]["name"] != full]
                    tools.TOOLS_SPEC.append(spec)
                    tools.TOOL_IMPL[full] = (lambda a, c=cli, tn=t["name"]: c.call(tn, a))
            except Exception as e:
                entry["error"] = str(e)
                try:
                    cli.stop()
                except Exception:
                    pass
            # 原始 bug 修复：entry 从未写入 _REGISTRY，get_status 永远空、UI 永远"连接中"
            if entry.get("client") is not None:
                _REGISTRY[name] = entry
            status[name] = {k: v for k, v in entry.items() if k != "client"}
            if "client" in entry:
                status[name]["connected"] = True
        # 记录 disabled/未配置的
        for cfg in _load_config():
            name = cfg.get("name", "")
            if name and name not in status:
                status[name] = {"connected": False, "tools": [], "error": None,
                                "disabled": not cfg.get("enabled")}
    return status


def get_status() -> dict:
    """只读快照：连着的给实时状态，没连的按配置给 disabled/未连接。
    绝不在这里同步 refresh（npx 首跑拉包以分钟计，会卡死 HTTP 请求）。"""
    out = {}
    for cfg in _load_config():
        name = cfg.get("name", "")
        entry = _REGISTRY.get(name)
        if entry:
            out[name] = {"connected": True, "tools": entry["tools"],
                         "server_info": entry["server_info"], "error": entry["error"]}
        else:
            out[name] = {"connected": False, "tools": [], "error": None,
                         "disabled": not cfg.get("enabled"),
                         "connecting": bool(cfg.get("enabled"))}
    return out


# ---- 能力库管理（OpenDot 式）：UI 直接增删改 mcp.json + 热刷新 ----

def _save_config(servers: list) -> None:
    doc = {}
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:
        doc = {}
    doc["servers"] = servers
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)


def add_server(name: str, command: str, args: list | None = None,
               env: dict | None = None, enabled: bool = True) -> dict:
    name = (name or "").strip()
    if not name or not (command or "").strip():
        return {"ok": False, "msg": "name 和 command 必填"}
    servers = _load_config()
    if any(s.get("name") == name for s in servers):
        return {"ok": False, "msg": f"同名 server 已存在：{name}"}
    servers.append({"name": name, "command": command.strip(),
                    "args": args or [], "env": env or {}, "enabled": bool(enabled)})
    _save_config(servers)
    if enabled:
        _refresh_async()  # npx 首跑要拉包，绝不能卡 HTTP 请求线程
    return {"ok": True, "msg": f"已添加 {name}" + ("，后台连接中（点刷新状态查看）" if enabled else "（未启用）")}


def toggle_server(name: str) -> dict:
    servers = _load_config()
    for s in servers:
        if s.get("name") == name:
            s["enabled"] = not s.get("enabled")
            _save_config(servers)
            if s["enabled"]:
                _refresh_async()
            else:
                refresh()  # 停用走全量刷新，同步摘工具（本地 kill，快）
            return {"ok": True, "enabled": s["enabled"]}
    return {"ok": False, "msg": f"未找到：{name}"}


def _refresh_async() -> None:
    threading.Thread(target=refresh, daemon=True).start()


def remove_server(name: str) -> dict:
    servers = _load_config()
    left = [s for s in servers if s.get("name") != name]
    if len(left) == len(servers):
        return {"ok": False, "msg": f"未找到：{name}"}
    _save_config(left)
    with _LOCK:
        entry = _REGISTRY.pop(name, None)
        if entry:
            entry["client"].stop()
        # 摘掉已桥接的工具
        from . import tools
        tools.TOOLS_SPEC = [s for s in tools.TOOLS_SPEC
                            if not s["function"]["name"].startswith(f"mcp_{name}_")]
        for k in [k for k in tools.TOOL_IMPL if k.startswith(f"mcp_{name}_")]:
            tools.TOOL_IMPL.pop(k, None)
    return {"ok": True}


def masked_servers() -> list:
    """给 UI 的配置列表：env 的值打码（OpenDot：密钥写入后不再显示）。"""
    out = []
    for s in _load_config():
        out.append({"name": s.get("name", ""), "command": s.get("command", ""),
                    "args": s.get("args", []), "enabled": bool(s.get("enabled")),
                    "env_keys": sorted((s.get("env") or {}).keys()),
                    "has_env": bool(s.get("env"))})
    return out
