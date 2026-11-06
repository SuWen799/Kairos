# -*- coding: utf-8 -*-
"""飞书通道（P0，需求 6.2）：lark-oapi WebSocket 长连接——免公网 IP。

凭据：config.local.json → channels.feishu.app_id / app_secret。
事件：im.message.receive_v1 → manager.handle_inbound → 回信。
M4.1：HITL 交互卡片——send_card 发送审批卡（批准/拒绝按钮），
按钮回调经同一 WS 长连接 register_p2_card_action_trigger 收取，
直接路由 manager._decide 落库，toast 回执。
"""
from __future__ import annotations

import json

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    P2ImMessageReceiveV1, ReplyMessageRequest, ReplyMessageRequestBody,
    CreateMessageRequest, CreateMessageRequestBody,
)
from lark_oapi.event.callback.model.p2_card_action_trigger import (
    P2CardActionTrigger, P2CardActionTriggerResponse, CallBackToast,
)

from . import manager


class FeishuChannel:
    name = "feishu"

    def __init__(self, cfg: dict):
        self.app_id = cfg["app_id"]
        self.app_secret = cfg["app_secret"]
        self._client = lark.Client.builder() \
            .app_id(self.app_id).app_secret(self.app_secret).build()

    # ── 长连接 ─────────────────────────────────────────
    def start(self) -> None:
        handler = lark.EventDispatcherHandler.builder("", "") \
            .register_p2_im_message_receive_v1(self._on_message) \
            .register_p2_card_action_trigger(self._on_card_action).build()
        # ws 长连接：阻塞在内部循环，manager 用线程拉起
        lark.ws.Client(self.app_id, self.app_secret,
                       event_handler=handler, log_level=lark.LogLevel.WARN).start()

    # ── 入站消息 ───────────────────────────────────────
    def _on_message(self, data: P2ImMessageReceiveV1) -> None:
        try:
            msg = data.event.message
            if msg.message_type != "text":
                self._reply(msg.message_id, "（暂只支持文本）")
                return
            text = json.loads(msg.content).get("text", "")
            sender = data.event.sender.sender_id.open_id
            manager.handle_inbound("feishu", sender, text,
                                   lambda t: self._reply(msg.message_id, t))
        except Exception as e:  # noqa: BLE001
            try:
                self._reply(data.event.message.message_id, f"[通道异常] {e}")
            except Exception:
                pass

    # ── 卡片按钮回调（M4.1）────────────────────────────
    def _on_card_action(self, data: P2CardActionTrigger) -> P2CardActionTriggerResponse:
        resp = P2CardActionTriggerResponse()
        try:
            value = (data.event.action.value if data.event and data.event.action else {}) or {}
            cfm_id = str(value.get("cfm_id", ""))
            act = str(value.get("act", ""))
            operator = data.event.operator.open_id if data.event and data.event.operator else ""
            if not cfm_id or act not in ("ok", "reject"):
                resp.toast = CallBackToast(**{"type": "error", "content": "无效的审批按钮"})
                return resp
            result = manager.decide(cfm_id, approve=(act == "ok"))
            manager.audit(f"channel:feishu", "card.decide",
                          note=f"by={operator} cfm={cfm_id[:12]} act={act}")
            resp.toast = CallBackToast(**{"type": "success", "content": result[:120]})
        except Exception as e:  # noqa: BLE001
            try:
                resp.toast = CallBackToast(**{"type": "error", "content": f"[审批异常] {e}"[:120]})
            except Exception:
                pass
        return resp

    # ── 文本回信 ───────────────────────────────────────
    def _reply(self, message_id: str, text: str) -> None:
        req = ReplyMessageRequest.builder() \
            .message_id(message_id) \
            .request_body(ReplyMessageRequestBody.builder()
                          .content(json.dumps({"text": text[:4000]}, ensure_ascii=False))
                          .msg_type("text").build()).build()
        self._client.im.v1.message.reply(req)

    def send(self, open_id: str, text: str) -> str | None:
        """主动推送（巡检简报等文本）。"""
        req = CreateMessageRequest.builder() \
            .receive_id_type("open_id") \
            .request_body(CreateMessageRequestBody.builder()
                          .receive_id(open_id)
                          .content(json.dumps({"text": text[:4000]}, ensure_ascii=False))
                          .msg_type("text").build()).build()
        resp = self._client.im.v1.message.create(req)
        return resp.data.message_id if resp.success else None

    # ── HITL 审批卡片（M4.1）───────────────────────────
    def send_card(self, open_id: str, cfm: dict) -> str | None:
        """推送一张待审批交互卡：批准（primary）/ 拒绝（danger），按钮 value 带回 cfm_id。"""
        card = {
            "config": {"wide_screen_mode": True},
            "header": {"template": "orange",
                       "title": {"tag": "plain_text", "content": "Kairos · 待审批"}},
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md",
                    "content": f"**{cfm.get('action', 'propose')}**\n{str(cfm.get('payload', ''))[:300]}"}},
                {"tag": "note", "elements": [
                    {"tag": "plain_text",
                     "content": f"cfm {str(cfm.get('id', ''))[:12]} · {cfm.get('created_at', '')}"}]},
                {"tag": "hr"},
                {"tag": "action", "actions": [
                    {"tag": "button",
                     "text": {"tag": "plain_text", "content": "批准"},
                     "type": "primary", "value": {"cfm_id": cfm.get("id", ""), "act": "ok"}},
                    {"tag": "button",
                     "text": {"tag": "plain_text", "content": "拒绝"},
                     "type": "danger", "value": {"cfm_id": cfm.get("id", ""), "act": "reject"}},
                ]},
            ],
        }
        req = CreateMessageRequest.builder() \
            .receive_id_type("open_id") \
            .request_body(CreateMessageRequestBody.builder()
                          .receive_id(open_id)
                          .content(json.dumps(card, ensure_ascii=False))
                          .msg_type("interactive").build()).build()
        resp = self._client.im.v1.message.create(req)
        return resp.data.message_id if resp.success else None
