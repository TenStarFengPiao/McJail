"""管理员通知服务：结构化草稿、查询与精确确认；不在此处直接广播。"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, exists, func, or_, select

from maidanlao.persistence.models import DeliveryAttempt, NotificationRun, NotificationTask

from .contracts import BEIJING, NotificationSettings, NotificationSpec, next_occurrence, render_body, stamp, utc_now
from .store import NotificationStore

logger = logging.getLogger(__name__)
COMMAND = re.compile(r"^(确认通知|确认取消通知|取消通知|查看通知|补发通知|确认补发)\s+([NR][A-F0-9]+)$")
STATUS_LABELS = {
    "draft": "待确认",
    "clarifying": "待补充",
    "superseded": "已替换",
    "expired": "已过期",
    "active": "已生效",
    "scheduled_done": "已完成排期",
    "queued": "待执行",
    "running": "执行中",
    "completed": "全部接口确认",
    "partial": "部分完成",
    "missed": "已错过",
    "cancelled": "已取消",
    "confirmed": "接口确认",
    "failed": "失败",
    "unknown": "结果未知",
    "skipped": "跳过/待发送",
}


class NotificationService:
    """权限来自入口真实 QQ 号；模型永远无法确认或发送通知。"""

    def __init__(self, db_path, client, *, settings=None, clock=utc_now):
        self.settings = settings or NotificationSettings.from_env()
        self.store = NotificationStore(db_path, self.settings)
        self.client, self.clock = client, clock

    def handle(self, actor: str, content: str, request_id: str, conversation: str, received_at=None) -> str | None:
        """只处理精确人工命令；自然语言交给统一 Agent，确认永远不经过模型。"""
        content = content.strip()
        command = COMMAND.fullmatch(content)
        listing = content == "查看通知任务"
        if actor != self.settings.admin_id:
            return "只有管理员可以管理通知任务。" if command or listing else None
        if not command and not listing and not content.startswith(("确认通知", "确认取消通知", "确认补发")):
            # 自然语言重放也走统一回答快照，不能改走旧回执分支而重复发送预览。
            return None
        cached = self.store.cached(request_id)
        if cached is not None:
            return cached
        now = self.clock()
        if listing:
            rows = self.store.tasks(actor)
            result = (
                "暂无通知任务。"
                if not rows
                else "通知任务：\n"
                + "\n".join(
                    f"{row['id']}：{STATUS_LABELS[row['status']]}；下次：{local_time(row['next_at'])}"
                    for row in rows[:100]
                )
            )
            return self.store.command(request_id, lambda _: result)
        if command:
            action, identifier = command.groups()
            if action == "查看通知":
                text = self.describe(identifier)
                return self.store.command(request_id, lambda _: text)
            if action in {"补发通知", "确认补发"}:
                return self._retry(action, identifier, request_id, now)
            if action == "确认取消通知":
                return self.confirm_cancel(actor, identifier, request_id)
            if action == "取消通知":
                return self.store.command(request_id, lambda session: self._cancel(session, identifier, actor))
            return self._confirm(identifier, request_id, now)
        if content.startswith(("确认通知", "确认取消通知", "确认补发")):
            return self.store.command(
                request_id, lambda _: "请使用完整命令：确认通知 任务号、确认取消通知 任务号，或确认补发 执行号。"
            )
        return None

    def authorize(self, actor):
        """服务层再次校验绑定身份；不能依赖模型选择或工具注册决定权限。"""
        if actor != self.settings.admin_id:
            raise PermissionError("只有管理员可以管理通知任务。")

    def drafts(self, actor, conversation):
        """返回当前会话未过期草稿；供 Agent 补全，不跨会话自动复用。"""
        self.authorize(actor)
        now = stamp(self.clock())
        return [
            {"id": row["id"], "status": row["status"], "spec": json.loads(row["spec_json"])}
            for row in self.store.tasks(actor)
            if row["conversation"] == conversation
            and row["status"] in {"draft", "clarifying"}
            and row["expires_at"] > now
        ][:10]

    def query(self, actor, *, status="pending", start=None, end=None, keyword="", page=1, page_size=20):
        """按北京时间区间查询任务；待发包含未完成批次，不把未确认草稿算成待发。"""
        from contextlib import closing

        from maidanlao.persistence.database import open_database

        self.authorize(actor)
        if status not in {"pending", "all", *STATUS_LABELS} or page < 1 or not 1 <= page_size <= 50:
            raise ValueError("查询状态或分页参数无效。")
        start, end = query_time(start), query_time(end)
        if start and end and start >= end:
            raise ValueError("开始时间必须早于结束时间。")
        now = stamp(self.clock())
        run_match = [NotificationRun.task_id == NotificationTask.id]
        conditions = [NotificationTask.owner_id == actor]
        if status == "pending":
            run_match.extend([NotificationRun.status.in_(["queued", "running"]), NotificationRun.deadline > now])
            task_match = and_(
                NotificationTask.status == "active",
                NotificationTask.next_at.is_not(None),
                NotificationTask.next_at >= stamp(self.clock() - timedelta(seconds=self.settings.grace_seconds)),
            )
            conditions.append(NotificationTask.status != "cancelled")
        elif status in {"queued", "running", "completed", "partial", "missed"}:
            task_match = False
            run_match.append(NotificationRun.status == status)
            if status in {"queued", "running"}:
                conditions.append(NotificationTask.status != "cancelled")
        elif status == "all":
            task_match = True
        else:
            task_match = NotificationTask.status == status
            if status in {"draft", "clarifying"}:
                task_match = and_(task_match, NotificationTask.expires_at > now)
            elif status == "expired":
                task_match = or_(
                    task_match,
                    and_(NotificationTask.status.in_(["draft", "clarifying"]), NotificationTask.expires_at <= now),
                )
            run_match.append(False)
        task_dates = []
        for bound, lower in ((start, True), (end, False)):
            if bound:
                task_dates.append(NotificationTask.next_at >= bound if lower else NotificationTask.next_at < bound)
                run_match.append(
                    NotificationRun.scheduled_at >= bound if lower else NotificationRun.scheduled_at < bound
                )
        conditions.append(or_(and_(task_match, *task_dates), exists(select(NotificationRun.id).where(*run_match))))
        if keyword.strip():
            pattern = "%" + keyword.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            conditions.append(
                or_(
                    *[
                        func.json_extract(NotificationTask.spec_json, path).like(pattern, escape="\\")
                        for path in ("$.agenda", "$.headline", "$.location")
                    ]
                )
            )
        with closing(open_database(self.store.path)) as db:
            total = db.session.scalar(select(func.count()).select_from(NotificationTask).where(*conditions))
            rows = [
                row.to_dict()
                for row in db.session.scalars(
                    select(NotificationTask)
                    .where(*conditions)
                    .order_by(NotificationTask.next_at.asc().nulls_last(), NotificationTask.id)
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ]
        items = []
        for row in rows:
            spec = NotificationSpec.model_validate_json(row["spec_json"])
            runs = self.store.runs(row["id"])
            effective = (
                "expired" if row["status"] in {"draft", "clarifying"} and row["expires_at"] <= now else row["status"]
            )
            items.append(
                {
                    "id": row["id"],
                    "status": effective,
                    "status_label": STATUS_LABELS[effective],
                    "agenda": spec.agenda,
                    "headline": spec.headline,
                    "location": spec.location,
                    "frequency": spec.frequency,
                    "next_at": local_time(row["next_at"]),
                    "recipient_count": len(json.loads(row["recipients_json"])),
                    "latest_execution": self.report(runs[0]["id"]).split("\n通知正文：", 1)[0] if runs else None,
                }
            )
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "next_page": page + 1 if page * page_size < total else None,
            "timezone": "Asia/Shanghai",
        }

    def detail(self, actor, identifier, *, include_recipients=False):
        """详情默认仅返回总数，管理员明确要求时才提供逐人回执。"""
        self.authorize(actor)
        run = self.store.run(identifier) if identifier.startswith("R") else None
        task = self.store.task(run["task_id"] if run else identifier)
        if not task or task["owner_id"] != actor:
            return {"error": "找不到这个通知任务或执行记录。"}
        runs = [run] if run else self.store.runs(task["id"])[:10]
        spec = NotificationSpec.model_validate_json(task["spec_json"])
        return {
            "id": task["id"],
            "status": task["status"],
            "spec": spec.model_dump(mode="json"),
            "next_at": local_time(task["next_at"]),
            "recipient_count": len(json.loads(task["recipients_json"])),
            "runs": [
                {"id": row["id"], "status": row["status"], "report": self.report(row["id"], details=include_recipients)}
                for row in runs
            ],
        }

    def prepare_cancel(self, actor, task_id, request_id):
        """持久化取消预览；绑定任务快照，确认前不影响调度。"""
        self.authorize(actor)
        now = self.clock()

        def save(session):
            task = session.get(NotificationTask, task_id)
            if task is None or task.owner_id != actor:
                return "找不到这个通知任务。"
            if task.status == "cancelled":
                return f"通知 {task_id} 已取消。"
            key = (task_id, "cancel_preview")
            payload = json.dumps(
                {
                    "actor": actor,
                    "expires_at": stamp(now + timedelta(seconds=self.settings.draft_seconds)),
                    "snapshot": cancel_snapshot(task),
                },
                ensure_ascii=False,
            )
            marker = session.get(DeliveryAttempt, key)
            if marker:
                marker.status, marker.payload_json = "prepared", payload
            else:
                session.add(
                    DeliveryAttempt(
                        request_id=task_id,
                        item_key="cancel_preview",
                        label="取消预览",
                        status="prepared",
                        payload_json=payload,
                    )
                )
            spec = NotificationSpec.model_validate_json(task.spec_json)
            return f"请确认取消通知 {task_id}：\n事项：{spec.agenda}\n下次发送：{local_time(task.next_at)}\n取消将阻止后续发送，已经提交给QQ的消息无法撤回。\n回复“确认取消通知 {task_id}”后生效，预览30分钟有效。"

        return self.store.command(request_id, save)

    def confirm_cancel(self, actor, task_id, request_id):
        """只供精确人工命令调用，原子复核预览期限与任务快照。"""
        self.authorize(actor)

        def confirm(session):
            task = session.get(NotificationTask, task_id)
            if task is None or task.owner_id != actor:
                return "找不到这个通知任务。"
            if task.status == "cancelled":
                return f"通知 {task_id} 已取消。"
            marker = session.get(DeliveryAttempt, (task_id, "cancel_preview"))
            payload = json.loads(marker.payload_json) if marker else {}
            if (
                not marker
                or marker.status != "prepared"
                or payload.get("actor") != actor
                or payload.get("expires_at", "") <= stamp(self.clock())
            ):
                return "取消预览不存在或已过期，请重新生成预览。"
            if payload.get("snapshot") != cancel_snapshot(task):
                return "任务状态已变化，请重新查询并生成取消预览。"
            marker.status = "consumed"
            return self._cancel(session, task_id, actor)

        return self.store.command(request_id, confirm)

    def prepare(self, actor, spec, request_id, conversation):
        """接受结构化候选，原子保存草稿；此方法不会激活任务或发送消息。"""
        self.authorize(actor)
        cached = self.store.cached(request_id)
        if cached is not None:
            return cached
        spec = NotificationSpec.model_validate(spec)
        if spec.intent == "none":
            return "没有通知变更，任务未生效。"
        if spec.intent == "update" and not spec.task_id:
            return "请先查询并选择要修改的任务号。"
        now = self.clock()
        previous_row = self.store.task(spec.task_id) if spec.task_id else None
        try:
            if spec.task_id:
                original = self.store.task(spec.task_id)
                if (
                    not original
                    or original["owner_id"] != actor
                    or original["status"] in {"cancelled", "superseded", "expired"}
                ):
                    return self.store.command(request_id, lambda _: "找不到可修改的通知任务，请提供正确任务号。")
                if original["status"] in {"draft", "clarifying"} and (
                    original["expires_at"] <= stamp(now) or original["conversation"] != conversation
                ):
                    return self.store.command(request_id, lambda _: "草稿已过期或属于其他会话，请重新生成预览。")
            missing = spec.missing()
            scheduled = now if spec.immediate else spec.send_at
            if not missing and not spec.immediate:
                if spec.frequency != "once":
                    scheduled = next_occurrence(spec, now, inclusive=True)
                elif scheduled < now:
                    missing.append("发送时间已经过去，请提供新的时间；不会自动顺延。")
                if scheduled is None:
                    missing.append("周期结束日期内没有可用发送时间，请调整日期。")
            recipients = [] if missing else self._recipients(spec)
        except Exception as exc:
            reason = str(exc) if isinstance(exc, ValueError) else "friend_api_unavailable"
            messages = {
                "recipient_not_friend": "指定接收者不在机器人的好友名单中，任务未生效，请核对QQ号或先添加好友。",
                "empty_friend_list": "机器人当前好友名单为空，任务未生效，请先添加接收者为好友。",
                "bot_identity_unavailable": "无法确认机器人QQ登录状态，任务未生效，请检查NapCat登录。",
            }
            # 只记录稳定错误码，不输出模型输入、账号密钥或原始 HTTP 错误正文。
            code = reason if reason in messages else "friend_api_unavailable"
            logger.warning("notification_friends_failed type=%s reason=%s", type(exc).__name__, code)
            text = messages.get(code, "获取或校验QQ好友名单失败，任务未生效，请检查NapCat连接后重试。")
            return self.store.command(request_id, lambda _: text)
        if previous_row and previous_row["status"] in {"draft", "clarifying"}:
            parent_id = json.loads(previous_row["spec_json"]).get("task_id")
            spec = spec.model_copy(update={"task_id": parent_id})
        task_id = "N" + uuid4().hex[:16].upper()
        expires = now + timedelta(seconds=self.settings.draft_seconds)

        def save(session):
            if previous_row:
                current = session.get(NotificationTask, previous_row["id"])
                if (
                    current is None
                    or current.status != previous_row["status"]
                    or current.spec_json != previous_row["spec_json"]
                ):
                    return "原任务状态已变化，请重新查询并生成预览。"
            if previous_row and previous_row["status"] in {"draft", "clarifying"}:
                old = session.get(NotificationTask, previous_row["id"])
                if old.status in {"draft", "clarifying"}:
                    old.status = "superseded"
            task = NotificationTask(
                id=task_id,
                source_request_id=request_id,
                owner_id=actor,
                conversation=conversation,
                status="clarifying" if missing else "draft",
                spec_json=spec.model_dump_json(),
                recipients_json=json.dumps(recipients),
                expires_at=stamp(expires),
                next_at=stamp(scheduled) if scheduled and not missing else None,
                created_at=stamp(now),
            )
            session.add(task)
            if missing:
                return "还需要确认这些信息：\n" + "\n".join(missing) + "\n请在30分钟内补充。"
            return preview(task.to_dict(), spec, scheduled)

        return self.store.command(request_id, save)

    def prepare_retry(self, actor, run_id, request_id):
        """仅准备补发风险预览；实际恢复批次由精确确认命令执行。"""
        self.authorize(actor)
        return self._retry("补发通知", run_id, request_id, self.clock())

    def _recipients(self, spec):
        """校验好友和机器人身份；不会用群成员或历史会话替代好友名单。"""
        self_id = str(self.client.get_login_info().get("user_id") or "")
        if not self_id.isdecimal():
            raise ValueError("bot_identity_unavailable")
        friends = self.client.get_friend_list()
        ids = list(
            dict.fromkeys(
                str(friend["user_id"])
                for friend in friends
                if str(friend.get("user_id", "")).isdecimal() and str(friend["user_id"]) != self_id
            )
        )
        if not ids:
            raise ValueError("empty_friend_list")
        if spec.target == "all":
            return ids
        if not set(spec.recipients) <= set(ids):
            raise ValueError("recipient_not_friend")
        return spec.recipients

    def _confirm(self, task_id, request_id, now):
        task = self.store.task(task_id)
        if task is None or task["owner_id"] != self.settings.admin_id:
            return self.store.command(request_id, lambda _: "找不到这个通知任务。")
        # 所有命令仍原子检查状态，网络校验不占数据库写锁。
        if task["status"] == "draft" and task["expires_at"] > stamp(now):
            try:
                spec = NotificationSpec.model_validate_json(task["spec_json"])
                current = self._recipients(
                    spec.model_copy(update={"target": "selected", "recipients": json.loads(task["recipients_json"])})
                )
                if not current:
                    raise ValueError("empty_recipients")
            except Exception:
                return self.store.command(
                    request_id, lambda _: "好友校验失败或接收者已不在好友名单，任务未生效，请重新生成预览。"
                )

        now = self.clock()

        def confirm(session):
            row = session.get(NotificationTask, task_id)
            if row.status in {"active", "scheduled_done"}:
                return f"通知 {task_id} 已生效，请勿重复创建。"
            if row.status != "draft":
                return "任务不是可确认草稿，请重新生成预览。"
            if row.expires_at <= stamp(now):
                row.status = "expired"
                return "草稿已超过30分钟，请重新发送指令。"
            spec = NotificationSpec.model_validate_json(row.spec_json)
            if not spec.immediate and row.next_at < stamp(now):
                row.status = "expired"
                return "预览中的发送时间已过去，请重新生成预览。"
            if spec.task_id:
                old = session.get(NotificationTask, spec.task_id)
                if old is None or old.owner_id != row.owner_id:
                    return "原任务不存在，修改未生效。"
                if old.status in {"cancelled", "superseded", "expired"}:
                    return "原任务状态已变化，修改未生效，请重新预览。"
                old.status = "cancelled"
                old.next_at = None
            row.status = "active"
            if spec.immediate:
                row.next_at = stamp(now)
            return f"通知 {task_id} 已确认，发送时间：{local_time(row.next_at)}。接收名单已固定。"

        return self.store.command(request_id, confirm)

    @staticmethod
    def _cancel(session, task_id, actor):
        row = session.get(NotificationTask, task_id)
        if row is None or row.owner_id != actor:
            return "找不到这个通知任务。"
        row.status, row.next_at = "cancelled", None
        return f"通知 {task_id} 已取消，后续接收者和未来周期将停止发送；已提交给QQ的消息无法撤回。"

    def describe(self, identifier):
        """查询冻结正文和逐人回执，结果未知不会冒充发送成功。"""
        if identifier.startswith("R"):
            runs = [self.store.run(identifier)]
            if runs[0] is None:
                return "找不到这个执行记录。"
            task = self.store.task(runs[0]["task_id"])
        else:
            task, runs = self.store.task(identifier), self.store.runs(identifier)
        if not task or task["owner_id"] != self.settings.admin_id:
            return "找不到这个通知任务。"
        text = f"通知 {task['id']}：{STATUS_LABELS[task['status']]}\n下次：{local_time(task['next_at'])}"
        for run in runs[:10]:
            text += "\n\n" + self.report(run["id"], details=True)
        return text

    def report(self, run_id, *, details=False):
        from contextlib import closing

        from maidanlao.persistence.database import open_database

        run = self.store.run(run_id)
        users = json.loads(run["recipients_json"])
        pages = (len(run["body"]) + 1999) // 2000
        counts = dict.fromkeys(["confirmed", "failed", "unknown", "skipped"], 0)
        lines = []
        with closing(open_database(self.store.path)) as db:
            receipts_by_key = {
                row.item_key: row.status
                for row in db.session.scalars(select(DeliveryAttempt).where(DeliveryAttempt.request_id == run_id))
            }
            for user in users:
                states = {receipts_by_key.get(f"user:{user}:page:{index}", "skipped") for index in range(pages)}
                state = recipient_state(states)
                counts[state] += 1
                if details:
                    lines.append(f"{user}：{STATUS_LABELS[state]}")
        return (
            f"通知执行 {run_id}（任务 {run['task_id']}）：{STATUS_LABELS[run['status']]}\n总人数：{len(users)}；"
            f"接口确认：{counts['confirmed']}；失败：{counts['failed']}；结果未知：{counts['unknown']}；跳过/待发送：{counts['skipped']}\n"
            "接口确认仅表示QQ接口接受，不表示用户已读。\n通知正文：\n"
            + run["body"]
            + ("\n接收明细：\n" + "\n".join(lines) if details else "")
        )

    def _retry(self, action, run_id, request_id, now):
        """补发未知结果必须二次明确确认；保留已确认页及原正文。"""
        run = self.store.run(run_id)
        task = self.store.task(run["task_id"]) if run else None
        if not task or task["owner_id"] != self.settings.admin_id:
            return self.store.command(request_id, lambda _: "请提供有效执行号（R开头），可以先查看通知任务。")

        def retry(session):
            row = session.get(NotificationRun, run_id)
            parent = session.get(NotificationTask, row.task_id)
            if parent.status == "cancelled" or row.status not in {"completed", "partial", "missed"}:
                return "任务已取消或正在执行，不能补发。"
            key = (run_id, "retry_preview")
            marker = session.get(DeliveryAttempt, key)
            if action == "补发通知":
                payload = json.dumps({"expires_at": stamp(now + timedelta(seconds=self.settings.draft_seconds))})
                if marker:
                    marker.status, marker.payload_json = "prepared", payload
                else:
                    session.add(
                        DeliveryAttempt(
                            request_id=run_id,
                            item_key="retry_preview",
                            label="补发预览",
                            status="prepared",
                            payload_json=payload,
                        )
                    )
                return f"将使用原正文补发 {run_id} 中失败、跳过及结果未知的项目，已确认页不重发。\n结果未知的接收者可能收到重复通知。回复“确认补发 {run_id}”才会执行。"
            if not marker or marker.status != "prepared" or json.loads(marker.payload_json)["expires_at"] <= stamp(now):
                return f"请先发送“补发通知 {run_id}”并查看重复风险，再明确确认。"
            receipts = list(
                session.scalars(
                    select(DeliveryAttempt).where(
                        DeliveryAttempt.request_id == run_id, DeliveryAttempt.item_key.like("user:%")
                    )
                )
            )
            if receipts and all(receipt.status == "confirmed" for receipt in receipts):
                return "所有项目已由接口确认，没有需要补发的内容。"
            for receipt in receipts:
                if receipt.status != "confirmed":
                    session.delete(receipt)
            row.progress_json = "{}"
            row.status, row.lease_owner, row.lease_until = "queued", None, None
            row.deadline = stamp(now + timedelta(seconds=self.settings.grace_seconds))
            report = session.get(DeliveryAttempt, (run_id, "admin_report"))
            if report:
                session.delete(report)
            marker.status = "consumed"
            return f"补发 {run_id} 已确认，将沿用原正文及原接收名单。"

        return self.store.command(request_id, retry)


def local_time(value):
    return datetime.fromisoformat(value).astimezone(BEIJING).strftime("%Y年%m月%d日 %H:%M") if value else "无"


def query_time(value):
    """无偏移查询参数按北京时间解释；查询区间为左闭右开。"""
    if not value:
        return None
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    return stamp(parsed.replace(tzinfo=BEIJING) if parsed.tzinfo is None else parsed)


def cancel_snapshot(task):
    """只比较会影响取消含义的任务字段，不以模型提供的快照为依据。"""
    return {"status": task.status, "next_at": task.next_at, "spec_json": task.spec_json}


def recipient_state(states):
    """用户所有页均确认才计为确认；未知优先，避免隐藏不确定的外部副作用。"""
    if states == {"confirmed"}:
        return "confirmed"
    if states & {"sending", "unknown"}:
        return "unknown"
    if "failed" in states:
        return "failed"
    return "skipped"


def preview(task, spec, scheduled):
    mode = {"once": "单次", "daily": "每天", "weekly": "每周"}[spec.frequency]
    recipients = json.loads(task["recipients_json"])
    target = "全部QQ好友" if spec.target == "all" else "指定QQ：" + "、".join(recipients)
    body = render_body(spec, scheduled)
    return (
        f"请确认这条通知：\n任务号：{task['id']}\n发送时间：{local_time(stamp(scheduled))}（北京时间）\n"
        f"周期：{mode}\n接收对象：{target}，共{len(recipients)}人\n"
        + (
            "每周：" + "、".join("一二三四五六日"[day] for day in spec.weekdays) + "\n"
            if spec.frequency == "weekly"
            else ""
        )
        + (f"结束日期：{spec.until.isoformat()}\n" if spec.until else "")
        + "通知正文：\n"
        + body
        + f"\n\n回复“确认通知 {task['id']}”后生效。草稿30分钟有效。"
    )
