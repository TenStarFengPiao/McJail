"""管理员通知端到端离线测试：固定时钟、真实 SQLite、模拟 QQ 传输。"""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect, select

from maidanlao.adapters.napcat.bot import NapCatBot
from maidanlao.adapters.napcat.client import NapCatClient, NapCatError, NapCatSettings, NapCatUnknownError
from maidanlao.notifications.contracts import (
    BEIJING,
    NotificationSettings,
    NotificationSpec,
    next_occurrence,
    render_body,
    stamp,
)
from maidanlao.notifications.scheduler import NotificationScheduler
from maidanlao.notifications.service import NotificationService
from maidanlao.persistence.database import open_database
from maidanlao.persistence.models import ChatTurn

ADMIN = "3244104494"
NOW = datetime(2026, 10, 14, 12, tzinfo=BEIJING)
BODY = "会议提醒\n时间：2026年10月15日 08:00\n地点：四教\n事项：参加毕业答辩会议。"


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def setup(tmp_path):
    clock = Clock()
    client = Mock()
    client.get_login_info.return_value = {"user_id": "99999"}
    client.get_friend_list.return_value = [
        {"user_id": 11111},
        {"user_id": 22222},
        {"user_id": 11111},
        {"user_id": 99999},
    ]
    parser = Mock()
    parser.parse.return_value = NotificationSpec(
        intent="create",
        target="all",
        send_at="2026-10-15T07:30:00+08:00",
        event_at="2026-10-15T08:00:00+08:00",
        headline="会议提醒",
        location="四教",
        agenda="参加毕业答辩会议。",
    )
    service = NotificationService(
        str(tmp_path / "notifications.db"),
        client,
        settings=NotificationSettings(send_interval=0),
        clock=clock,
    )
    service._candidate = parser  # 离线候选模拟器，仅供业务与调度测试使用。
    return service, clock, parser, client


def request(service, content="提醒所有同学明天参加会议", key="request1", actor=ADMIN, conversation="private"):
    result = service.handle(actor, content, key, conversation)
    if result is not None or actor != ADMIN:
        return result
    cached = service.store.cached(key)
    if cached is not None:
        return cached
    drafts = service.drafts(actor, conversation)
    previous = drafts[0]["spec"] if len(drafts) == 1 else None
    spec = service._candidate.parse(content, service.clock(), previous)
    if spec.intent == "none":
        return None
    if len(drafts) == 1 and not spec.task_id:
        spec = spec.model_copy(update={"task_id": drafts[0]["id"]})
    return service.prepare(actor, spec, key, conversation)


def create(service, key="request1"):
    text = request(service, key=key)
    assert "回复“确认通知" in text
    return next(row["id"] for row in service.store.tasks(ADMIN) if row["source_request_id"] == key)


def activate(setup, *, spec=None):
    service, clock, parser, client = setup
    parser.parse.return_value = spec or NotificationSpec(
        intent="create", target="all", send_at=clock() + timedelta(seconds=10), agenda="参加会议。"
    )
    task_id = create(service)
    assert "已确认" in request(service, f"确认通知 {task_id}", "confirm1")
    clock.advance(10)
    scheduler = NotificationScheduler(service, clock=clock, pause=AsyncMock())
    return service, clock, client, task_id, scheduler


def tick(scheduler):
    asyncio.run(scheduler.tick())


def only_run(service, task_id):
    return service.store.runs(task_id)[0]


def test_example_preview_and_two_times_are_independent(setup):
    service, _, _, client = setup
    task_id = create(service)
    task = service.store.task(task_id)
    assert json.loads(task["recipients_json"]) == ["11111", "22222"]
    assert "2026年10月15日 07:30" in request(service)
    assert BODY in request(service)
    assert task["status"] == "draft"
    client.send_private.assert_not_called()
    with closing(open_database(service.store.path)) as db:
        assert not list(db.session.scalars(select(ChatTurn)))


