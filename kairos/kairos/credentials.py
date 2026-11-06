# -*- coding: utf-8 -*-
"""凭据层（需求 9.1）：LLM key 不落盘，进系统凭据管理器（Windows Credential Manager）。

服务名 SERVICE = "Kairos"；账号约定：
  - "llm.api_key"   ：默认 LLM key
  - "llm.api_key.<model>"：分模型 key（预留）
config.local.json 里的 api_key 仅作首次迁移来源，迁走后置空。
"""
from __future__ import annotations

SERVICE = "Kairos"


def set_secret(account: str, value: str) -> None:
    import keyring
    keyring.set_password(SERVICE, account, value)


def get_secret(account: str) -> str | None:
    import keyring
    try:
        return keyring.get_password(SERVICE, account)
    except Exception:
        return None  # 无凭据/后端不可用时静默（Phase A 单机）


def delete_secret(account: str) -> None:
    import keyring
    try:
        keyring.delete_password(SERVICE, account)
    except Exception:
        pass
