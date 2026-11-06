# -*- coding: utf-8 -*-
"""启动 DeerFlow Gateway（方案 C 集成的研究引擎）。

用法：KAIROS venv 或任意 python 运行：
  python scripts/start_deerflow.py            # 前台常驻（建议 run_in_background 场景用 exec）
端口：127.0.0.1:8001（Kairos tool_deep_research 的调用地址）
配置：参考项目/deer-flow/config.yaml（DeepSeek + DuckDuckGo，key 已从 Kairos keyring 注入）
"""
import os
import subprocess
import sys

DEERFLOW_BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))),
    "参考项目", "deer-flow", "backend")
DEERFLOW_PYTHON = r"C:/Users/<user>\.workbuddy\binaries\python\envs\deerflow\Scripts\python.exe"


def main() -> int:
    backend = os.path.normpath(DEERFLOW_BACKEND)
    if not os.path.isdir(backend):
        print(f"[deerflow] backend 目录不存在：{backend}")
        return 1
    env = {
        **os.environ,
        "PYTHONPATH": backend,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "DEER_FLOW_DATE_TIMEZONE": "Asia/Shanghai",
        "DEER_FLOW_CONFIG_PATH": os.path.join(backend, "..", "config.yaml"),
        # 本地研究引擎模式：网关仅绑 127.0.0.1，关 auth 走默认用户
        "DEER_FLOW_AUTH_DISABLED": "1",
    }
    print(f"[deerflow] starting gateway at {backend} (port 8001)")
    return subprocess.call([DEERFLOW_PYTHON, "-m", "uvicorn", "app.gateway.app:app",
                            "--host", "127.0.0.1", "--port", "8001"],
                           cwd=backend, env=env)


if __name__ == "__main__":
    sys.exit(main())
