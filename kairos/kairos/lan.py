# -*- coding: utf-8 -*-
"""LAN 手机遥控（M3 重构版）：状态落 data/lan.json，运行时可开关，无需重启。

设计对齐 OpenDot「设备节点默认关闭 + 配对码」：
- 默认关闭：sidecar 虽绑 0.0.0.0，但中间件对非 loopback 一律 403；
- 开启后：远端请求需带配对 token（?token= 或 Bearer）；
- token 持久化 data/lan.json，同步落 lan_token.txt 供手抄；
- 开关/换 token 仅限本机（loopback）操作，远端无权。
"""
from __future__ import annotations

import json
import os
import secrets
import socket

from .config import DATA_DIR

LAN_PATH = os.path.join(DATA_DIR, "lan.json")
TXT_PATH = os.path.join(DATA_DIR, "lan_token.txt")


def _load() -> dict:
    try:
        with open(LAN_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
            if isinstance(d, dict):
                return d
    except Exception:
        pass
    return {}


def _save(d: dict) -> None:
    with open(LAN_PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def enabled() -> bool:
    return bool(_load().get("enabled"))


def token() -> str:
    """取持久 token；没有则生成（惰性，首次开启时落盘）。"""
    d = _load()
    t = d.get("token")
    if t:
        return t
    t = f"krs_{secrets.token_urlsafe(24)}"
    d["token"] = t
    _save(d)
    _write_txt(t)
    return t


def set_enabled(on: bool) -> dict:
    d = _load()
    d["enabled"] = bool(on)
    if on:
        d["token"] = token()  # 保证 token 已生成落盘
    _save(d)
    return d


def rotate() -> str:
    """换配对 token（旧设备立即失效）。"""
    d = _load()
    t = f"krs_{secrets.token_urlsafe(24)}"
    d["token"] = t
    _save(d)
    _write_txt(t)
    return t


def _write_txt(t: str) -> None:
    try:
        urls = client_urls()
        line = "\n".join([u + f"?token={t}" for u in urls])
        with open(TXT_PATH, "w", encoding="utf-8") as f:
            f.write(line + f"\nBearer {t}\n")
    except Exception:
        pass


def lan_ips() -> list[str]:
    """本机所有局域网 IPv4（去环回）。"""
    ips = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    primary = _primary_ip()
    if primary and primary not in ips:
        ips.insert(0, primary)
    return ips


def _primary_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("223.5.5.5", 80))  # 阿里 DNS：不发包，仅定源地址
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return ""


def client_urls() -> list[str]:
    """手机可访问的入口 URL（不含 token）。"""
    from .config import SERVER
    port = SERVER["port"]
    return [f"http://{ip}:{port}/app/" for ip in lan_ips()]


def is_loopback(client_host: str | None) -> bool:
    if not client_host:
        return True  # 测试环境/无 client 信息时按本机处理
    return client_host in ("127.0.0.1", "::1", "localhost") or client_host.startswith("127.")


def status(include_secret: bool) -> dict:
    """include_secret=False 时抹掉 token（远端不可见）。"""
    d = _load()
    t = d.get("token", "")
    return {
        "enabled": bool(d.get("enabled")),
        "token": t if include_secret else "",
        "has_token": bool(t),
        "urls": client_urls(),
        "ips": lan_ips(),
        "port": __import__("kairos.config", fromlist=["SERVER"]).SERVER["port"],
    }
