"""通知工作单元：写操作先领取 SQLite 锁，网络请求始终在事务外。"""

import json
from contextlib import closing, contextmanager
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from maidanlao.persistence.database import open_database
from maidanlao.persistence.models import DeliveryAttempt, NotificationRun, NotificationTask

from .contracts import NotificationSettings, NotificationSpec, next_occurrence, render_body, stamp

FINAL_STATUSES = {"completed", "partial", "missed", "cancelled"}


class NotificationStore:
    """短事务存储；返回字典快照，禁止把 ORM 对象传入网络发送流程。"""

    def __init__(self, path, settings: NotificationSettings):
        self.path, self.settings = path, settings

    @contextmanager
    def write(self):
        with closing(open_database(self.path)) as db, db.session.begin():
            db.session.connection(execution_options={"write_lock": True})
            yield db.session

    def cached(self, request_id):
        with closing(open_database(self.path)) as db:
            row = db.session.get(DeliveryAttempt, (request_id, "notification_command"))
            return json.loads(row.payload_json)["text"] if row else None

    def command(self, request_id, callback):
        """命令结果与状态变更原子保存；消息重放不重新建任务或激活补发。"""
        with self.write() as session:
            row = session.get(DeliveryAttempt, (request_id, "notification_command"))
            if row:
                return json.loads(row.payload_json)["text"]
            text = callback(session)
            session.add(
                DeliveryAttempt(
                    request_id=request_id,
                    item_key="notification_command",
                    label="通知管理回执",
                    status="prepared",
                    payload_json=json.dumps({"text": text}, ensure_ascii=False),
                )
            )
            return text

    def task(self, task_id):
        with closing(open_database(self.path)) as db:
            row = db.session.get(NotificationTask, task_id)
            return row.to_dict() if row else None

    def tasks(self, owner):
        with closing(open_database(self.path)) as db:
            return [
                row.to_dict()
                for row in db.session.scalars(
                    select(NotificationTask)
                    .where(NotificationTask.owner_id == owner)
                    .order_by(NotificationTask.created_at.desc())
                )
            ]

    def run(self, run_id):
        with closing(open_database(self.path)) as db:
            row = db.session.get(NotificationRun, run_id)
            return row.to_dict() if row else None

    def runs(self, task_id):
        with closing(open_database(self.path)) as db:
            return [
                row.to_dict()
                for row in db.session.scalars(
                    select(NotificationRun)
                    .where(NotificationRun.task_id == task_id)
                    .order_by(NotificationRun.scheduled_at.desc())
                )
            ]

    def materialize(self, now: datetime):
        """到期任务每次只生成一个运行快照；周期任务直接跳到未来，避免积压广播。"""
        with self.write() as session:
            for task in session.scalars(
                select(NotificationTask).where(
                    NotificationTask.status == "active", NotificationTask.next_at <= stamp(now)
                )
            ):
                scheduled = datetime.fromisoformat(task.next_at)
                spec = NotificationSpec.model_validate_json(task.spec_json)
                if spec.frequency != "once":
                    # 恢复时优先检查最近一轮；旧积压不掩盖今天仍在补发窗口内的提醒。
                    recent = next_occurrence(spec, now - timedelta(days=8), inclusive=True)
                    while recent is not None and recent <= now:
                        scheduled = recent
                        recent = next_occurrence(spec, recent)
                session.add(
                    NotificationRun(
                        id="R" + uuid4().hex[:16].upper(),
                        task_id=task.id,
                        scheduled_at=stamp(scheduled),
                        deadline=stamp(scheduled + timedelta(seconds=self.settings.grace_seconds)),
                        status="queued",
                        body=render_body(spec, scheduled),
                        recipients_json=task.recipients_json,
                        progress_json="{}",
                    )
                )
                upcoming = next_occurrence(spec, now)
                task.next_at = stamp(upcoming) if upcoming else None
                if upcoming is None:
                    task.status = "scheduled_done"

    def available(self, now):
        with closing(open_database(self.path)) as db:
            return list(
                db.session.scalars(
                    select(NotificationRun.id)
                    .where(
                        NotificationRun.status.in_(["queued", "running"]),
                        (NotificationRun.lease_until.is_(None)) | (NotificationRun.lease_until <= stamp(now)),
                    )
                    .order_by(NotificationRun.scheduled_at)
                )
            )

    def claim(self, run_id, worker, now):
        with self.write() as session:
            run = session.get(NotificationRun, run_id)
            if run is None or run.status in FINAL_STATUSES:
                return None
            if run.lease_until and run.lease_until > stamp(now):
                return None
            run.lease_owner = worker
            run.lease_until = stamp(now + timedelta(seconds=self.settings.lease_seconds))
            run.status = "running"
            return run.to_dict()

    def before_send(self, run_id, worker, item_key, now):
        """每个网络动作前检查取消、截止时间和租约；领取回执与重试计数原子落盘。"""
        with self.write() as session:
            run = session.get(NotificationRun, run_id)
            if run.lease_owner != worker or not run.lease_until or run.lease_until <= stamp(now):
                return "lost"
            task = session.get(NotificationTask, run.task_id)
            if task.status == "cancelled":
                return "cancelled"
            if stamp(now) > run.deadline:
                return "expired"
            progress = json.loads(run.progress_json)
            entry = progress.setdefault(item_key, {"attempts": 0})
            receipt = session.get(DeliveryAttempt, (run_id, item_key))
            if receipt and receipt.status == "confirmed":
                return "confirmed"
            if receipt and receipt.status in {"unknown", "sending"}:
                receipt.status = "unknown"
                return "unknown"
            if entry["attempts"] >= 3:
                return "failed"
            if entry.get("retry_at", "") > stamp(now):
                return "wait"
            entry["attempts"] += 1
            run.progress_json = json.dumps(progress)
            run.lease_until = stamp(now + timedelta(seconds=self.settings.lease_seconds))
            if receipt:
                receipt.status = "sending"
            else:
                session.add(DeliveryAttempt(request_id=run_id, item_key=item_key, label=item_key, status="sending"))
            return "send"

    def record(self, run_id, worker, item_key, status, now):
        """持有租约才更新回执；发送后丢失租约的项目保留在途，下一次按未知处理。"""
        with self.write() as session:
            run = session.get(NotificationRun, run_id)
            if run.lease_owner != worker or run.lease_until <= stamp(now):
                return False
            receipt = session.get(DeliveryAttempt, (run_id, item_key))
            receipt.status = status
            progress = json.loads(run.progress_json)
            entry = progress[item_key]
            if status == "failed":
                delay = 30 if entry["attempts"] == 1 else 120
                entry["retry_at"] = stamp(now + timedelta(seconds=delay))
            run.progress_json = json.dumps(progress)
            run.lease_until = stamp(now + timedelta(seconds=self.settings.lease_seconds))
            return True

    def settle(self, run_id, worker, now):
        """汇总逐页回执；待重试则释放租约，完成批次等待单独发送管理员报告。"""
        with self.write() as session:
            run = session.get(NotificationRun, run_id)
            if run.lease_owner != worker:
                return
            task = session.get(NotificationTask, run.task_id)
            progress = json.loads(run.progress_json)
            receipts = {
                r.item_key: r
                for r in session.scalars(select(DeliveryAttempt).where(DeliveryAttempt.request_id == run_id))
            }
            keys = delivery_keys(run.to_dict())
            cancelled = task.status == "cancelled"
            expired = stamp(now) > run.deadline
            pending = False
            blocked_user = None
            for key in keys:
                receipt = receipts.get(key)
                user = key.split(":")[1]
                if receipt and receipt.status == "sending":
                    receipt.status = "unknown"
                terminal_failure = receipt and (
                    receipt.status == "unknown"
                    or (receipt.status == "failed" and progress.get(key, {}).get("attempts", 0) >= 3)
                )
                if terminal_failure:
                    blocked_user = user
                retryable = receipt is None or (
                    receipt.status == "failed" and progress.get(key, {}).get("attempts", 0) < 3
                )
                if retryable:
                    if cancelled or expired or blocked_user == user:
                        if receipt is None:
                            session.add(DeliveryAttempt(request_id=run_id, item_key=key, label=key, status="skipped"))
                    else:
                        pending = True
            if pending:
                run.status = "queued"
            else:
                confirmed = sum(1 for key in keys if key in receipts and receipts[key].status == "confirmed")
                if cancelled:
                    run.status = "cancelled"
                elif confirmed == len(keys):
                    run.status = "completed"
                elif expired and confirmed == 0:
                    run.status = "missed"
                else:
                    run.status = "partial"
            run.lease_owner = run.lease_until = None

    def reports_due(self):
        with closing(open_database(self.path)) as db:
            runs = db.session.scalars(select(NotificationRun).where(NotificationRun.status.in_(FINAL_STATUSES)))
            return [run.id for run in runs if db.session.get(DeliveryAttempt, (run.id, "admin_report")) is None]


def delivery_keys(run):
    """分页仅依赖冻结正文，所有接收者使用同一分页边界。"""
    pages = (len(run["body"]) + 1999) // 2000
    return [f"user:{user}:page:{index}" for user in json.loads(run["recipients_json"]) for index in range(pages)]
