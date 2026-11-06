# -*- coding: utf-8 -*-
"""一次性迁移：config.local.json 的明文 api_key → 系统凭据管理器。

- 先备份 config.local.json → config.local.json.bak-<ts>
- keyring 写入成功且读回一致 → 原 api_key 置空（提示迁移完成）
- keyring 不可用（异常）→ 不动原文件，退出码 1

用法：venv python scripts/migrate_key_to_keyring.py
"""
import json
import os
import shutil
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

CFG_PATH = os.path.join(BASE, "config.local.json")


def main() -> int:
    from kairos.credentials import get_secret, set_secret

    if not os.path.exists(CFG_PATH):
        print("config.local.json 不存在，无需迁移")
        return 0

    with open(CFG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    key = (cfg.get("llm") or {}).get("api_key", "")
    if not key:
        print("config.local.json 无 api_key（可能已迁移过）")
        return 0

    try:
        set_secret("llm.api_key", key)
    except Exception as e:
        print(f"[FAIL] keyring 写入失败，原文件未动: {e}")
        return 1

    back = get_secret("llm.api_key")
    if back != key:
        print("[FAIL] keyring 读回不一致，原文件未动")
        return 1

    bak = f"{CFG_PATH}.bak-{time.strftime('%Y%m%d%H%M%S')}"
    shutil.copy2(CFG_PATH, bak)

    cfg["llm"]["api_key"] = ""  # 迁走后置空（keyring 为唯一来源）
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    print(f"[OK] api_key 已入 keyring（服务=Kairos 账号=llm.api_key）")
    print(f"[OK] 原文件已置空，备份在 {bak}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
