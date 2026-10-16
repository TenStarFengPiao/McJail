"""收集与审批使用短事务；所有外部发送只能在明确人工确认后进入工作器。"""

import json
import re
from contextlib import closing, contextmanager
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from maidanlao.notifications.contracts import BEIJING, NotificationSettings, stamp, utc_now
from maidanlao.persistence.database import open_database
from maidanlao.persistence.models import DeliveryAttempt, ForwardAttachment, ForwardBatch, ForwardPreview, ForwardRule


def identifier(prefix):
    return prefix + uuid4().hex[:16].upper()


class ForwardService:
    """只监听已确认规则。附件保存原消息引用，绝不下载或自动切换上传方式。"""

    def __init__(self, path, client, *, clock=utc_now, admin_id=None):
        self.path, self.client, self.clock = path, client, clock
        self.admin_id = admin_id or NotificationSettings.from_env().admin_id

    @contextmanager
    def write(self):
        with closing(open_database(self.path)) as db, db.session.begin():
            db.session.connection(execution_options={"write_lock": True})
            yield db.session

    def authorize(self, actor):
        if actor != self.admin_id:
            raise PermissionError("只有管理员可以管理群转发。")

    def groups(self, source, target):
        """成员关系可验证；实际发言权限仍由QQ接口最终判断。"""
        joined = {str(item.get("group_id")) for item in self.client.get_group_list()}
        if source == target or not {source, target} <= joined:
            raise ValueError("来源群与目标群必须不同，且机器人必须已加入两个群。")

    def cached(self, request_id):
        with closing(open_database(self.path)) as db:
            row = db.session.get(DeliveryAttempt, (request_id, "forward_command"))
            return json.loads(row.payload_json)["text"] if row else None

    def command(self, request_id, callback):
        """同一入站消息的变更与固定回执原子提交。"""
        with self.write() as session:
            row = session.get(DeliveryAttempt, (request_id, "forward_command"))
            if row:
                return json.loads(row.payload_json)["text"]
            result = callback(session)
            session.add(
                DeliveryAttempt(
                    request_id=request_id,
                    item_key="forward_command",
                    label="群转发管理",
                    status="prepared",
                    payload_json=json.dumps({"text": result}, ensure_ascii=False),
                )
            )
            return result

    def prepare_rule(self, actor, request_id, source, target):
        self.authorize(actor)
        if not re.fullmatch(r"\d{5,12}", source) or not re.fullmatch(r"\d{5,12}", target):
            raise ValueError("请提供完整数字群号。")
        self.groups(source, target)

        def save(session):
            row = session.scalar(select(ForwardRule).where(ForwardRule.source_group == source))
            if row is None:
                row = ForwardRule(
                    id=identifier("F"), source_group=source, target_group=target, status="draft", config_json="{}"
                )
                session.add(row)
            token = identifier("R")
            row.config_json = json.dumps(
                {"target": target, "token": token, "expires_at": stamp(self.clock() + timedelta(minutes=30))}
            )
            return f"群转发规则预览：{row.id}\n来源群：{source}\n目标群：{target}\n首条消息后固定60秒，文字与文件先交管理员审阅。\n回复“确认规则 {row.id} {token}”后启用，30分钟有效。"

        return self.command(request_id, save)

    def collect(self, event):
        """在任何聊天过滤前调用；跨发送人按同一来源群锁定窗口。"""
        if event.get("post_type") not in {"message", "notice"} or (
            event.get("self_id") is not None and str(event.get("user_id")) == str(event.get("self_id"))
        ):
            return
        group = str(event.get("group_id") or "")
        if not group:
            return
        notice = event.get("notice_type") == "group_upload"
        if event.get("post_type") == "notice" and not notice:
            return
        if not notice and event.get("message_type") != "group":
            return
        segments = event.get("message") if isinstance(event.get("message"), list) else []
        files = []
        if notice and isinstance(event.get("file"), dict):
            files.append(event["file"])
        files.extend(
            item["data"]
            for item in segments
            if isinstance(item, dict) and item.get("type") == "file" and isinstance(item.get("data"), dict)
        )
        message_id = str(event.get("message_id") or "")
        if not message_id and not files:
            return
        now = self.clock()
        with self.write() as session:
            rule = session.scalar(
                select(ForwardRule).where(ForwardRule.source_group == group, ForwardRule.status == "active")
            )
            if rule is None:
                return
            event_key = f"group_capture:{group}"
            # 文件事件有时没有消息ID，保存独立文件引用；之后可由同文件消息补全。
            unique = (
                "msg:" + message_id
                if message_id
                else "file:" + str(files[0].get("id") or files[0].get("file_id") or "")
            )
            if unique == "file:":
                return
            if session.get(DeliveryAttempt, (event_key, unique)):
                return
            if message_id and files:
                remaining_files = []
                for file in files:
                    fid = str(file.get("file_id") or file.get("id") or "")
                    old = (
                        session.scalar(
                            select(ForwardAttachment)
                            .join(ForwardBatch)
                            .where(
                                ForwardBatch.source_group == group,
                                ForwardAttachment.file_id == fid,
                                ForwardAttachment.sender_id == str(event.get("user_id") or ""),
                                ForwardAttachment.message_id.is_(None),
                                ForwardBatch.closes_at > stamp(now - timedelta(minutes=2)),
                                ForwardBatch.status.in_(["collecting", "review"]),
                            )
                            .order_by(ForwardBatch.opened_at.desc())
                        )
                        if fid
                        else None
                    )
                    if old:
                        old.message_id = message_id
                    else:
                        remaining_files.append(file)
                # 关联文件不吞掉同条消息的文字，也不遗漏其余尚未登记的文件。
                files = remaining_files
            # 同文件的notice与message可能顺序颠倒；以文件ID关联，避免双收集。
            if notice:
                fid = str(files[0].get("id") or files[0].get("file_id") or "")
                old = session.scalar(
                    select(ForwardAttachment)
                    .join(ForwardBatch)
                    .where(
                        ForwardBatch.source_group == group,
                        ForwardAttachment.file_id == fid,
                        ForwardAttachment.sender_id == str(event.get("user_id") or ""),
                        ForwardBatch.closes_at > stamp(now - timedelta(minutes=2)),
                    )
                )
                if old:
                    return
            batch = session.scalar(
                select(ForwardBatch).where(
                    ForwardBatch.rule_id == rule.id,
                    ForwardBatch.status == "collecting",
                    ForwardBatch.closes_at > stamp(now),
                )
            )
            if batch is None:
                batch = ForwardBatch(
                    id=identifier("B"),
                    rule_id=rule.id,
                    source_group=group,
                    target_group=rule.target_group,
                    opened_at=stamp(now),
                    closes_at=stamp(now + timedelta(seconds=60)),
                    status="collecting",
                    messages_json="[]",
                    version=0,
                )
                session.add(batch)
                session.flush()
            sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
            texts = []
            for segment in segments:
                if not isinstance(segment, dict):
                    continue
                kind = segment.get("type")
                data = segment.get("data") if isinstance(segment.get("data"), dict) else {}
                if kind == "text":
                    texts.append(str(data.get("text", "")))
                elif kind != "file":
                    texts.append(f"[{kind}]")
            text = "".join(texts) if segments else str(event.get("raw_message") or event.get("message") or "")
            messages = json.loads(batch.messages_json)
            messages.append(
                {
                    "message_id": message_id,
                    "sender_id": str(event.get("user_id") or ""),
                    "sender": str(sender.get("card") or sender.get("nickname") or event.get("user_id") or "未知"),
                    "platform_time": event.get("time"),
                    "received_at": stamp(now),
                    "text": text,
                }
            )
            batch.messages_json = json.dumps(messages, ensure_ascii=False)
            for file in files:
                session.add(
                    ForwardAttachment(
                        id=identifier("A"),
                        batch_id=batch.id,
                        message_id=message_id or None,
                        file_id=str(file.get("file_id") or file.get("id") or ""),
                        filename=str(file.get("name") or file.get("file") or "未命名文件"),
                        sender_id=str(event.get("user_id") or ""),
                    )
                )
            session.add(DeliveryAttempt(request_id=event_key, item_key=unique, label="采集去重", status="confirmed"))

    def query(self, actor, kind="batches", identifier_value=None, page=1):
        """只返回有界快照；完整原文按单批次查看，避免无限模型上下文。"""
        self.authorize(actor)
        if page < 1:
            raise ValueError("页码必须大于0。")
        with closing(open_database(self.path)) as db:
            if kind == "rules":
                return {
                    "items": [
                        r.to_dict()
                        for r in db.session.scalars(
                            select(ForwardRule).order_by(ForwardRule.id).offset((page - 1) * 20).limit(20)
                        )
                    ]
                }
            if identifier_value:
                row = db.session.get(ForwardBatch, identifier_value)
                if not row:
                    return {"error": "找不到批次。"}
                result = row.to_dict()
                result["messages"] = json.loads(result.pop("messages_json"))
                result["attachments"] = [
                    a.to_dict()
                    for a in db.session.scalars(select(ForwardAttachment).where(ForwardAttachment.batch_id == row.id))
                ]
                preview = db.session.scalar(
                    select(ForwardPreview).where(
                        ForwardPreview.batch_id == row.id, ForwardPreview.version == row.version
                    )
                )
                result["preview"] = preview.to_dict() if preview else None
                result["receipts"] = [
                    r.to_dict()
                    for r in db.session.scalars(
                        select(DeliveryAttempt).where(
                            DeliveryAttempt.request_id.in_(
                                ["forward_review:" + row.id, f"forward_publish:{row.id}:V{row.version}"]
                            )
                        )
                    )
                ]
                return result
            rows = db.session.scalars(
                select(ForwardBatch)
                .order_by(ForwardBatch.opened_at.desc(), ForwardBatch.id)
                .offset((page - 1) * 20)
                .limit(21)
            ).all()
            return {
                "items": [{k: v for k, v in r.to_dict().items() if k != "messages_json"} for r in rows[:20]],
                "next_page": page + 1 if len(rows) > 20 else None,
            }

    def preview(self, actor, request_id, batch_id, body, attachment_ids=None):
        """Agent提供整理正文，服务绑定文件白名单和新版本；不确认、不发送。"""
        self.authorize(actor)
        body = body.strip()
        if not body or len(body) > 20000:
            raise ValueError("转发正文须为1至20000字。")

        def save(session):
            batch = session.get(ForwardBatch, batch_id)
            if batch is None or batch.status not in {"review", "preview"}:
                return "批次尚未结束、已放弃或已进入发布，不能生成预览。"
            files = list(session.scalars(select(ForwardAttachment).where(ForwardAttachment.batch_id == batch_id)))
            selected = [a.id for a in files] if attachment_ids is None else list(dict.fromkeys(attachment_ids))
            if not set(selected) <= {a.id for a in files}:
                return "附件清单包含不属于该批次的文件。"
            if any(not a.message_id for a in files if a.id in selected):
                return "部分文件没有可原生转发的原消息ID，请等待消息关联或明确排除后重新预览；不会下载上传。"
            selected_messages = {a.message_id for a in files if a.id in selected}
            if any(a.id not in selected and a.message_id in selected_messages for a in files):
                return "同一条原消息中的文件必须一起选择或一起排除，原生转发无法拆开消息。"
            batch.version += 1
            batch.status = "preview"
            names = [a.filename for a in files if a.id in selected]
            source = f"来源群：{batch.source_group}；消息窗口：{local(batch.opened_at)}—{local(batch.closes_at)}"
            frozen = body + "\n\n" + source
            session.add(
                ForwardPreview(
                    id=identifier("P"),
                    batch_id=batch.id,
                    version=batch.version,
                    body=frozen,
                    target_group=batch.target_group,
                    attachment_ids_json=json.dumps(selected),
                )
            )
            return (
                f"转发预览：{batch.id} V{batch.version}\n目标群：{batch.target_group}\n\n{frozen}\n\n附件：\n"
                + ("\n".join(names) or "无")
                + f"\n回复“确认转发 {batch.id} V{batch.version}”后发送。"
            )

        return self.command(request_id, save)

    def handle(self, actor, text, request_id):
        """仅私聊入口调用精确确认；模型不注册此接口。"""
        rule = re.fullmatch(r"确认规则 (F[A-F0-9]+) (R[A-F0-9]+)", text.strip())
        publish = re.fullmatch(r"确认转发 (B[A-F0-9]+) V(\d+)", text.strip())
        retry = re.fullmatch(r"确认补发转发 (B[A-F0-9]+) V(\d+) (R[A-F0-9]+)", text.strip())
        if not rule and not publish and not retry:
            return None
        self.authorize(actor)
        if rule:
            with closing(open_database(self.path)) as db:
                row = db.session.get(ForwardRule, rule[1])
                if row:
                    config = json.loads(row.config_json)
                    self.groups(row.source_group, config.get("target", row.target_group))

        def confirm(session):
            if retry:
                batch = session.get(ForwardBatch, retry[1])
                record = session.get(DeliveryAttempt, ("forward_retry:" + retry[1], retry[3]))
                snapshot = json.loads(record.payload_json) if record else {}
                if (
                    batch is None
                    or batch.version != int(retry[2])
                    or batch.status != "unknown"
                    or snapshot.get("expires_at", "") <= stamp(self.clock())
                    or record.status != "prepared"
                ):
                    return "补发预览不存在、已过期或状态已变化。"
                # 明确承认重复风险后才释放未知项目；已经接口确认的项目绝不重置。
                for item in session.scalars(
                    select(DeliveryAttempt).where(
                        DeliveryAttempt.request_id == f"forward_publish:{batch.id}:V{batch.version}",
                        DeliveryAttempt.status.in_(["unknown", "sending"]),
                    )
                ):
                    item.status = "failed"
                record.status = "confirmed"
                batch.status = "approved"
                return f"批次 {batch.id} 已按管理员确认补发，可能产生重复消息；已确认项目仍跳过。"
            if rule:
                row = session.get(ForwardRule, rule[1])
                config = json.loads(row.config_json) if row else {}
                if config.get("token") != rule[2] or config.get("expires_at", "") <= stamp(self.clock()):
                    return "规则预览不存在、已过期或版本已变化。"
                row.target_group, row.status = config["target"], "active"
                row.config_json = json.dumps({"target": row.target_group, "confirmed": True})
                return f"规则 {row.id} 已启用；群聊始终不回复。"
            row = session.get(ForwardBatch, publish[1])
            if row is None or row.version != int(publish[2]):
                return "预览版本不存在或已失效，请重新查看。"
            if row.status in {"approved", "publishing", "published", "partial", "unknown"}:
                return f"批次已处于{row.status}状态，不会重复发布。"
            if row.status != "preview":
                return "批次没有可确认预览。"
            row.status = "approved"
            return f"批次 {row.id} V{row.version} 已确认，等待发布。"

        return self.command(request_id, confirm)

    def control(self, actor, request_id, identifier_value, action):
        """暂停/恢复规则，放弃批次或重试明确失败项目；未知结果必须显式承认重复风险。"""
        self.authorize(actor)
        if action not in {"pause", "resume", "cancel", "discard", "retry", "retry_unknown"}:
            raise ValueError("不支持的管理操作。")

        def change(session):
            rule = session.get(ForwardRule, identifier_value)
            if rule:
                if action not in {"pause", "resume", "cancel"}:
                    return "规则仅支持暂停、恢复和取消。"
                if action == "resume" and rule.status != "paused":
                    return "只能恢复已暂停规则，草稿必须明确确认。"
                if action == "pause" and rule.status != "active":
                    return "只能暂停已启用规则，草稿必须明确确认。"
                rule.status = {"pause": "paused", "resume": "active", "cancel": "cancelled"}[action]
                return f"规则 {rule.id}：{rule.status}。已有批次保持原目标群。"
            batch = session.get(ForwardBatch, identifier_value)
            if not batch:
                return "找不到规则或批次。"
            if action == "discard" and batch.status != "published":
                batch.status = "discarded"
                return "批次已放弃，后续发送停止；已经提交给QQ的内容无法撤回。"
            if action == "retry" and batch.status == "partial":
                # 回到预览再次人工确认；已确认页仍跳过，结果未知页不会被重置。
                batch.status = "preview"
                return f"请再次发送“确认转发 {batch.id} V{batch.version}”，仅重试明确失败项目。"
            if action == "retry_unknown" and batch.status == "unknown":
                token = identifier("R")
                session.add(
                    DeliveryAttempt(
                        request_id="forward_retry:" + batch.id,
                        item_key=token,
                        label="未知结果补发预览",
                        status="prepared",
                        payload_json=json.dumps(
                            {
                                "expires_at": stamp(self.clock() + timedelta(minutes=30)),
                            }
                        ),
                    )
                )
                return f"警告：批次 {batch.id} V{batch.version} 的未知项目可能已经送达，补发可能重复。\n确认承担重复风险后，私聊发送“确认补发转发 {batch.id} V{batch.version} {token}”。30分钟有效；已确认项目不重发。"
            return "当前状态不能执行该操作；结果未知不自动重发。"

        return self.command(request_id, change)


def local(value):
    return datetime.fromisoformat(value).astimezone(BEIJING).strftime("%Y-%m-%d %H:%M:%S")
