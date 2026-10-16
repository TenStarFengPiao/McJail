"""NapCat HTTP 动作客户端：构建请求、鉴权和响应校验。"""

from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from maidanlao.config.env import ensure_env_loaded

logger = logging.getLogger(__name__)


class NapCatError(RuntimeError):
    """NapCat transport or OneBot action failure."""


class NapCatUnknownError(NapCatError):
    """请求可能已被 QQ 接收，但响应不可确认；通知不能据此自动重发。"""


@dataclass(frozen=True)
class NapCatSettings:
    http_url: str
    websocket_url: str
    access_token: str = ""
    request_timeout: float = 15.0
    reconnect_min_delay: float = 1.0
    reconnect_max_delay: float = 30.0
    max_concurrency: int = 8
    respond_without_mention: bool = False

    @classmethod
    def from_env(cls) -> NapCatSettings:
        ensure_env_loaded()
        http_url = os.getenv("NAPCAT_HTTP_URL", "http://127.0.0.1:3000").strip().rstrip("/")
        websocket_url = os.getenv("NAPCAT_WS_URL", "ws://127.0.0.1:3001").strip()
        if not http_url or not websocket_url:
            raise ValueError("NAPCAT_HTTP_URL and NAPCAT_WS_URL must not be empty")
        reconnect_min_delay = max(0.1, float(os.getenv("NAPCAT_RECONNECT_MIN_DELAY", "1")))
        return cls(
            http_url=http_url,
            websocket_url=websocket_url,
            access_token=os.getenv("NAPCAT_ACCESS_TOKEN", "").strip(),
            request_timeout=max(0.1, float(os.getenv("NAPCAT_REQUEST_TIMEOUT", "15"))),
            reconnect_min_delay=reconnect_min_delay,
            reconnect_max_delay=max(reconnect_min_delay, float(os.getenv("NAPCAT_RECONNECT_MAX_DELAY", "30"))),
            max_concurrency=max(1, int(os.getenv("NAPCAT_MAX_CONCURRENCY", "8"))),
            respond_without_mention=os.getenv("NAPCAT_RESPOND_WITHOUT_MENTION", "0").lower()
            in {"1", "true", "yes", "on"},
        )


