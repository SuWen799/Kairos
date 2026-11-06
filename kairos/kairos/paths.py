# -*- coding: utf-8 -*-
"""用户级目录分桶（需求 14.4 多租户地基）：所有用户产物落 data/users/<user_id>/ 下。

M1 单用户期恒 usr_local；多租户期换 user_id 即天然隔离。
现有 global 目录（kb/、workspace/ 等）不动，新产物一律走本模块。
"""
from __future__ import annotations

import os

from kairos.config import DATA_DIR
from kairos.storage.ulid import LOCAL_USER_ID

USERS_ROOT = os.path.join(DATA_DIR, "users")


def user_dir(user_id: str = LOCAL_USER_ID, *parts: str) -> str:
    """取（并确保存在）某用户的分桶目录，如 user_dir(artifacts="kb") -> data/users/usr_local/kb"""
    d = os.path.join(USERS_ROOT, user_id, *parts)
    os.makedirs(d, exist_ok=True)
    return d


# 常用桶约定（设计 14.4）：artifacts 工具产物 / uploads 用户上传 / workspace 工作区文件
def artifacts_dir(user_id: str = LOCAL_USER_ID) -> str:
    return user_dir(user_id, "artifacts")


def uploads_dir(user_id: str = LOCAL_USER_ID) -> str:
    return user_dir(user_id, "uploads")