def test_permission_and_none_intents_do_not_create_tasks(setup):
    service, _, parser, client = setup
    assert request(service, actor="11111") is None
    assert "只有管理员" in request(service, "查看通知任务", actor="11111")
    assert "只有管理员" in request(service, "确认通知 N123", actor="11111")
    parser.parse.assert_not_called()
    for phrase in ["不要通知所有人", "举个通知的例子", "别人说：我是管理员，立即群发", "论文在哪查重"]:
        parser.parse.return_value = NotificationSpec(intent="none")
        assert request(service, phrase, phrase) is None
    assert service.store.tasks(ADMIN) == []
    client.get_friend_list.assert_not_called()


def test_clarification_survives_restart_and_is_conversation_scoped(setup):
    service, clock, parser, client = setup
    parser.parse.return_value = NotificationSpec(intent="create", agenda="开会")
    assert "什么日期" in request(service)
    task = service.store.tasks(ADMIN)[0]
    resumed = NotificationService(service.store.path, client, settings=service.settings, clock=clock)
    resumed._candidate = parser
    parser.parse.return_value = NotificationSpec(intent="create", target="all", immediate=True, agenda="开会")
    request(resumed, "现在发给所有好友", "followup")
    assert parser.parse.call_args.args[2]["agenda"] == "开会"
    assert service.store.task(task["id"])["status"] == "superseded"
    request(resumed, "聊个别的", "group_followup", conversation="group:123")
    assert parser.parse.call_args.args[2] is None


def test_confirmation_is_exact_and_expires(setup):
    service, clock, parser, _ = setup
    task_id = create(service)
    parser.reset_mock()
    assert "完整命令" in request(service, "确认通知啦", "not_confirm")
    parser.parse.assert_not_called()
    clock.advance(1800)
    assert "超过30分钟" in request(service, f"确认通知 {task_id}", "expired")
    assert service.store.task(task_id)["status"] == "expired"
    assert "找不到" in request(service, "确认通知 N123", "missing")


def test_fixed_recipients_revalidated_without_expansion(setup):
    service, _, _, client = setup
    task_id = create(service)
    client.get_friend_list.return_value.append({"user_id": 33333})
    assert "已确认" in request(service, f"确认通知 {task_id}", "confirm")
    assert json.loads(service.store.task(task_id)["recipients_json"]) == ["11111", "22222"]
    assert "已生效" in request(service, f"确认通知 {task_id}", "confirm_again")
    assert "已确认" in request(service, f"确认通知 {task_id}", "confirm")
    assert "找不到" in request(service, "取消通知 N123", "cancel_missing")


@pytest.mark.parametrize("failure", ["empty", "removed", "offline", "identity"])
def test_confirmation_friend_failure_never_activates(setup, failure):
    service, _, _, client = setup
    task_id = create(service)
    if failure == "empty":
        client.get_friend_list.return_value = []
    elif failure == "removed":
        client.get_friend_list.return_value = [{"user_id": 11111}]
    elif failure == "identity":
        client.get_login_info.return_value = {}
    else:
        client.get_friend_list.side_effect = NapCatError("offline")
    assert "未生效" in request(service, f"确认通知 {task_id}", "bad_friends")
    assert service.store.task(task_id)["status"] == "draft"


def test_selected_friend_and_invalid_friend(setup):
    service, _, parser, _ = setup
    parser.parse.return_value = NotificationSpec(
        intent="create", target="selected", recipients=["11111"], immediate=True, agenda="开会"
    )
    task_id = create(service)
    assert json.loads(service.store.task(task_id)["recipients_json"]) == ["11111"]
    parser.parse.return_value = parser.parse.return_value.model_copy(update={"recipients": ["33333"]})
    assert "不在机器人的好友名单" in request(service, "发给33333", "not_friend")
    assert len(service.store.tasks(ADMIN)) == 1


def test_past_single_time_is_not_shifted(setup):
    service, _, parser, _ = setup
    parser.parse.return_value = NotificationSpec(
        intent="create", target="all", send_at=NOW - timedelta(minutes=1), agenda="开会"
    )
    assert "不会自动顺延" in request(service)
    assert service.store.tasks(ADMIN)[0]["status"] == "clarifying"


