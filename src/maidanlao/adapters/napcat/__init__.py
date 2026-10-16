"""NapCat 接入公共入口；实现按协议与调度职责分包。"""

from .bot import NapCatBot
from .client import NapCatClient, NapCatError, NapCatSettings
from .events import extract_message_text, normalize_message_event

__all__ = [
    "NapCatClient",
    "NapCatError",
    "NapCatSettings",
    "NapCatBot",
    "extract_message_text",
    "normalize_message_event",
]
