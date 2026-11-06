# -*- coding: utf-8 -*-
"""简化 cron（5 段：分 时 日 月 周）+ 随机抖动（需求 11.5 错峰）。

支持 * / 数字 / 列表(,) / 步进(*/n)。够 automations v1 用。
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta


def _field_match(value: int, field: str, lo: int, hi: int) -> bool:
    if field == "*":
        return True
    for part in field.split(","):
        part = part.strip()
        if "/" in part:
            base, step = part.split("/", 1)
            step = int(step)
            start = lo if base in ("*", "") else int(base)
            if value >= start and (value - start) % step == 0:
                return True
        elif "-" in part:
            a, b = part.split("-", 1)
            if int(a) <= value <= int(b):
                return True
        elif int(part) == value:
            return True
    return False


def matches(cron: str, dt: datetime) -> bool:
    try:
        minute, hour, day, month, weekday = cron.split()
    except ValueError:
        return False
    return (_field_match(dt.minute, minute, 0, 59)
            and _field_match(dt.hour, hour, 0, 23)
            and _field_match(dt.day, day, 1, 31)
            and _field_match(dt.month, month, 1, 12)
            and _field_match(dt.isoweekday() % 7, weekday, 0, 6))


def next_fire(cron: str, after: datetime | None = None,
              jitter_seconds: int = 120) -> datetime | None:
    """下一次触发时间（扫描未来 366 天；命中后加随机抖动错峰）。"""
    base = (after or datetime.now()).replace(second=0, microsecond=0) + timedelta(minutes=1)
    for i in range(366 * 24 * 60):  # 逐分钟扫描上限一年
        t = base + timedelta(minutes=i)
        if matches(cron, t):
            return t + timedelta(seconds=random.randint(0, jitter_seconds))
    return None