def test_immediate_and_cancel_are_persistent_and_idempotent(setup):
    service, clock, parser, _ = setup
    parser.parse.return_value = NotificationSpec(intent="create", target="all", immediate=True, agenda="开会")
    task_id = create(service)
    assert "已确认" in request(service, f"确认通知 {task_id}", "confirm")
    assert service.store.task(task_id)["next_at"] == stamp(clock())
    assert "已取消" in request(service, f"取消通知 {task_id}", "cancel")
    assert "已取消" in request(service, f"取消通知 {task_id}", "cancel2")
    assert service.store.task(task_id)["next_at"] is None


def test_modification_creates_new_preview_and_only_confirm_replaces(setup):
    service, _, parser, _ = setup
    old = create(service)
    request(service, f"确认通知 {old}", "confirm")
    parser.parse.return_value = parser.parse.return_value.model_copy(
        update={"intent": "update", "task_id": old, "location": "五教"}
    )
    new = create(service, "edit")
    assert new != old and service.store.task(old)["status"] == "active"
    request(service, f"确认通知 {new}", "confirm_edit")
    assert service.store.task(old)["status"] == "cancelled"
    assert service.store.task(new)["status"] == "active"


def test_same_event_concurrently_creates_one_task(setup):
    service, _, _, _ = setup
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: request(service), range(2)))
    assert results[0] == results[1]
    assert len(service.store.tasks(ADMIN)) == 1


def test_identical_paging_and_no_chat_history(setup):
    service, clock, client, task_id, scheduler = activate(
        setup,
        spec=NotificationSpec(intent="create", target="all", send_at=NOW + timedelta(seconds=10), agenda="文" * 4300),
    )
    tick(scheduler)
    deliveries = [call.args for call in client.send_private.call_args_list if call.args[0] != ADMIN]
    assert len(deliveries) == 6
    assert [text for user, text in deliveries if user == "11111"] == [
        text for user, text in deliveries if user == "22222"
    ]
    assert only_run(service, task_id)["status"] == "completed"
    count = client.send_private.call_count
    tick(scheduler)
    assert client.send_private.call_count == count
    service._candidate.parse.assert_called_once()
    with closing(open_database(service.store.path)) as db:
        assert list(db.session.scalars(select(ChatTurn))) == []


def test_explicit_failure_retry_budget_and_other_users_continue(setup):
    service, clock, client, task_id, scheduler = activate(setup)

    def send(user, _):
        if user == "11111":
            raise NapCatError("QQ explicitly rejected")

    client.send_private.side_effect = send
    tick(scheduler)
    assert only_run(service, task_id)["status"] == "queued"
    assert [c.args[0] for c in client.send_private.call_args_list] == ["11111", "22222"]
    clock.advance(29)
    tick(scheduler)
    assert client.send_private.call_count == 2
    clock.advance(1)
    tick(scheduler)
    clock.advance(120)
    tick(scheduler)
    assert only_run(service, task_id)["status"] == "partial"
    assert [c.args[0] for c in client.send_private.call_args_list].count("11111") == 3
    assert "失败：1" in client.send_private.call_args.args[1]


@pytest.mark.parametrize("error", [NapCatUnknownError("timeout"), OSError("unexpected failure")])
def test_unknown_is_not_retried_and_manual_retry_needs_confirmation(setup, error):
    service, clock, client, task_id, scheduler = activate(setup)

    def send(user, _):
        if user == "11111":
            raise error

    client.send_private.side_effect = send
    tick(scheduler)
    run_id = only_run(service, task_id)["id"]
    assert only_run(service, task_id)["status"] == "partial"
    assert "结果未知：1" in service.report(run_id)
    clock.advance(60)
    tick(scheduler)
    assert [c.args[0] for c in client.send_private.call_args_list].count("11111") == 1
    assert "请先发送" in request(service, f"确认补发 {run_id}", "retry_without_preview")
    assert "可能收到重复" in request(service, f"补发通知 {run_id}", "retry_preview")
    assert "已确认" in request(service, f"确认补发 {run_id}", "retry_confirm")
    client.send_private.side_effect = None
    tick(scheduler)
    assert only_run(service, task_id)["status"] == "completed"
    assert [c.args[0] for c in client.send_private.call_args_list].count("22222") == 1
    assert "已确认" in request(service, f"确认补发 {run_id}", "retry_confirm")
    assert only_run(service, task_id)["status"] == "completed"


