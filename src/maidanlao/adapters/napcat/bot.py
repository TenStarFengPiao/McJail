"""QQ 事件调度、会话串行化和分页文本回复。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import time
from collections import OrderedDict
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

from maidanlao.agent.context import RequestContext
from maidanlao.config.settings import database_path
from maidanlao.notifications import NotificationService
from maidanlao.persistence.delivery import DeliveryLedger
from maidanlao.service import AssistantResponse, AssistantService, conversation_key
from maidanlao.text import plain_text

from .client import NapCatClient, NapCatError, NapCatSettings, NapCatUnknownError
from .events import normalize_message_event
from .rendering import reply_message

logger = logging.getLogger(__name__)


def build_response_for_text(
    content: str,
    message_source: str,
    user_open_id: str,
    group_id: str | None = None,
    db_path: str | None = None,
    request_id: str | None = None,
    *,
    context: RequestContext | None = None,
    notification_service=None,
    forwarding_service=None,
) -> AssistantResponse:
    """统一助手入口；绑定真实 QQ 身份，工具只能准备通知预览。"""
    return AssistantService(
        db_path, notification_service=notification_service, forwarding_service=forwarding_service
    ).prepare_response(user_open_id, group_id, message_source, content, request_id, context=context)


class _RecentEvents:
    """有界进程内去重集合；重启后不保留事件记录。"""

    def __init__(self, max_items: int = 4096, ttl_seconds: float = 300.0):
        self.max_items = max_items
        self.ttl_seconds = ttl_seconds
        self._items: OrderedDict[str, float] = OrderedDict()

    def add_if_new(self, key: str) -> bool:
        """移除过期项，再领取未见过的消息键，重复键返回 False。"""
        now = time.monotonic()
        while self._items and now - next(iter(self._items.values())) > self.ttl_seconds:
            self._items.popitem(last=False)
        if key in self._items:
            return False
        while self._items and len(self._items) >= self.max_items:
            self._items.popitem(last=False)
        self._items[key] = now
        return True

    def discard(self, key: str) -> None:
        """失败事件可重试；具体已交付项目由持久化回执防止重复。"""
        self._items.pop(key, None)


class NapCatBot:
    """QQ 连接与事件调度；跨会话并发、同一会话串行处理。"""

    def __init__(self, settings: NapCatSettings | None = None, db_path: str | None = None):
        self.settings = settings or NapCatSettings.from_env()
        self.db_path = db_path or database_path()
        self.client = NapCatClient(self.settings)
        self._recent = _RecentEvents()
        self._conversation_locks: dict[str, asyncio.Lock] = {}
        self._semaphore = asyncio.Semaphore(self.settings.max_concurrency)
        self._tasks: set[asyncio.Task[Any]] = set()
        self._notifications: NotificationService | None = None
        self._forwarding = None

    def run(self) -> None:
        """启动异步机器人事件循环并配置日志。"""
        logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
        asyncio.run(self.run_forever())

    async def run_forever(self) -> None:
        """通知调度与事件连接同生命周期；断线不丢任务，退出时回收调度器。"""
        from maidanlao.notifications.scheduler import NotificationScheduler

        scheduler = NotificationScheduler(self._notification_service())
        worker = asyncio.create_task(scheduler.run_forever())
        from maidanlao.group_forwarding.worker import ForwardWorker

        forward_worker = asyncio.create_task(ForwardWorker(self._forward_service()).run_forever())
        try:
            await self._consume_events()
        finally:
            worker.cancel()
            forward_worker.cancel()
            await asyncio.gather(worker, forward_worker, return_exceptions=True)

    def _forward_service(self):
        if self._forwarding is None:
            from maidanlao.group_forwarding.service import ForwardService

            self._forwarding = ForwardService(self.db_path, self.client)
        return self._forwarding

    def _notification_service(self):
        if self._notifications is None:
            self._notifications = NotificationService(self.db_path, self.client)
        return self._notifications

    async def _consume_events(self) -> None:
        """消费 WebSocket 事件，施加背压并在断线后退避重连。"""
        try:
            import websockets
        except ImportError as exc:
            raise NapCatError("websockets is not installed; install the project dependencies") from exc
        delay = self.settings.reconnect_min_delay
        while True:
            headers = {"Authorization": f"Bearer {self.settings.access_token}"} if self.settings.access_token else None
            url = self.settings.websocket_url
            if self.settings.access_token and "access_token=" not in url:
                separator = "&" if "?" in url else "?"
                url = f"{url}{separator}{urlencode({'access_token': self.settings.access_token})}"
            try:
                async with websockets.connect(
                    url, additional_headers=headers, ping_interval=20, ping_timeout=20, max_size=8 * 1024 * 1024
                ) as websocket:
                    login = await asyncio.to_thread(self.client.get_login_info)
                    logger.info(
                        "napcat_connected url=%s user_id=%s nickname=%s",
                        self.settings.websocket_url,
                        login.get("user_id", ""),
                        login.get("nickname", ""),
                    )
                    delay = self.settings.reconnect_min_delay
                    async for raw in websocket:
                        try:
                            event = json.loads(raw)
                        except (TypeError, json.JSONDecodeError):
                            logger.warning("napcat_invalid_event")
                            continue
                        # 创建任务前领取并发名额，防止消息积压导致无限增长。
                        await self._semaphore.acquire()
                        task = asyncio.create_task(self._handle_event_with_slot(event))
                        self._tasks.add(task)
                        task.add_done_callback(self._task_done)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("napcat_websocket_disconnected error=%s retry_in=%.1fs", exc, delay)
                await asyncio.sleep(delay + random.random() * min(delay, 1.0))
                delay = min(self.settings.reconnect_max_delay, max(self.settings.reconnect_min_delay, delay * 2))

    def _task_done(self, task: asyncio.Task[Any]) -> None:
        """回收已结束任务并记录未处理异常。"""
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("napcat_event_task_failed error=%r", task.exception())

    async def _handle_event_with_slot(self, event: dict[str, Any]) -> None:
        """处理事件并确保释放并发名额。"""
        try:
            await self.handle_event(event)
        finally:
            self._semaphore.release()

    async def handle_event(self, event: dict[str, Any]) -> None:
        """先过滤与去重，再按会话锁串行处理有效消息。"""
        if isinstance(event, dict) and event.get("group_id"):
            # 群消息只采集，永不进入Agent或管理命令，包括@和管理员消息。
            await asyncio.to_thread(self._forward_service().collect, event)
            return
        normalized = normalize_message_event(event)
        if normalized is None:
            return
        event_key = f"{normalized['message_source']}:{normalized['group_id']}:{normalized['message_id']}"
        if not normalized["message_id"] or not self._recent.add_if_new(event_key):
            return
        conversation = conversation_key(
            normalized["user_open_id"], normalized["group_id"], normalized["message_source"]
        )
        lock = self._conversation_locks.setdefault(conversation, asyncio.Lock())
        async with lock:
            if await self._process_message(normalized) is False:
                self._recent.discard(event_key)

    async def _process_message(self, message: dict[str, Any]) -> bool | None:
        """只响应私聊；群消息无条件静默，审批发布由独立工作器执行。"""
        if message["message_source"] == "group":
            return
        if not message["content"]:
            return
        request_id = json.dumps(
            [
                conversation_key(message["user_open_id"], message["group_id"], message["message_source"]),
                message["message_id"],
            ]
        )
        # 仅精确人工命令直接处理；自然语言全部进入同一个 Agent。
        try:
            forward_result = await asyncio.to_thread(
                self._forward_service().handle, message["user_open_id"], message["content"], request_id
            )
        except (PermissionError, ValueError) as exc:
            forward_result = str(exc)
        except Exception:
            forward_result = "群转发确认失败，请检查连接并重新查询；不会自动发布。"
        if forward_result is not None:
            return await self._send_notification_reply(request_id, message["user_open_id"], forward_result)
        received_at = datetime.now(UTC)
        if isinstance(message.get("created_at"), (int, float)):
            received_at = datetime.fromtimestamp(message["created_at"], UTC)
        result = await asyncio.to_thread(
            self._notification_service().handle,
            message["user_open_id"],
            message["content"],
            request_id,
            conversation_key(message["user_open_id"], message["group_id"], message["message_source"]),
            received_at,
        )
        if result is not None:
            return await self._send_notification_reply(request_id, message["user_open_id"], result)
        ledger = DeliveryLedger(self.db_path, request_id)
        if await asyncio.to_thread(ledger.completed):
            return True
        try:
            response = await asyncio.to_thread(ledger.load_response)
        except ValueError:
            response = AssistantResponse("此前待交付的文件已变化或不可用，请重新查询并明确索要。")
        if response is None:
            response = await asyncio.to_thread(
                build_response_for_text,
                message["content"],
                message["message_source"],
                message["user_open_id"],
                message["group_id"],
                self.db_path,
                request_id,
                context=RequestContext(
                    "qq",
                    message["user_open_id"],
                    conversation_key(message["user_open_id"], message["group_id"], message["message_source"]),
                    request_id,
                    received_at,
                ),
                notification_service=self._notification_service(),
                forwarding_service=self._forward_service(),
            )
        private = response.reply_target == "private"
        return await self._send_response(
            response,
            user_id=message["user_open_id"] if private or message["message_source"] == "private" else None,
            group_id=None if private else message["group_id"],
            reply_to=None if private and message["message_source"] == "group" else message["message_id"],
        )

    async def _send_notification_reply(self, request_id, user_id, text):
        """管理回执只发私聊，不记入知识问答；重复事件不重复发已确认页。"""
        ledger = DeliveryLedger(self.db_path, request_id)
        for index, offset in enumerate(range(0, len(text), 2000)):
            key = f"notification_reply:{index}"
            claim = await asyncio.to_thread(ledger.claim, key, "通知管理回执")
            if claim == "confirmed":
                continue
            if claim == "unknown":
                return True
            try:
                await asyncio.to_thread(self.client.send_private, user_id, text[offset : offset + 2000])
            except Exception as exc:
                unknown = isinstance(exc, NapCatUnknownError) or bool(exc.__cause__)
                await asyncio.to_thread(ledger.finish, key, "unknown" if unknown else "failed")
                return unknown
            await asyncio.to_thread(ledger.finish, key, "confirmed")
        return True

    async def _send_response(
        self,
        response: AssistantResponse,
        *,
        user_id: str | None = None,
        group_id: str | None = None,
        reply_to: str | None = None,
    ) -> bool:
        """附件和文本全部确认后提交历史；已确认项目在同一消息重试时跳过。"""
        pending = response.pending_turn
        ledger = DeliveryLedger(self.db_path, pending.request_id) if pending and pending.request_id else None
        confirmed = []
        current_label = "文字回复"
        current_key = ""
        sending = False
        unknown = False

        async def deliver(item_key, label, function, *args, **kwargs):
            nonlocal current_label, current_key, sending, unknown
            current_label, current_key = label, item_key
            sending = False
            claim = await asyncio.to_thread(ledger.claim, item_key, label) if ledger else "send"
            if claim == "unknown":
                unknown = True
                raise NapCatError("delivery_result_unknown")
            if claim == "send":
                sending = True
                await asyncio.to_thread(function, *args, **kwargs)
                sending = False
                if ledger:
                    unknown = True  # 回执落盘失败时，外部副作用可能已经发生。
                    await asyncio.to_thread(ledger.finish, item_key, "confirmed")
                    unknown = False
            confirmed.append(label)

        try:
            if ledger:
                await asyncio.to_thread(ledger.remember, response)
                response = await asyncio.to_thread(ledger.load_response)
            for attachment in response.attachments:
                digest = hashlib.sha256(attachment.content).hexdigest()
                await deliver(
                    f"file:{attachment.document_id}:{digest}",
                    attachment.filename,
                    self.client.send_file,
                    attachment.filename,
                    attachment.content,
                    user_id=user_id if not group_id else None,
                    group_id=group_id,
                )
            text = plain_text(response.text)
            for index, offset in enumerate(range(0, len(text), 2000)):
                page = text[offset : offset + 2000]
                message = reply_message(reply_to, page) if reply_to and index == 0 else page
                item_key = f"text:{index}:" + hashlib.sha256(page.encode()).hexdigest()
                if group_id:
                    await deliver(item_key, f"文字回复第{index + 1}页", self.client.send_group, group_id, message)
                else:
                    await deliver(
                        item_key, f"文字回复第{index + 1}页", self.client.send_private, user_id or "", message
                    )
            await asyncio.to_thread(AssistantService(self.db_path).commit_turn, response)
            return True
        except Exception as exc:
            logger.warning("napcat_response_send_failed type=%s", type(exc).__name__)
            unknown = unknown or (sending and (isinstance(exc, NapCatUnknownError) or bool(exc.__cause__)))
            if ledger and sending:
                try:
                    await asyncio.to_thread(ledger.finish, current_key, "unknown" if unknown else "failed")
                except Exception:
                    # 回执写入失败仍保留 sending，下次不自动重发。
                    unknown = True
            message = ("接口已确认发送：" + "、".join(confirmed) + "。\n") if confirmed else ""
            message += current_label + (
                "发送结果未知，请先确认是否收到；同一消息不会自动重发。" if unknown else "发送失败，请稍后重试。"
            )
            if (
                not sending
                and confirmed
                and len(confirmed) == len(response.attachments) + (len(plain_text(response.text)) + 1999) // 2000
            ):
                message = "本轮内容已确认发送，但聊天记录保存失败；请勿重复索要已收到的文件。"
            try:
                if group_id:
                    await asyncio.to_thread(self.client.send_group, group_id, message)
                else:
                    await asyncio.to_thread(self.client.send_private, user_id or "", message)
            except Exception:
                pass
            return False


__all__ = [
    "NapCatBot",
    "NapCatClient",
    "NapCatError",
    "NapCatSettings",
    "normalize_message_event",
]
