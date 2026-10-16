"""OneBot 事件解析；只规范化消息，不访问模型或数据库。"""

from __future__ import annotations

import re
from typing import Any


def _string_id(value: Any) -> str:
    """把平台 ID 转为字符串，缺失时返回空字符串。"""
    return "" if value is None else str(value)


def _message_segments(event: dict[str, Any]) -> list[dict[str, Any]]:
    """仅接受字典形式消息段，忽略格式异常项。"""
    message = event.get("message")
    if isinstance(message, list):
        return [item for item in message if isinstance(item, dict)]
    return []


def _clean_cq_text(text: str, self_id: str = "") -> str:
    """移除 CQ 控制段并规范化空白，保留可读文本。"""
    if self_id:
        text = re.sub(rf"\[CQ:at,qq={re.escape(self_id)}(?:,[^]]*)?]", "", text)
    text = re.sub(r"\[CQ:[^]]+]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def extract_message_text(event: dict[str, Any]) -> str:
    """提取文本与媒体标记，移除机器人 @；不会下载媒体。"""
    segments = _message_segments(event)
    if not segments:
        return _clean_cq_text(
            str(event.get("raw_message") or event.get("message") or ""), _string_id(event.get("self_id"))
        )
    parts: list[str] = []
    labels = {"image": "[图片]", "record": "[语音]", "video": "[视频]", "file": "[文件]", "json": "[卡片]"}
    self_id = _string_id(event.get("self_id"))
    for segment in segments:
        kind = str(segment.get("type") or "")
        data = segment.get("data") if isinstance(segment.get("data"), dict) else {}
        if kind == "text":
            parts.append(str(data.get("text") or ""))
        elif kind == "at" and _string_id(data.get("qq")) not in {self_id, "all"}:
            parts.append(f"@{data.get('name') or data.get('qq')}")
        elif kind in labels:
            parts.append(labels[kind])
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def normalize_message_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """规范化入站私聊或群消息，忽略其他事件及机器人自身消息。"""
    if not isinstance(event, dict) or event.get("post_type") not in {"message", "message_sent"}:
        return None
    message_type = event.get("message_type")
    if message_type not in {"private", "group"}:
        return None
    sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
    user_id = _string_id(event.get("user_id") or sender.get("user_id"))
    if message_type == "group" and not event.get("group_id"):
        return None
    self_id = _string_id(event.get("self_id"))
    if not user_id or user_id == self_id or event.get("post_type") == "message_sent":
        return None
    segments = _message_segments(event)
    mentioned = message_type == "private" or any(
        item.get("type") == "at"
        and isinstance(item.get("data"), dict)
        and _string_id(item["data"].get("qq")) == self_id
        for item in segments
    )
    if message_type == "group" and not segments:
        mentioned = bool(
            self_id and re.search(rf"\[CQ:at,qq={re.escape(self_id)}(?:,[^]]*)?]", str(event.get("raw_message") or ""))
        )
    return {
        "platform": "qq",
        "protocol": "onebot11",
        "message_id": _string_id(event.get("message_id")),
        "message_source": str(message_type),
        # 沿用入口参数名；此值是 QQ 用户号，不是旧身份系统的账号。
        "user_open_id": user_id,
        "group_id": _string_id(event.get("group_id")) if message_type == "group" else None,
        "content": extract_message_text(event),
        "mentioned_bot": mentioned,
        "created_at": event.get("time"),
    }