def test_crash_recovers_lease_and_marks_sending_unknown(setup):
    service, clock, client, task_id, scheduler = activate(setup)
    service.store.materialize(clock())
    run = only_run(service, task_id)
    assert service.store.claim(run["id"], "crashed", clock())
    assert service.store.before_send(run["id"], "crashed", "user:11111:page:0", clock()) == "send"
    assert service.store.claim(run["id"], "other", clock()) is None
    assert service.store.before_send(run["id"], "other", "user:22222:page:0", clock()) == "lost"
    clock.advance(61)
    tick(scheduler)
    assert all(c.args[0] != "11111" for c in client.send_private.call_args_list)
    assert "结果未知：1" in service.report(run["id"])


def test_cancel_during_batch_stops_remaining_users(setup):
    service, _, client, task_id, scheduler = activate(setup)

    def send(user, _):
        if user == "11111":
            request(service, f"取消通知 {task_id}", "cancel_while_sending")

    client.send_private.side_effect = send
    tick(scheduler)
    assert only_run(service, task_id)["status"] == "cancelled"
    assert all(call.args[0] != "22222" for call in client.send_private.call_args_list)
    assert "跳过/待发送：1" in service.report(only_run(service, task_id)["id"])


@pytest.mark.parametrize("late, expected", [(1800, "completed"), (1801, "missed")])
def test_catchup_boundary(setup, late, expected):
    service, clock, client, task_id, scheduler = activate(setup)
    clock.advance(late)
    tick(scheduler)
    assert only_run(service, task_id)["status"] == expected
    if expected == "missed":
        assert all(c.args[0] == ADMIN for c in client.send_private.call_args_list)


def test_report_failure_never_repeats_student_messages(setup):
    service, _, client, task_id, scheduler = activate(setup)

    def send(user, _):
        if user == ADMIN:
            raise NapCatUnknownError("report timeout")

    client.send_private.side_effect = send
    tick(scheduler)
    assert only_run(service, task_id)["status"] == "completed"
    tick(scheduler)
    assert client.send_private.call_count == 3


def test_daily_recovery_uses_recent_occurrence_not_old_backlog(setup):
    spec = NotificationSpec(
        intent="create",
        target="all",
        send_at=NOW + timedelta(seconds=10),
        frequency="daily",
        event_time="13:00",
        event_day_offset=0,
        headline="会议提醒",
        agenda="开会",
        location="四教",
    )
    service, clock, client, task_id, scheduler = activate(setup, spec=spec)
    clock.advance(2 * 86400 + 5)
    tick(scheduler)
    run = only_run(service, task_id)
    assert run["status"] == "completed"
    assert "2026年10月16日 13:00" in run["body"]
    assert len(service.store.runs(task_id)) == 1
    assert "2026年10月17日" in request(service, "查看通知任务", "list")
    assert "接口确认：2" in request(service, f"查看通知 {run['id']}", "details")
    assert "11111：接口确认" in request(service, f"查看通知 {task_id}", "task_details")
    assert "找不到" in request(service, "查看通知 R123", "missing_run")


@pytest.mark.parametrize(
    "fields",
    [{"recipients": ["昵称"]}, {"weekdays": [7]}, {"event_time": "99:00"}, {"immediate": True, "frequency": "daily"}],
)
def test_contract_rejects_invalid_structured_output(fields):
    with pytest.raises(ValidationError):
        NotificationSpec(**fields)


def test_weekly_cross_year_end_date_and_recurring_event_rules():
    spec = NotificationSpec(
        intent="create",
        target="all",
        frequency="weekly",
        weekdays=[0, 4],
        send_at="2026-12-31T07:30:00",
        event_time="08:00",
        event_day_offset=1,
        headline="会议提醒",
        agenda="开会",
        until="2027-01-04",
    )
    first = next_occurrence(spec, datetime(2026, 12, 31, 23, tzinfo=BEIJING))
    assert first == datetime(2027, 1, 1, 7, 30, tzinfo=BEIJING)
    assert "2027年01月02日 08:00" in render_body(spec, first)
    second = next_occurrence(spec, first)
    assert second == datetime(2027, 1, 4, 7, 30, tzinfo=BEIJING)
    assert next_occurrence(spec, second) is None
    assert NotificationSpec(frequency="daily", event_at=NOW).missing()


