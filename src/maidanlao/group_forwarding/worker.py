"""窗口截止与发布工作器；不调用模型，逐项发送前持久化回执。"""

import asyncio
import json
import logging

from sqlalchemy import select

from maidanlao.adapters.napcat.client import NapCatError, NapCatUnknownError
from maidanlao.notifications.contracts import stamp
from maidanlao.persistence.delivery import DeliveryLedger
from maidanlao.persistence.models import DeliveryAttempt, ForwardAttachment, ForwardBatch, ForwardPreview

from .service import local

logger = logging.getLogger(__name__)


class ForwardWorker:
    """与机器人同生命周期；发布领取是原子状态转换，网络请求不占数据库事务。"""

    def __init__(self, service):
        self.service = service
        self._file_checks = {}

    def send_text(self, sender, target, text):
        """文字使用独立消息段，避免原文中的CQ字串变成@或其他动作。"""
        result = sender(target, [{"type": "text", "data": {"text": text}}])
        if isinstance(result, bool) or not isinstance(result, (int, str)) or not str(result):
            raise NapCatUnknownError("text_send_missing_message_id")

    def send_private(self, target, text):
        self.send_text(self.service.client.send_private, target, text)

    def send_group(self, target, text):
        self.send_text(self.service.client.send_group, target, text)

    def recover(self):
        """单机器人进程启动时冻结上次在途项目；回执不确定就通知管理员，不盲目重放。"""
        recovered = []
        with self.service.write() as session:
            for row in session.scalars(select(ForwardBatch).where(ForwardBatch.status == "publishing")):
                row.status = "unknown"
                recovered.append((row.id, row.target_group, row.version))
                for item in session.scalars(
                    select(DeliveryAttempt).where(
                        DeliveryAttempt.request_id == f"forward_publish:{row.id}:V{row.version}",
                        DeliveryAttempt.status == "sending",
                    )
                ):
                    item.status = "unknown"
        for batch_id, target, version in recovered:
            self.report(batch_id, target, version, "unknown")

    async def run_forever(self):
        # 同一账号单进程部署：前次进程在途发布结果不可确定，不自动重新发送。
        await asyncio.to_thread(self.recover)
        while True:
            try:
                await asyncio.to_thread(self.tick)
            except Exception as exc:
                logger.warning("forward_worker_failed type=%s", type(exc).__name__)
            await asyncio.sleep(1)

    def tick(self):
        """窗口按截止时间切分；空闲时每秒检查，外部发送耗时不延长采集窗口。"""
        with self.service.write() as session:
            for row in session.scalars(
                select(ForwardBatch).where(
                    ForwardBatch.status == "collecting", ForwardBatch.closes_at <= stamp(self.service.clock())
                )
            ):
                row.status = "review"
            completed = select(DeliveryAttempt.request_id).where(
                DeliveryAttempt.item_key == "review_complete", DeliveryAttempt.status == "confirmed"
            )
            reviews = [
                r.id
                for r in session.scalars(
                    select(ForwardBatch).where(
                        ForwardBatch.status.in_(["review", "preview"]),
                        ("forward_review:" + ForwardBatch.id).not_in(completed),
                    )
                )
            ]
            approved = [r.id for r in session.scalars(select(ForwardBatch).where(ForwardBatch.status == "approved"))]
        for batch_id in reviews:
            self.resolve_files(batch_id)
            self.review(batch_id)
        for batch_id in approved:
            self.publish(batch_id)

    def resolve_files(self, batch_id):
        """独立上传通知没有消息ID时，通过最近历史严格匹配文件ID；不猜同名文件。"""
        data = self.service.query(self.service.admin_id, identifier_value=batch_id)
        missing = [a for a in data["attachments"] if not a["message_id"]]
        if not missing:
            self._file_checks.pop(batch_id, None)
            return
        now = self.service.clock()
        if batch_id in self._file_checks and (now - self._file_checks[batch_id]).total_seconds() < 30:
            return
        self._file_checks[batch_id] = now
        try:
            messages = self.service.client.get_group_history(data["source_group"])
            for file in missing:
                matches = set()
                for message in messages:
                    if str(message.get("user_id")) != file["sender_id"]:
                        continue
                    for segment in message.get("message", []):
                        if not isinstance(segment, dict) or segment.get("type") != "file":
                            continue
                        values = segment.get("data") or {}
                        if (
                            file["file_id"]
                            and file["file_id"] == str(values.get("file_id") or values.get("id") or "")
                            and message.get("message_id")
                        ):
                            matches.add(str(message["message_id"]))
                if len(matches) == 1:
                    with self.service.write() as session:
                        row = session.get(ForwardAttachment, file["id"])
                        if row and not row.message_id:
                            row.message_id = matches.pop()
        except Exception as exc:
            logger.warning("forward_file_reference_unavailable type=%s", type(exc).__name__)

    def deliver(self, ledger, key, function, *args, **kwargs):
        """未知结果不换路径或盲目重发；已确认项目直接跳过。"""
        state = ledger.claim(key, key)
        if state != "send":
            return state
        try:
            function(*args, **kwargs)
        except Exception as exc:
            state = (
                "failed"
                if isinstance(exc, NapCatError) and not isinstance(exc, NapCatUnknownError) and not exc.__cause__
                else "unknown"
            )
            ledger.finish(key, state)
            return state
        ledger.finish(key, "confirmed")
        return "confirmed"

    def review(self, batch_id):
        data = self.service.query(self.service.admin_id, identifier_value=batch_id)
        ledger = DeliveryLedger(self.service.path, "forward_review:" + batch_id)
        records = {r["item_key"]: r["status"] for r in ledger.records()}
        if any(state in {"failed", "unknown", "sending"} for state in records.values()):
            return
        text = (
            f"待审批次：{batch_id}\n来源群：{data['source_group']}\n目标群：{data['target_group']}\n"
            f"消息窗口：{local(data['opened_at'])}—{local(data['closes_at'])}\n原始消息：\n"
        )
        text += "\n\n".join(f"{m['sender']}（{local(m['received_at'])}）\n{m['text']}" for m in data["messages"])
        text += "\n\n文件清单：\n" + (
            "\n".join(
                a["id"] + " " + a["filename"] + ("（缺少原消息ID，不能原生转发）" if not a["message_id"] else "")
                for a in data["attachments"]
            )
            or "无"
        )
        text += f"\n回复“生成预览 {batch_id}”，由Agent整理正文。"
        for index, offset in enumerate(range(0, len(text), 1800)):
            page = f"批次 {batch_id} 原文第{index + 1}页\n" + text[offset : offset + 1800]
            if self.deliver(ledger, f"text:{index}", self.send_private, self.service.admin_id, page) != "confirmed":
                return
        seen_messages = set()
        for file in data["attachments"]:
            if file["message_id"]:
                if file["message_id"] in seen_messages:
                    continue
                seen_messages.add(file["message_id"])
                if (
                    self.deliver(
                        ledger,
                        "file:" + file["id"],
                        self.service.client.forward_single,
                        file["message_id"],
                        user_id=self.service.admin_id,
                    )
                    != "confirmed"
                ):
                    self.deliver(
                        DeliveryLedger(self.service.path, "forward_review_error:" + batch_id),
                        "error",
                        self.send_private,
                        self.service.admin_id,
                        f"批次 {batch_id} 原文件转发给管理员未全部确认，已暂停。可查看批次回执；不会下载上传或自动重发未知结果。",
                    )
                    return
        if all(a["message_id"] for a in data["attachments"]):
            # 完整审阅记录不再每秒扫描；缺ID的批次仍可等待关联。
            if ledger.claim("review_complete", "管理员原文交付完成") == "send":
                ledger.finish("review_complete", "confirmed")

    def publish(self, batch_id):
        with self.service.write() as session:
            row = session.get(ForwardBatch, batch_id)
            if row is None or row.status != "approved":
                return
            preview = session.scalar(
                select(ForwardPreview).where(ForwardPreview.batch_id == batch_id, ForwardPreview.version == row.version)
            )
            if preview is None:
                row.status = "partial"
                return
            row.status = "publishing"
            target, body, version = preview.target_group, preview.body, preview.version
            files = []
            for aid in json.loads(preview.attachment_ids_json):
                file = session.get(ForwardAttachment, aid)
                files.append((file.id, file.message_id) if file else (aid, None))
            source = row.source_group
        ledger = DeliveryLedger(self.service.path, f"forward_publish:{batch_id}:V{version}")
        state = "confirmed"
        try:
            self.service.groups(source, target)
            seen_messages = set()
            items = []
            for aid, message_id in files:
                if message_id in seen_messages:
                    continue
                seen_messages.add(message_id)
                items.append(("file:" + aid, self.service.client.forward_single, (message_id,), {"group_id": target}))
            items.extend(
                (f"text:{i}", self.send_group, (target, body[o : o + 2000]), {})
                for i, o in enumerate(range(0, len(body), 2000))
            )
            for key, function, args, kwargs in items:
                with self.service.write() as session:
                    current = session.get(ForwardBatch, batch_id)
                    if current.status != "publishing":
                        state = "cancelled"
                        break
                if not args[0]:
                    state = "failed"
                    break
                state = self.deliver(ledger, key, function, *args, **kwargs)
                if state != "confirmed":
                    break
        except Exception as exc:
            logger.warning("forward_publish_failed type=%s", type(exc).__name__)
            # 回执落盘也可能失败；出现任何在途记录时不得当成明确拒绝。
            state = "unknown" if any(r["status"] in {"sending", "unknown"} for r in ledger.records()) else "failed"
        with self.service.write() as session:
            row = session.get(ForwardBatch, batch_id)
            if row.status == "publishing":
                row.status = "published" if state == "confirmed" else "unknown" if state == "unknown" else "partial"
            status = row.status
        self.report(batch_id, target, version, status)

    def report(self, batch_id, target, version, status):
        """执行报告独立于群发布回执；管理员收报失败不引发目标群重发。"""
        records = DeliveryLedger(self.service.path, f"forward_publish:{batch_id}:V{version}").records()
        counts = {s: sum(r["status"] == s for r in records) for s in ("confirmed", "failed", "unknown", "sending")}
        report = f"批次 {batch_id} V{version} 发布结果：{status}\n目标群：{target}\n逐项回执：{json.dumps(counts, ensure_ascii=False)}\n接口确认不代表已读；未知结果不自动重发。"
        self.deliver(
            DeliveryLedger(self.service.path, f"forward_report:{batch_id}:V{version}:{status}"),
            "report",
            self.send_private,
            self.service.admin_id,
            report,
        )
