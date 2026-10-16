"""通知契约与纯时间计算；统一使用北京时间，落盘时间统一使用 UTC。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

BEIJING = timezone(timedelta(hours=8))


def utc_now() -> datetime:
    return datetime.now(UTC)


def stamp(value: datetime) -> str:
    """固定格式 UTC ISO 字符串可用于 SQLite 时间比较。"""
    return value.astimezone(UTC).isoformat(timespec="microseconds")


@dataclass(frozen=True)
class NotificationSettings:
    """通知默认权限与运行参数；不允许模型覆盖。"""

    admin_id: str = "3244104494"
    poll_seconds: float = 5.0
    send_interval: float = 1.0
    grace_seconds: int = 1800
    draft_seconds: int = 1800
    lease_seconds: int = 60

    def __post_init__(self):
        if not self.admin_id.isdecimal() or not 5 <= len(self.admin_id) <= 12:
            raise ValueError("NOTIFICATION_ADMIN_QQ must be a QQ number")
        if not 0 <= self.send_interval <= 30:
            raise ValueError("NOTIFICATION_SEND_INTERVAL must be between 0 and 30 seconds")

    @classmethod
    def from_env(cls):
        return cls(
            admin_id=os.getenv("NOTIFICATION_ADMIN_QQ", "3244104494").strip(),
            send_interval=max(0.0, float(os.getenv("NOTIFICATION_SEND_INTERVAL", "1"))),
        )


class NotificationSpec(BaseModel):
    """模型只能输出候选通知；服务再次验证权限、时间和名单后才可确认。"""

    intent: Literal["none", "create", "update"] = "none"
    task_id: str | None = None
    target: Literal["all", "selected"] | None = None
    recipients: list[str] = Field(default_factory=list, max_length=5000)
    immediate: bool = False
    send_at: datetime | None = None
    frequency: Literal["once", "daily", "weekly"] = "once"
    weekdays: list[int] = Field(default_factory=list, max_length=7)
    until: date | None = None
    headline: str = Field(default="通知提醒", max_length=100)
    agenda: str = Field(default="", max_length=10000)
    location: str | None = Field(default=None, max_length=500)
    event_at: datetime | None = None
    event_time: str | None = None
    event_day_offset: int | None = Field(default=None, ge=0, le=366)
    questions: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("send_at", "event_at")
    @classmethod
    def localize(cls, value):
        if value is None:
            return None
        return (value.replace(tzinfo=BEIJING) if value.tzinfo is None else value).astimezone(BEIJING)

    @field_validator("weekdays")
    @classmethod
    def valid_weekdays(cls, value):
        if any(day not in range(7) for day in value):
            raise ValueError("星期必须为0至6（周一至周日）")
        return sorted(set(value))

    @field_validator("event_time")
    @classmethod
    def valid_time(cls, value):
        if value is not None:
            value = time.fromisoformat(value).strftime("%H:%M")
        return value

    @field_validator("recipients")
    @classmethod
    def valid_recipients(cls, value):
        if any(not item.isdecimal() or not 5 <= len(item) <= 12 for item in value):
            raise ValueError("接收者必须使用明确的 QQ 号")
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def valid_immediate(self):
        if self.immediate and self.frequency != "once":
            raise ValueError("立即发送不能同时设置周期")
        return self

    def missing(self) -> list[str]:
        """缺少关键信息时只追问；地点和活动时间允许省略。"""
        result = list(self.questions)
        if self.target is None or (self.target == "selected" and not self.recipients):
            result.append("发给全部好友，还是指定 QQ 号？")
        if not self.immediate and self.send_at is None:
            result.append("通知在什么日期、几点发送？")
        if not self.agenda.strip():
            result.append("通知的具体内容是什么？")
        if self.frequency == "weekly" and not self.weekdays:
            result.append("每周几发送？")
        if self.frequency != "once" and (
            self.event_at is not None or (self.event_time is None) != (self.event_day_offset is None)
        ):
            result.append("周期通知的活动在发送当天还是几天后、几点发生？")
        return list(dict.fromkeys(result))


def next_occurrence(spec: NotificationSpec, after: datetime, *, inclusive=False) -> datetime | None:
    """直接求下一次每天/每周执行，不逐次补排历史积压。"""
    if spec.send_at is None:
        return None
    anchor = spec.send_at.astimezone(BEIJING)
    after = after.astimezone(BEIJING)
    if spec.frequency == "once":
        return anchor if anchor > after or (inclusive and anchor == after) else None
    first_day = max(anchor.date(), after.date())
    for offset in range(8):
        day = first_day + timedelta(days=offset)
        if spec.until is not None and day > spec.until:
            return None
        candidate = datetime.combine(day, anchor.timetz())
        if spec.frequency == "weekly" and day.weekday() not in spec.weekdays:
            continue
        if candidate > after or (inclusive and candidate == after):
            return candidate
    return None


def render_body(spec: NotificationSpec, scheduled: datetime) -> str:
    """确定性渲染一份正文；周期日期由模板计算，不重新请求模型。"""
    event = spec.event_at
    if spec.frequency != "once" and spec.event_time is not None and spec.event_day_offset is not None:
        day = scheduled.astimezone(BEIJING).date() + timedelta(days=spec.event_day_offset)
        event = datetime.combine(day, time.fromisoformat(spec.event_time), BEIJING)
    lines = [spec.headline.strip() or "通知提醒"]
    if event:
        lines.append("时间：" + event.astimezone(BEIJING).strftime("%Y年%m月%d日 %H:%M"))
    if spec.location:
        lines.append("地点：" + spec.location.strip())
    lines.append("事项：" + spec.agenda.strip())
    return "\n".join(lines)
