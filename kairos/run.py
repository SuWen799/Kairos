"""启动入口：python run.py

LAN 模式（M3，手机/PWA 遥控）：KAIROS_LAN=1 时绑定 0.0.0.0，
生成一次性 token 落 data/lan_token.txt，API 全部要求 Bearer（/health 与静态资源豁免）。
"""
import os
import secrets
import socket

import uvicorn

from kairos.config import DATA_DIR, SERVER


def _lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main():
    lan = os.environ.get("KAIROS_LAN") == "1"
    host = "0.0.0.0" if lan else SERVER["host"]
    if lan:
        token = os.environ.get("KAIROS_TOKEN") or f"krs_{secrets.token_urlsafe(24)}"
        os.environ["KAIROS_TOKEN"] = token  # server 中间件读取
        path = os.path.join(DATA_DIR, "lan_token.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"http://{_lan_ip()}:{SERVER['port']}/app/\nBearer {token}\n")
        print(f"[lan] 手机访问：http://{_lan_ip()}:{SERVER['port']}/app/  首页输入 token（见 {path}）")
    uvicorn.run("kairos.server:app", host=host, port=SERVER["port"], log_level="info")


if __name__ == "__main__":
    main()
