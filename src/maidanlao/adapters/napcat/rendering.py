"""QQ 消息段渲染；纯函数不发消息、不写入数据库。"""

from typing import Any

from maidanlao.text import plain_text


def reply_message(message_id: str, content: str) -> list[dict[str, Any]]:
    """构建引用原消息的文本回复，消息 ID 使用字符串保持精度。"""
    return [{"type": "reply", "data": {"id": str(message_id)}}, {"type": "text", "data": {"text": plain_text(content)}}]
