# -*- coding: utf-8 -*-
"""M5：PyInstaller 打包 sidecar → desktop/resources/backend/（onedir，启动快）。

用法（在 venv 里）：
  C:/Users/<user>/.workbuddy/binaries/python/envs/kairos/Scripts/python.exe scripts/build_backend.py

产物：kairos/dist/backend/backend.exe
Electron 发布态自动拉 process.resourcesPath/backend/backend.exe（electron-builder.yml extraResources 已配）。
"""
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV = sys.executable

def main() -> int:
    add_data = [
        # 数据随包：参考素材/技能/提示词资产按需追加
    ]
    cmd = [
        VENV, "-m", "PyInstaller",
        "--name", "backend",
        "--noconfirm",
        "--onedir",                      # onedir：启动快、杀进程干净（设计 11.6）
        "--distpath", os.path.join(BASE, "dist"),
        "--workpath", os.path.join(BASE, "build"),
        "--specpath", BASE,
        "--hidden-import", "kairos.api",
        "--hidden-import", "kairos.channels.feishu",
        "--hidden-import", "kairos.runtime.tasks",
        "--collect-submodules", "kairos",
        os.path.join(BASE, "run.py"),
    ]
    print(" ".join(cmd))
    return subprocess.call(cmd)


if __name__ == "__main__":
    sys.exit(main())
