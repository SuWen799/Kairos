"""ULID 主键生成：时间有序 + 去中心 + 类型前缀（Stripe 风格）。
需求文档 12.2：不用自增（多端离线生成/暴露量级），不用 UUIDv4（索引膨胀）。"""
from ulid import new as _ulid_new

PREFIXES = {
    "user": "usr", "session": "sess", "message": "msg", "run": "run",
    "event": "evt", "task": "task", "confirmation": "cfm", "memory": "mem",
    "skill": "skl", "kb_doc": "kbd", "kb_chunk": "kbc", "source": "src",
    "application": "app", "watch": "wt", "automation": "auto",
    "channel_binding": "chn", "budget": "bud", "checkpoint": "ckpt",
    "audit": "aud", "failure": "fail", "idem": "idem",
    "mandate": "mdl", "ticket": "tkt", "trigger": "trg",
}

LOCAL_USER_ID = "usr_local"  # Phase A 单用户恒定值（多租户地基，需求 14.8）


def new_id(kind: str) -> str:
    """按实体类型生成带前缀 ULID，如 new_id('session') -> 'sess_01J...'"""
    prefix = PREFIXES.get(kind)
    if prefix is None:
        raise KeyError(f"unknown id kind: {kind}（允许：{sorted(PREFIXES)}）")
    return f"{prefix}_{_ulid_new().str}"


def is_valid(entity_id: str, kind: str) -> bool:
    prefix = PREFIXES.get(kind, "")
    return isinstance(entity_id, str) and entity_id.startswith(prefix + "_")
