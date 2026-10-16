"""交付回执：独立短事务，不在网络调用期间占用数据库事务。"""

import hashlib
import json
from contextlib import closing

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert

from .database import open_database
from .models import ChatTurn, DeliveryAttempt
from .transactions import transaction


class DeliveryLedger:
    """同一消息的确认项目不重发；sending/unknown 必须由人工确认。"""

    def __init__(self, db_path: str, request_id: str):
        self.db_path = db_path
        self.request_id = request_id

    def completed(self) -> bool:
        """已提交完整历史的重复事件可直接忽略，避免再次调用模型。"""
        with closing(open_database(self.db_path)) as db:
            return db.session.scalar(select(ChatTurn.id).where(ChatTurn.request_id == self.request_id)) is not None

    def remember(self, response) -> None:
        """冻结同一事件的发送内容；重试不重新生成文本或替换附件。"""
        if response.pending_turn is None:
            return
        payload = {
            "text": response.text,
            "sources": response.sources,
            "reply_target": response.reply_target,
            "conversation_key": response.pending_turn.conversation_key,
            "question": response.pending_turn.question,
            "attachments": [
                {"id": a.document_id, "filename": a.filename, "sha256": hashlib.sha256(a.content).hexdigest()}
                for a in response.attachments
            ],
        }
        with closing(open_database(self.db_path)) as db, transaction(db):
            db.session.execute(
                insert(DeliveryAttempt)
                .values(
                    request_id=self.request_id,
                    item_key="response",
                    label="回答快照",
                    status="prepared",
                    payload_json=json.dumps(payload, ensure_ascii=False),
                )
                .on_conflict_do_nothing()
            )

    def load_response(self):
        """恢复相同回答；原件不可用或版本/校验值变化时停止重试，绝不换发别的文件。"""
        from maidanlao.rag.models import OriginalAttachment
        from maidanlao.rag.originals import validate_original
        from maidanlao.service import AssistantResponse, PendingTurn

        from .repository import KnowledgeRepository

        with closing(open_database(self.db_path)) as db:
            row = db.session.get(DeliveryAttempt, (self.request_id, "response"))
            if row is None:
                return None
            payload = json.loads(row.payload_json)
            attachments = []
            repos = KnowledgeRepository(db)
            for item in payload["attachments"]:
                doc = repos.knowledge_documents.get(item["id"])
                metadata = doc or repos.resources.get(item["id"])
                original = repos.knowledge_documents.original(item["id"]) if doc else metadata
                source = next(
                    s for s in payload["sources"] if (s.get("document_id") or s.get("resource_id")) == item["id"]
                )
                if (
                    metadata is None
                    or metadata.status != "active"
                    or original is None
                    or metadata.version != (source.get("document_version") or source.get("resource_version"))
                ):
                    raise ValueError("delivery_document_changed")
                if (
                    original.filename != item["filename"]
                    or validate_original(original.filename, original.content) != item["sha256"]
                ):
                    raise ValueError("delivery_original_changed")
                attachments.append(OriginalAttachment(item["id"], original.filename, original.content))
            return AssistantResponse(
                payload["text"],
                payload["sources"],
                attachments,
                PendingTurn(payload["conversation_key"], payload["question"], self.request_id),
                reply_target=payload.get("reply_target", "conversation"),
            )

    def claim(self, item_key: str, label: str) -> str:
        """原子领取交付项目；先持久化 sending，防止进程中断后盲目重发。"""
        with closing(open_database(self.db_path)) as db, transaction(db):
            statement = (
                insert(DeliveryAttempt)
                .values(request_id=self.request_id, item_key=item_key, label=label, status="sending")
                .on_conflict_do_nothing()
            )
            if db.session.execute(statement).rowcount:
                return "send"
            row = db.session.get(DeliveryAttempt, (self.request_id, item_key))
            if row.status == "confirmed":
                return "confirmed"
            if row.status == "failed":
                claimed = db.session.execute(
                    update(DeliveryAttempt)
                    .where(
                        DeliveryAttempt.request_id == self.request_id,
                        DeliveryAttempt.item_key == item_key,
                        DeliveryAttempt.status == "failed",
                    )
                    .values(status="sending")
                )
                if claimed.rowcount:
                    return "send"
            return "unknown"

    def finish(self, item_key: str, status: str) -> None:
        """落盘接口确认或失败；超时与网络断开一律标记结果未知。"""
        if status not in {"confirmed", "failed", "unknown"}:
            raise ValueError("invalid_delivery_status")
        with closing(open_database(self.db_path)) as db, transaction(db):
            db.session.execute(
                update(DeliveryAttempt)
                .where(DeliveryAttempt.request_id == self.request_id, DeliveryAttempt.item_key == item_key)
                .values(status=status)
            )

    def records(self) -> list[dict]:
        """返回可读回执，用于部分交付失败提示，不包含文件内容。"""
        with closing(open_database(self.db_path)) as db:
            return [
                r.to_dict()
                for r in db.session.scalars(
                    select(DeliveryAttempt).where(DeliveryAttempt.request_id == self.request_id)
                )
            ]
