"""并行子代理编排器（v2.0 R7 / M7.2）：线程池 + 并发闸，对齐 DeerFlow 上限 3。
角色实现函数由 tools.tool_agent_run 注入（_one），本模块只管并发与事件转发。"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

_lock = threading.Lock()
_running = 0
MAX_CONCURRENT = 6  # 全局硬上限（settings.max_subagents 是单次 batch 上限）


def _slot():
    global _running
    with _lock:
        if _running >= MAX_CONCURRENT:
            return False
        _running += 1
        return True


def _unslot():
    global _running
    with _lock:
        _running -= 1


def run_batch(tasks: list, worker, *, emit=None, max_workers: int = 3) -> list:
    """并行执行 tasks（[{role,task},...]），worker(t)->dict。保持输入顺序返回。
    全局并发闸满时排队等待（不丢弃）。emit(dict) 转发排队/完成事件。"""
    max_workers = max(1, min(int(max_workers), MAX_CONCURRENT))

    def _wrapped(i: int, t: dict):
        while not _slot():
            import time
            time.sleep(0.2)
        try:
            if emit:
                try:
                    emit({"type": "subagent.start", "role": t.get("role", ""),
                          "seq": i, "task": (t.get("task") or "")[:80]})
                except Exception:
                    pass
            res = worker(t)
            res["seq"] = i
            if emit:
                try:
                    emit({"type": "subagent.done", "role": t.get("role", ""),
                          "seq": i, "ok": res.get("ok", True)})
                except Exception:
                    pass
            return res
        finally:
            _unslot()

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = [pool.submit(_wrapped, i, t) for i, t in enumerate(tasks)]
        results = [f.result() for f in futs]
    return results
