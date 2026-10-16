"""入口绑定的请求身份；这些字段不是模型可以选择的工具参数。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass(frozen=True)
class RequestContext:
    """只有 NapCat 入口建立 transport=qq；CLI 用户号仅用于隔离聊天。"""

    transport: Literal["qq", "cli"]
    user_id: str
    conversation: str
    request_id: str
    received_at: datetime

    def __post_init__(self):
        if self.transport not in {"qq", "cli"} or not self.user_id or not self.request_id:
            raise ValueError("invalid_request_context")
        if self.received_at.tzinfo is None:
            raise ValueError("received_at_requires_timezone")