def test_two_workers_claim_one_run(setup):
    service, clock, _, task_id, _ = activate(setup)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: service.store.materialize(clock()), range(2)))
    run_id = only_run(service, task_id)["id"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda worker: service.store.claim(run_id, worker, clock()), ["a", "b"]))
    assert sum(claim is not None for claim in claims) == 1
    assert len(service.store.runs(task_id)) == 1


def test_old_database_preserves_existing_tables_and_rows(tmp_path):
    import sqlite3

    path = str(tmp_path / "legacy.db")
    with closing(sqlite3.connect(path)) as old, old:
        old.execute("CREATE TABLE legacy_students(id INTEGER, name TEXT)")
        old.execute("INSERT INTO legacy_students VALUES (1, '保留')")
    with closing(open_database(path)) as db:
        assert {"notification_tasks", "notification_runs", "legacy_students"} <= set(
            inspect(db.engine).get_table_names()
        )
    with closing(sqlite3.connect(path)) as old:
        assert old.execute("SELECT name FROM legacy_students").fetchone() == ("保留",)


def test_friend_list_http_contract_and_invalid_response():
    client = NapCatClient(NapCatSettings("http://localhost", "ws://localhost"))
    with patch.object(client, "call", return_value=[{"user_id": 11111}]) as call:
        assert client.get_friend_list() == [{"user_id": 11111}]
        call.assert_called_once_with("get_friend_list")
    for invalid in [None, {}, [1]]:
        with patch.object(client, "call", return_value=invalid), pytest.raises(NapCatUnknownError):
            client.get_friend_list()


def test_bot_routes_admin_group_commands_to_private_and_replays_once(setup):
    service, clock, _, client = setup
    bot = NapCatBot(NapCatSettings("http://localhost", "ws://localhost"), service.store.path)
    bot.client, bot._notifications = client, service
    message = {
        "message_source": "group",
        "mentioned_bot": False,
        "user_open_id": ADMIN,
        "content": "查看通知任务",
        "group_id": "123",
        "message_id": "message1",
        "created_at": clock().timestamp(),
    }
    asyncio.run(bot._process_message(message))
    client.send_private.assert_not_called()
    message["mentioned_bot"] = True
    with patch("maidanlao.adapters.napcat.bot.build_response_for_text") as rag:
        asyncio.run(bot._process_message(message))
        asyncio.run(bot._process_message(message))
        rag.assert_not_called()
    client.send_private.assert_not_called()
    message["message_source"] = "private"
    message["group_id"] = None
    asyncio.run(bot._process_message(message))
    asyncio.run(bot._process_message(message))
    assert client.send_private.call_count == 1
    assert client.send_private.call_args.args[0] == ADMIN
    client.send_group.assert_not_called()


def test_admin_ack_failure_and_unknown_do_not_change_task_state(setup):
    service, _, _, client = setup
    bot = NapCatBot(NapCatSettings("http://localhost", "ws://localhost"), service.store.path)
    bot.client = client
    client.send_private.side_effect = NapCatError("rejected")
    assert not asyncio.run(bot._send_notification_reply("ack", ADMIN, "通知回执"))
    client.send_private.side_effect = None
    assert asyncio.run(bot._send_notification_reply("ack", ADMIN, "通知回执"))
    client.send_private.side_effect = NapCatUnknownError("timeout")
    assert asyncio.run(bot._send_notification_reply("unknown_ack", ADMIN, "通知回执"))
    assert asyncio.run(bot._send_notification_reply("unknown_ack", ADMIN, "通知回执"))
    assert client.send_private.call_count == 3


def test_scheduler_cancellation_and_error_recovery(setup):
    service, _, _, _ = setup
    scheduler = NotificationScheduler(service, pause=AsyncMock(side_effect=asyncio.CancelledError))
    scheduler.tick = AsyncMock(side_effect=ValueError("database temporarily unavailable"))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scheduler._work_forever())
    scheduler.tick.assert_awaited_once()


def test_invalid_send_response_cannot_be_treated_as_explicit_rejection():
    client = NapCatClient(NapCatSettings("http://localhost", "ws://localhost"))
    for value in [
        {"status": "ok"},
        {"status": "ok", "retcode": False},
        {"status": "ok", "retcode": 0.5},
        {"status": ["ok"], "retcode": 0},
    ]:
        with pytest.raises(NapCatUnknownError):
            client._validate_response(value, "send_private_msg")