class NapCatClient:
    """Small synchronous client for NapCat's OneBot 11 HTTP actions."""

    def __init__(self, settings: NapCatSettings):
        self.settings = settings

    def call(
        self,
        action: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        """调用 OneBot 动作，编排请求构建、发送与业务响应校验。

        Args:
            action: NapCat 动作名称，例如 send_group_msg。
            payload: 动作参数；省略时发送空 JSON 对象。

        Returns:
            成功响应中的 data 字段，未提供该字段时返回 None。

        Raises:
            NapCatError: HTTP、网络、超时、JSON 解析或业务响应校验失败。
        """
        endpoint = self._build_endpoint(action)
        request = self._build_request(endpoint, payload)
        result = self._send_request(request, action)

        self._validate_response(result, action)

        return result.get("data")

    def _build_endpoint(self, action: str) -> str:
        """拼接动作地址，移除动作开头的斜杠以保留基础 URL 的路径前缀。"""
        return urljoin(f"{self.settings.http_url}/", action.lstrip("/"))

    def _build_request(self, endpoint: str, payload: dict[str, Any] | None) -> Request:
        """构建 UTF-8 JSON POST 请求，并在配置 token 时添加 Bearer 鉴权。"""
        body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.settings.access_token:
            headers["Authorization"] = f"Bearer {self.settings.access_token}"
        return Request(endpoint, data=body, headers=headers, method="POST")

    def _send_request(self, request: Request, action: str) -> Any:
        """按配置超时发送请求并解析 JSON，将传输及解析错误包装为 NapCatError。"""
        try:
            with urlopen(request, timeout=self.settings.request_timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            # HTTPError 是 URLError 的子类，需先捕获以保留状态码和响应正文。
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise NapCatUnknownError(f"NapCat HTTP {exc.code} for {action}: {detail}") from exc
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise NapCatUnknownError(f"NapCat request failed for {action}: {exc}") from exc

    def _validate_response(self, result: Any, action: str) -> None:
        """校验 OneBot 响应结构与成功标记，失败时抛出 NapCatError。"""
        if not isinstance(result, dict):
            raise NapCatUnknownError(f"NapCat returned an invalid response for {action}")
        if (
            "retcode" not in result
            or isinstance(result["retcode"], bool)
            or not isinstance(result["retcode"], (int, str))
        ):
            raise NapCatUnknownError(f"NapCat returned an invalid retcode for {action}")
        try:
            # HTTP 成功不代表动作成功；同时要求 status=ok、retcode=0。
            # 接受整数或数字字符串形式的 retcode，缺失或无法转换均视为失败。
            succeeded = result.get("status") == "ok" and int(result.get("retcode", -1)) == 0
        except (TypeError, ValueError):
            raise NapCatUnknownError(f"NapCat returned an invalid retcode for {action}") from None
        if result.get("status") not in ("ok", "failed"):
            raise NapCatUnknownError(f"NapCat returned an invalid status for {action}")
        if not succeeded:
            message = result.get("wording") or result.get("message") or "unknown action error"
            raise NapCatError(f"NapCat action {action} failed ({result.get('retcode')}): {message}")

    def get_login_info(self) -> dict[str, Any]:
        return dict(self.call("get_login_info") or {})

    def get_friend_list(self) -> list[dict[str, Any]]:
        """读取完整好友列表；不把无效响应误当作空名单。"""
        result = self.call("get_friend_list")
        if not isinstance(result, list) or any(not isinstance(friend, dict) for friend in result):
            raise NapCatUnknownError("NapCat returned an invalid friend list")
        return result

    def send_private(self, user_id: str | int, message: str | list[dict[str, Any]]) -> int | str:
        data = self.call("send_private_msg", {"user_id": str(user_id), "message": message}) or {}
        return data.get("message_id", "")

    def send_group(self, group_id: str | int, message: str | list[dict[str, Any]]) -> int | str:
        data = self.call("send_group_msg", {"group_id": str(group_id), "message": message}) or {}
        return data.get("message_id", "")

    def get_group_list(self) -> list[dict]:
        """读取机器人已加入的群，配置与发布前复核目标可用性。"""
        data = self.call("get_group_list")
        if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
            raise NapCatUnknownError("invalid_group_list")
        return data

    def forward_single(self, message_id: str, *, user_id=None, group_id=None) -> None:
        """按原消息ID原生转发；成功数据为null，不要求返回新消息ID。"""
        if bool(user_id) == bool(group_id):
            raise ValueError("one_forward_recipient_required")
        action = "forward_group_single_msg" if group_id else "forward_friend_single_msg"
        target = {"group_id": str(group_id)} if group_id else {"user_id": str(user_id)}
        self.call(action, {"message_id": str(message_id), **target})

    def get_group_history(self, group_id: str) -> list[dict]:
        """读取最近消息元数据，为独立群文件通知关联原消息；不下载文件。"""
        data = self.call("get_group_msg_history", {"group_id": str(group_id), "count": 100})
        if not isinstance(data, dict) or not isinstance(data.get("messages"), list):
            raise NapCatUnknownError("invalid_group_history")
        return [item for item in data["messages"] if isinstance(item, dict)]

    def send_file(self, filename: str, content: bytes, *, user_id=None, group_id=None) -> None:
        """使用 NapCat Base64 文件资源发送原始字节，兼容异机部署。"""
        if bool(user_id) == bool(group_id):
            raise ValueError("one_file_recipient_required")
        target = {"group_id": str(group_id)} if group_id else {"user_id": str(user_id)}
        self.call(
            "upload_group_file" if group_id else "upload_private_file",
            {
                **target,
                "name": filename,
                "file": "base64://" + base64.b64encode(content).decode("ascii"),
                "upload_file": True,
            },
        )

    def send_private_text(self, user_id: str | int, content: str) -> bool:
        try:
            self.send_private(user_id, content)
            return True
        except NapCatError:
            logger.exception("napcat_private_send_failed user_id=%s", user_id)
            return False

    def send_group_text(self, group_id: str | int, content: str) -> bool:
        try:
            self.send_group(group_id, content)
            return True
        except NapCatError:
            logger.exception("napcat_group_send_failed group_id=%s", group_id)
            return False
