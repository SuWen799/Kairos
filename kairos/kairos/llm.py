"""LLM 客户端：OpenAI 兼容接口，多模型注册表（v2.0 R4）。
model 参数语义 = config models[].name（如 "DeepSeek Flash"），
按名字解析独立 client（懒建），未知名字回落默认模型。
v2.6：动态读 config.MODELS（配置向导写完热重载即生效）。"""
import json

from openai import OpenAI

from . import config

_clients: dict[str, OpenAI] = {}


def _usage_of(resp) -> dict | None:
    """从响应/分片里抓 usage（OpenAI 兼容字段，拿不到就 None）。"""
    u = getattr(resp, "usage", None)
    if u is None:
        return None
    return {"prompt_tokens": getattr(u, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(u, "completion_tokens", 0) or 0}


def _resolve(model_name: str | None) -> tuple[OpenAI, str, str]:
    """返回 (client, model_id, 注册名)。未知名字回落默认。"""
    models = config.MODELS
    name = model_name or config.DEFAULT_MODEL
    cfg = next((m for m in models if m["name"] == name), None)
    if cfg is None:
        name = config.DEFAULT_MODEL
        cfg = next((m for m in models if m["name"] == name), models[0])
    if name not in _clients:
        _clients[name] = OpenAI(base_url=cfg["base_url"],
                                api_key=cfg["api_key"] or "EMPTY")
    return _clients[name], cfg["model"], name


def chat(messages: list, tools: list | None = None, temperature: float = 0.3,
         model: str | None = None) -> dict:
    """返回 {"content": str, "tool_calls": [...], "usage": {...}|None}。model=注册名。"""
    client, model_id, _ = _resolve(model)
    kwargs = {"model": model_id, "messages": messages, "temperature": temperature}
    if tools:
        kwargs["tools"] = tools
    resp = client.chat.completions.create(**kwargs)
    msg = resp.choices[0].message
    tool_calls = []
    if getattr(msg, "tool_calls", None):
        for tc in msg.tool_calls:
            tool_calls.append(
                {"id": tc.id, "name": tc.function.name,
                 "arguments": json.loads(tc.function.arguments or "{}")}
            )
    return {"content": msg.content or "", "tool_calls": tool_calls,
            "usage": _usage_of(resp)}


def chat_stream(messages: list, tools: list | None = None, temperature: float = 0.3,
                model: str | None = None, on_delta=None) -> dict:
    """流式版 chat（M2 补课：message.delta）。on_delta(chunk) 逐段回调正文增量，
    返回 {"content", "tool_calls", "usage"}（usage 来自 include_usage 尾片，拿不到为 None）。
    任何异常回落非流式 chat()，保证主循环永不因流式挂掉。"""
    try:
        client, model_id, _ = _resolve(model)
        kwargs = {"model": model_id, "messages": messages,
                  "temperature": temperature, "stream": True}
        if tools:
            kwargs["tools"] = tools
        try:
            # OpenAI/DeepSeek 支持 include_usage：usage 走最后一个空 choices 分片
            stream = client.chat.completions.create(
                **kwargs, stream_options={"include_usage": True})
        except Exception:
            stream = client.chat.completions.create(**kwargs)  # 不认 stream_options 的服务商
        content_parts: list[str] = []
        usage = None
        # tool_calls 按 index 聚合（流式分片：id/name 首片到，arguments 逐片追加）
        tc_acc: dict[int, dict] = {}
        for chunk in stream:
            u = _usage_of(chunk)
            if u is not None:
                usage = u
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            piece = getattr(delta, "content", None)
            if piece:
                content_parts.append(piece)
                if on_delta:
                    try:
                        on_delta(piece)
                    except Exception:
                        pass
            for tcd in (getattr(delta, "tool_calls", None) or []):
                acc = tc_acc.setdefault(tcd.index, {"id": "", "name": "", "arguments": ""})
                if tcd.id:
                    acc["id"] = tcd.id
                fn = tcd.function
                if fn is not None:
                    if fn.name:
                        acc["name"] = fn.name
                    if fn.arguments:
                        acc["arguments"] += fn.arguments
        tool_calls = [{"id": a["id"], "name": a["name"],
                       "arguments": json.loads(a["arguments"] or "{}")}
                      for _, a in sorted(tc_acc.items())]
        return {"content": "".join(content_parts), "tool_calls": tool_calls,
                "usage": usage}
    except Exception:
        if on_delta is not None:
            # 回落前关掉增量回调，避免调用方收到两份正文
            on_delta = None
        out = chat(messages, tools=tools, temperature=temperature, model=model)
        return {"content": out["content"], "tool_calls": out["tool_calls"],
                "usage": out.get("usage")}


def chat_text(system: str, user: str, temperature: float = 0.3) -> str:
    """单轮纯文本调用（记忆提取 / Curriculum / Verifier judge 等内部角色用）。"""
    out = chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=temperature,
    )
    return out["content"]