def test_unknown_first_page_blocks_only_that_users_remaining_pages(setup):
    service, _, client, task_id, scheduler = activate(
        setup,
        spec=NotificationSpec(intent="create", target="all", send_at=NOW + timedelta(seconds=10), agenda="文" * 4300),
    )

    def send(user, _):
        if user == "11111":
            raise NapCatUnknownError("timeout")

    client.send_private.side_effect = send
    tick(scheduler)
    assert only_run(service, task_id)["status"] == "partial"
    assert [call.args[0] for call in client.send_private.call_args_list].count("11111") == 1
    assert [call.args[0] for call in client.send_private.call_args_list].count("22222") == 3


def test_deadline_mid_batch_preserves_confirmed_user_and_skips_remainder(setup):
    service, clock, client, task_id, scheduler = activate(setup)

    async def pause(_):
        clock.advance(1801)

    scheduler.pause = pause
    tick(scheduler)
    assert only_run(service, task_id)["status"] == "partial"
    assert [call.args[0] for call in client.send_private.call_args_list if call.args[0] != ADMIN] == ["11111"]
    assert "接口确认：1" in service.report(only_run(service, task_id)["id"])


def test_confirmation_after_scheduled_time_requires_new_preview(setup):
    service, clock, parser, _ = setup
    parser.parse.return_value = NotificationSpec(
        intent="create", target="all", send_at=clock() + timedelta(seconds=10), agenda="开会"
    )
    task_id = create(service)
    clock.advance(11)
    assert "发送时间已过去" in request(service, f"确认通知 {task_id}", "too_late")
    assert service.store.task(task_id)["status"] == "expired"


def test_periodic_scanning_continues_while_delivery_is_busy(setup):
    service, _, _, _ = setup
    service.store.materialize = Mock()

    async def scenario():
        polls = 0

        async def pause(_):
            nonlocal polls
            polls += 1
            if polls >= 3:
                raise asyncio.CancelledError()
            await asyncio.sleep(0)

        scheduler = NotificationScheduler(service, pause=pause)
        blocked = asyncio.Event()
        scheduler.tick = AsyncMock(side_effect=lambda: None)

        async def busy():
            await blocked.wait()

        scheduler.tick.side_effect = busy
        with pytest.raises(asyncio.CancelledError):
            await scheduler.run_forever()
        scheduler.tick.assert_awaited_once()

    asyncio.run(scenario())
    assert service.store.materialize.call_count == 3


@pytest.mark.parametrize(
    "reason, expected",
    [
        ("empty", "好友名单为空"),
        ("identity", "QQ登录状态"),
        ("offline", "NapCat连接"),
    ],
)
def test_friend_errors_are_distinct_from_model_parse_failures(setup, reason, expected):
    service, _, _, client = setup
    if reason == "empty":
        client.get_friend_list.return_value = []
    elif reason == "identity":
        client.get_login_info.return_value = {}
    else:
        client.get_friend_list.side_effect = NapCatError("unavailable")
    assert expected in request(service)
    assert not service.store.tasks(ADMIN)


def test_bot_admin_smalltalk_reaches_unified_agent(setup):
    from maidanlao.service import AssistantResponse

    service, _, parser, client = setup
    bot = NapCatBot(NapCatSettings("http://localhost", "ws://localhost"), service.store.path)
    bot.client, bot._notifications = client, service
    message = {
        "message_source": "private",
        "mentioned_bot": True,
        "user_open_id": ADMIN,
        "content": "你是谁",
        "group_id": None,
        "message_id": "identity-question",
    }
    with patch(
        "maidanlao.adapters.napcat.bot.build_response_for_text", return_value=AssistantResponse("我是麦当牢呀。")
    ) as rag:
        assert asyncio.run(bot._process_message(message))
    rag.assert_called_once()
    client.send_private.assert_called_once_with(
        ADMIN,
        [{"type": "reply", "data": {"id": "identity-question"}}, {"type": "text", "data": {"text": "我是麦当牢呀。"}}],
    )
