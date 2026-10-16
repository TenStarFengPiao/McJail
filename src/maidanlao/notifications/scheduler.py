"""机器人进程内通知调度：可恢复短事务、统一正文与逐页发送回执。"""

import asyncio
import json
import logging
from uuid import uuid4

from maidanlao.adapters.napcat.client import NapCatError, NapCatUnknownError
from maidanlao.persistence.delivery import DeliveryLedger

from .contracts import utc_now
from .service import NotificationService

logger = logging.getLogger(__name__)


class NotificationScheduler:
    """每五秒扫描到期任务；发送循环不占用 QQ 问答并发名额或数据库事务。"""

    def __init__(self, service: NotificationService, *, clock=utc_now, pause=asyncio.sleep):
        self.service, self.store = service, service.store
        self.clock, self.pause = clock, pause
        self.worker = uuid4().hex

    async def run_forever(self):
        """到期扫描与发送并行；一个大批次不会阻塞五秒扫描。"""
        worker = asyncio.create_task(self._work_forever())
        try:
            while True:
                try:
                    await asyncio.to_thread(self.store.materialize, self.clock())
                except Exception as exc:
                    logger.warning("notification_scan_failed type=%s", type(exc).__name__)
                await self.pause(self.service.settings.poll_seconds)
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def _work_forever(self):
        while True:
            try:
                await self.tick()
            except Exception as exc:
                logger.warning("notification_tick_failed type=%s", type(exc).__name__)
            await self.pause(self.service.settings.poll_seconds)

    async def tick(self):
        """可注入时钟和传输进行离线验证；模型从不参与到期发送。"""
        await asyncio.to_thread(self.store.materialize, self.clock())
        for run_id in await asyncio.to_thread(self.store.available, self.clock()):
            run = await asyncio.to_thread(self.store.claim, run_id, self.worker, self.clock())
            if run is not None:
                await self._deliver(run)
        for run_id in await asyncio.to_thread(self.store.reports_due):
            await self._report(run_id)

    async def _deliver(self, run):
        pages = [run["body"][offset : offset + 2000] for offset in range(0, len(run["body"]), 2000)]
        stop = False
        for user in json.loads(run["recipients_json"]):
            for index, page in enumerate(pages):
                key = f"user:{user}:page:{index}"
                state = await asyncio.to_thread(self.store.before_send, run["id"], self.worker, key, self.clock())
                if state in {"cancelled", "expired", "lost"}:
                    stop = True
                    break
                if state == "confirmed":
                    continue
                if state != "send":
                    # 后页等待前页确认，保证分页顺序；继续其他接收者。
                    break
                try:
                    await asyncio.to_thread(self.service.client.send_private, user, page)
                    status = "confirmed"
                except NapCatUnknownError:
                    status = "unknown"
                except NapCatError as exc:
                    status = "unknown" if exc.__cause__ else "failed"
                except Exception:
                    status = "unknown"
                saved = await asyncio.to_thread(self.store.record, run["id"], self.worker, key, status, self.clock())
                if not saved:
                    stop = True
                    break
                await self.pause(self.service.settings.send_interval)
                if status != "confirmed":
                    break
            if stop:
                break
        await asyncio.to_thread(self.store.settle, run["id"], self.worker, self.clock())

    async def _report(self, run_id):
        """报告独立回执；报告失败不会再次给学生发送。"""
        ledger = DeliveryLedger(self.store.path, run_id)
        if await asyncio.to_thread(ledger.claim, "admin_report", "通知执行报告") != "send":
            return
        try:
            report = await asyncio.to_thread(self.service.report, run_id)
            report = report.split("\n通知正文：", 1)[0] + f"\n发送“查看通知 {run_id}”可查看正文及逐人明细。"
            # 报告使用一次普通私聊；完整逐人明细由查看命令分页展示。
            await asyncio.to_thread(self.service.client.send_private, self.service.settings.admin_id, report)
        except Exception as exc:
            await asyncio.to_thread(
                ledger.finish,
                "admin_report",
                "unknown" if isinstance(exc, NapCatUnknownError) or exc.__cause__ else "failed",
            )
        else:
            await asyncio.to_thread(ledger.finish, "admin_report", "confirmed")
