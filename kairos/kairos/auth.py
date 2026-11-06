# -*- coding: utf-8 -*-
"""M6 多用户地基（需求第 14 章）：注册/登录（Phase B 云端形态的脚手架）。

M1 桌面单用户不受影响：KAIROS_MULTIUSER 未开启时端点不可用。
密码 PBKDF2-SHA256（10 万轮）；LLM key 依旧只进 keyring，永不入库。
"""
from __future__ import annotations

import hashlib
import os
import secrets

from kairos.storage.conn import get_engine
from kairos.storage.ulid import new_id

_MULTIUSER = os.environ.get("KAIROS_MULTIUSER") == "1"
_ROUNDS = 100_000


def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _ROUNDS).hex()


def register(email: str, password: str, display_name: str = "") -> dict:
    from sqlalchemy import select
    from sqlalchemy.orm import Session as SASession
    from kairos.storage.models import User
    if not _MULTIUSER:
        raise PermissionError("多用户模式未开启（KAIROS_MULTIUSER=1）")
    with SASession(get_engine(), expire_on_commit=False) as s:
        exists = s.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if exists:
            raise ValueError("email 已注册")
        salt = secrets.token_hex(16)
        row = User(id=new_id("user"), email=email,
                   display_name=display_name or email.split("@")[0],
                   settings_json=f'{{"salt":"{salt}","pw_hash":"{_hash(password, salt)}"}}',
                   created_at=_ts(), updated_at=_ts())
        s.add(row)
        s.commit()
        return {"id": row.id, "email": row.email}


def verify(email: str, password: str) -> str | None:
    """成功返回 user_id（session token 层 M6.2 接 JWT/opaque token）。"""
    import json as _json
    from sqlalchemy import select
    from sqlalchemy.orm import Session as SASession
    from kairos.storage.models import User
    with SASession(get_engine(), expire_on_commit=False) as s:
        row = s.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if row is None:
            return None
        cfg = _json.loads(row.settings_json or "{}")
        salt, expect = cfg.get("salt"), cfg.get("pw_hash")
        if salt and expect and secrets.compare_digest(_hash(password, salt), expect):
            return row.id
    return None


def _ts() -> str:
    from kairos.storage.models import utcnow
    return utcnow()


# ── M6.2：session token（紧凑 JWT，HMAC-SHA256，stdlib 实现零新依赖）──
_TTL_HOURS = 24 * 7


def _secret() -> bytes:
    """token 签名密钥：data/auth_secret 自动生成（0600），永不入库不入 git。"""
    path = os.path.join(_data_dir(), "auth_secret")
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="ascii") as f:
            f.write(secrets.token_hex(32))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    with open(path, "r", encoding="ascii") as f:
        return bytes.fromhex(f.read().strip())


def _data_dir() -> str:
    from .config import DATA_DIR
    return DATA_DIR


def _b64url(data: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_dec(s: str) -> bytes:
    import base64
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue_token(user_id: str, ttl_hours: int = _TTL_HOURS) -> str:
    """登录成功后签发 session token：header.payload.signature（JWT HS256）。"""
    import json as _json
    import time as _time
    header = _b64url(_json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url(_json.dumps({
        "sub": user_id,
        "iat": int(_time.time()),
        "exp": int(_time.time()) + ttl_hours * 3600,
    }).encode())
    import hmac
    sig = _b64url(hmac.new(_secret(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
    return f"{header}.{payload}.{sig}"


def verify_token(token: str) -> str | None:
    """校验通过返回 user_id，否则 None（过期/篡改/格式错一律 None）。"""
    import json as _json
    import hmac
    import time as _time
    try:
        header, payload, sig = token.split(".")
        expect = hmac.new(_secret(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
        if not secrets.compare_digest(_b64url_dec(sig), expect):
            return None
        claims = _json.loads(_b64url_dec(payload))
        if claims.get("exp", 0) < _time.time():
            return None
        return claims.get("sub")
    except Exception:
        return None
