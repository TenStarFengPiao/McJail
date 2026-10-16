"""群采集、人工审批与原生文件转发；所有网络接口均为模拟，不发真实消息。"""

import asyncio
import re
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from unittest.mock import AsyncMock, Mock, patch

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import select
from test_agentic_rag import ScriptedModel
from test_notifications import ADMIN, Clock
from test_unified_agent import call, context, respond

from maidanlao.adapters.napcat.bot import NapCatBot
from maidanlao.adapters.napcat.client import NapCatClient, NapCatError, NapCatSettings, NapCatUnknownError
from maidanlao.group_forwarding.service import ForwardService
from maidanlao.group_forwarding.worker import ForwardWorker
from maidanlao.notifications.service import NotificationService
from maidanlao.persistence.database import open_database
from maidanlao.persistence.delivery import DeliveryLedger
from maidanlao.persistence.models import ForwardBatch, ForwardRule
from maidanlao.rag import LocalHashEmbeddingClient
from maidanlao.service import AssistantService

SOURCE, TARGET = "111111", "222222"


@pytest.fixture
def env(tmp_path):
    client = Mock()
    client.get_group_list.return_value = [{"group_id": SOURCE}, {"group_id": TARGET}, {"group_id": "333333"}]
    client.get_group_history.return_value = []
    client.send_private.return_value = "sent"
    client.send_group.return_value = "sent"
    clock = Clock()
    service = ForwardService(str(tmp_path / "forward.db"), client, clock=clock, admin_id=ADMIN)
    return service, clock, client


def activate(service, source=SOURCE, target=TARGET, key="rule"):
    receipt = service.prepare_rule(ADMIN, key, source, target)
    command = re.search(r"确认规则 F[A-F0-9]+ R[A-F0-9]+", receipt)[0]
    service.handle(ADMIN, command, "confirm:" + key)
    return command.split()[1]


def event(mid=1, group=SOURCE, files=(), text="明天八点去四教开会。", **overrides):
    return {
        "post_type": "message",
        "message_type": "group",
        "self_id": 999,
        "user_id": 12345,
        "group_id": group,
        "message_id": mid,
        "message": [
            {"type": "text", "data": {"text": text}},
            *[{"type": "file", "data": {"id": fid, "name": fid + ".pdf"}} for fid in files],
        ],
        **overrides,
    }


def closed(env, files=()):
    service, clock, _ = env
    activate(service)
    service.collect(event(files=files))
    clock.advance(60)
    ForwardWorker(service).tick()
    return service.query(ADMIN)["items"][0]["id"]


def approve(service, bid, body="会议提醒", key="preview"):
    service.preview(ADMIN, key, bid, body)
    return service.handle(ADMIN, f"确认转发 {bid} V1", "approve:" + key)


def test_rule_confirmation_authorization_and_draft_cannot_be_resumed(env):
    service, clock, client = env
    with pytest.raises(PermissionError):
        service.prepare_rule("123", "evil", SOURCE, TARGET)
    with pytest.raises(ValueError):
        service.prepare_rule(ADMIN, "bad", SOURCE, SOURCE)
    with pytest.raises(ValueError):
        service.prepare_rule(ADMIN, "bad", "群聊", TARGET)
    receipt = service.prepare_rule(ADMIN, "rule", SOURCE, TARGET)
    command = re.search(r"确认规则 F[A-F0-9]+ R[A-F0-9]+", receipt)[0]
    rid = command.split()[1]
    assert "只能暂停" in service.control(ADMIN, "pause", rid, "pause")
    assert "只能恢复" in service.control(ADMIN, "resume", rid, "resume")
    service.collect(event())
    assert not service.query(ADMIN)["items"]
    clock.advance(1800)
    assert "过期" in service.handle(ADMIN, command, "late")
    assert service.prepare_rule(ADMIN, "rule", SOURCE, TARGET) == receipt
    activate(service, key="new-rule")
    service.collect(event())
    assert len(service.query(ADMIN)["items"]) == 1
    client.send_group.assert_not_called()


def test_fixed_window_boundary_dedup_restart_and_groups_are_independent(env):
    service, clock, client = env
    activate(service)
    activate(service, TARGET, "333333", "rule2")
    service.collect(event())
    clock.advance(59)
    service.collect(event(2))
    service.collect(event(2))
    service.collect(event(3, TARGET))
    clock.advance(1)
    service = ForwardService(service.path, client, clock=clock, admin_id=ADMIN)
    service.collect(event(4))
    rows = service.query(ADMIN)["items"]
    assert len(rows) == 3
    first = next(
        service.query(ADMIN, identifier_value=r["id"]) for r in rows if r["opened_at"] == rows[-1]["opened_at"]
    )
    assert [m["message_id"] for m in first["messages"]] == ["1", "2"]
    ForwardWorker(service).tick()
    assert service.query(ADMIN, identifier_value=first["id"])["status"] == "review"
    assert client.send_private.called
    client.send_group.assert_not_called()


def test_concurrent_capture_is_single_window_and_durable_dedup(env):
    service, _, _ = env
    activate(service)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(service.collect, [event(i // 2 + 1) for i in range(12)]))
    rows = service.query(ADMIN)["items"]
    assert len(rows) == 1
    assert len(service.query(ADMIN, identifier_value=rows[0]["id"])["messages"]) == 6


@pytest.mark.parametrize("mention,actor", [(False, "123"), (True, "123"), (True, ADMIN)])
def test_all_group_messages_are_silent_including_admin_confirmation(env, mention, actor):
    service, _, client = env
    activate(service)
    bot = NapCatBot(NapCatSettings("http://localhost", "ws://localhost"), service.path)
    bot.client, bot._forwarding = client, service
    bot._process_message = AsyncMock()
    raw = event(user_id=actor, text="确认转发 B123 V1")
    if mention:
        raw["message"].insert(0, {"type": "at", "data": {"qq": "999"}})
    asyncio.run(bot.handle_event(raw))
    bot._process_message.assert_not_awaited()
    client.send_private.assert_not_called()
    client.send_group.assert_not_called()
    assert len(service.query(ADMIN)["items"]) == 1


def test_original_files_reviewed_once_and_published_before_frozen_text(env):
    service, _, client = env
    bid = closed(env, files=("a", "b"))
    client.forward_single.assert_called_once_with("1", user_id=ADMIN)
    ForwardWorker(service).tick()
    assert client.forward_single.call_count == 1
    preview = service.preview(ADMIN, "p", bid, "会议提醒\n地点：四教")
    assert "a.pdf" in preview and "b.pdf" in preview
    client.send_group.assert_not_called()
    assert service.handle(ADMIN, f"确认转发 {bid} V1", "ok")
    client.reset_mock()
    ForwardWorker(service).tick()
    calls = client.method_calls
    native = next(i for i, c in enumerate(calls) if c[0] == "forward_single")
    text = next(i for i, c in enumerate(calls) if c[0] == "send_group")
    assert native < text
    client.forward_single.assert_called_once_with("1", group_id=TARGET)
    assert client.send_group.call_args.args[0] == TARGET
    assert "会议提醒\n地点：四教" in client.send_group.call_args.args[1][0]["data"]["text"]
    assert service.query(ADMIN, identifier_value=bid)["status"] == "published"
    ForwardWorker(service).publish(bid)
    assert client.send_group.call_count == 1
    assert "不会重复" in service.handle(ADMIN, f"确认转发 {bid} V1", "duplicate")
    client.send_file.assert_not_called()


def test_preview_versions_selection_and_rule_target_freezing(env):
    service, _, client = env
    bid = closed(env, files=("a", "b"))
    files = service.query(ADMIN, identifier_value=bid)["attachments"]
    assert "必须一起" in service.preview(ADMIN, "subset", bid, "开会", [files[0]["id"]])
    assert "不属于" in service.preview(ADMIN, "foreign", bid, "开会", ["AFAKE"])
    service.preview(ADMIN, "p1", bid, "初版")
    service.preview(ADMIN, "p2", bid, "修改版", [])
    assert "失效" in service.handle(ADMIN, f"确认转发 {bid} V1", "old")
    activate(service, target="333333", key="change-target")
    service.handle(ADMIN, f"确认转发 {bid} V2", "new")
    client.reset_mock()
    ForwardWorker(service).publish(bid)
    client.forward_single.assert_not_called()
    assert client.send_group.call_args.args[0] == TARGET
    assert "修改版" in client.send_group.call_args.args[1][0]["data"]["text"]


def test_upload_notice_requires_strict_original_message_association(env):
    service, clock, client = env
    activate(service)
    notice = event(
        post_type="notice",
        notice_type="group_upload",
        message_id=None,
        message=[],
        file={"id": "file123", "name": "材料.pdf"},
    )
    service.collect(notice)
    service.collect(notice)
    clock.advance(60)
    worker = ForwardWorker(service)
    worker.tick()
    bid = service.query(ADMIN)["items"][0]["id"]
    assert "没有可原生转发" in service.preview(ADMIN, "p", bid, "材料通知")
    client.forward_single.assert_not_called()
    # 同名、不同ID不能关联；严格匹配文件ID和发送人。
    client.get_group_history.return_value = [event(8, files=("different",))]
    worker.resolve_files(bid)
    assert service.query(ADMIN, identifier_value=bid)["attachments"][0]["message_id"] is None
    clock.advance(30)
    client.get_group_history.return_value = [event(9, files=("file123",))]
    worker.resolve_files(bid)
    assert service.query(ADMIN, identifier_value=bid)["attachments"][0]["message_id"] == "9"
    worker.review(bid)
    client.forward_single.assert_called_once_with("9", user_id=ADMIN)
    assert "确认转发" in service.preview(ADMIN, "p2", bid, "材料通知")
    client.send_file.assert_not_called()


@pytest.mark.parametrize("notice_first", [True, False])
def test_upload_notice_and_original_message_reconcile_without_duplicate(env, notice_first):
    service, _, _ = env
    activate(service)
    notice = event(
        post_type="notice", notice_type="group_upload", message_id=None, message=[], file={"id": "x", "name": "x.pdf"}
    )
    events = [notice, event(files=("x",))]
    for raw in events if notice_first else reversed(events):
        service.collect(raw)
    bid = service.query(ADMIN)["items"][0]["id"]
    data = service.query(ADMIN, identifier_value=bid)
    assert len(data["attachments"]) == 1
    assert data["attachments"][0]["message_id"] == "1"


@pytest.mark.parametrize(
    "error,expected",
    [(NapCatError("拒绝"), "partial"), (NapCatUnknownError("超时"), "unknown"), (OSError("断线"), "unknown")],
)
def test_failed_and_unknown_results_do_not_automatically_republish(env, error, expected):
    service, _, client = env
    bid = closed(env, files=("a",))
    approve(service, bid)
    client.forward_single.side_effect = error
    worker = ForwardWorker(service)
    worker.publish(bid)
    assert service.query(ADMIN, identifier_value=bid)["status"] == expected
    count = client.forward_single.call_count
    worker.tick()
    assert client.forward_single.call_count == count
    client.send_group.assert_not_called()
    client.forward_single.side_effect = None
    if expected == "partial":
        service.control(ADMIN, "retry", bid, "retry")
        service.handle(ADMIN, f"确认转发 {bid} V1", "reconfirm")
    else:
        assert "不能" in service.control(ADMIN, "bad-retry", bid, "retry")
        preview = service.control(ADMIN, "risk", bid, "retry_unknown")
        command = re.search(r"确认补发转发 B[A-F0-9]+ V1 R[A-F0-9]+", preview)[0]
        with pytest.raises(PermissionError):
            service.handle("12345", command, "fake")
        service.handle(ADMIN, command, "risk-confirm")
    worker.publish(bid)
    assert service.query(ADMIN, identifier_value=bid)["status"] == "published"


def test_partial_retry_skips_confirmed_file_and_cancellation_stops_next_item(env):
    service, _, client = env
    bid = closed(env, files=("a",))
    approve(service, bid)
    client.send_group.side_effect = NapCatError("拒绝")
    ForwardWorker(service).publish(bid)
    count = client.forward_single.call_count
    service.control(ADMIN, "retry", bid, "retry")
    service.handle(ADMIN, f"确认转发 {bid} V1", "reconfirm")
    client.send_group.side_effect = None
    ForwardWorker(service).publish(bid)
    assert client.forward_single.call_count == count
    # 新批次的原文件提交后管理员放弃，后续正文不能继续发送。
    service.collect(event(2, files=("b",)))
    env[1].advance(60)
    ForwardWorker(service).tick()
    bid2 = service.query(ADMIN)["items"][0]["id"]
    approve(service, bid2, key="p2")
    client.send_group.reset_mock()
    client.forward_single.side_effect = lambda *a, **kw: service.control(ADMIN, "stop", bid2, "discard")
    ForwardWorker(service).publish(bid2)
    client.send_group.assert_not_called()
    assert service.query(ADMIN, identifier_value=bid2)["status"] == "discarded"


def test_publish_atomic_claim_and_restart_unknown(env):
    service, _, client = env
    bid = closed(env)
    approve(service, bid)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: ForwardWorker(service).publish(bid), range(2)))
    assert client.send_group.call_count == 1
    with service.write() as db:
        db.get(ForwardBatch, bid).status = "publishing"
    worker = ForwardWorker(service)
    with patch("maidanlao.group_forwarding.worker.asyncio.sleep", new=AsyncMock(side_effect=asyncio.CancelledError)):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(worker.run_forever())
    assert service.query(ADMIN, identifier_value=bid)["status"] == "unknown"
    assert client.send_group.call_count == 1


def test_agent_tools_prepare_canonical_preview_and_model_failure_fallback(env):
    service, _, client = env
    bid = closed(env)
    notifications = NotificationService(service.path, client)
    model = ScriptedModel(
        script=[
            call("query_group_forwarding", batch_id=bid),
            call("prepare_group_forwarding", action="preview", batch_id=bid, body="会议提醒\n地点：四教"),
            RuntimeError("模型中断"),
        ]
    )
    app = AssistantService(
        service.path,
        model,
        LocalHashEmbeddingClient(),
        None,
        notification_service=notifications,
        forwarding_service=service,
    )
    response = respond(app, f"生成预览 {bid}")
    assert f"确认转发 {bid} V1" in response.text
    assert response.reply_target == "private"
    assert service.query(ADMIN, identifier_value=bid)["status"] == "preview"
    client.send_group.assert_not_called()
    tools = {t.name for t in model.bindings[0][0]}
    assert {"query_group_forwarding", "prepare_group_forwarding", "control_group_forwarding"} <= tools
    assert not any("confirm" in t for t in tools)
    # 普通QQ用户与CLI伪造QQ号均不获得管理工具。
    for ctx in (context(actor="123"), context(transport="cli")):
        plain = ScriptedModel(script=[AIMessage(content="你好")])
        app = AssistantService(
            service.path,
            plain,
            LocalHashEmbeddingClient(),
            None,
            notification_service=notifications,
            forwarding_service=service,
        )
        respond(app, "查看群转发", ctx)
        assert "query_group_forwarding" not in {t.name for t in plain.bindings[0][0]}


def test_precise_confirmation_requires_unquoted_private_admin_message(env):
    service, _, client = env
    bid = closed(env)
    service.preview(ADMIN, "p", bid, "会议提醒")
    assert service.handle(ADMIN, f"他说“确认转发 {bid} V1”", "quoted") is None
    bot = NapCatBot(NapCatSettings("http://localhost", "ws://localhost"), service.path)
    bot._forwarding, bot.client = service, client
    message = dict(
        message_source="private", user_open_id=ADMIN, group_id=None, message_id="confirm", content=f"确认转发 {bid} V1"
    )
    assert asyncio.run(bot._process_message(message))
    assert asyncio.run(bot._process_message(message))
    assert service.query(ADMIN, identifier_value=bid)["status"] == "approved"
    client.send_group.assert_not_called()


def test_native_client_action_contract_and_null_success():
    client = NapCatClient(NapCatSettings("http://localhost", "ws://localhost"))
    client.call = Mock(return_value=None)
    assert client.forward_single("9", user_id=ADMIN) is None
    client.call.assert_called_with("forward_friend_single_msg", {"message_id": "9", "user_id": ADMIN})
    client.forward_single("9", group_id=TARGET)
    client.call.assert_called_with("forward_group_single_msg", {"message_id": "9", "group_id": TARGET})
    with pytest.raises(ValueError):
        client.forward_single("9")
    with pytest.raises(ValueError):
        client.forward_single("9", group_id=TARGET, user_id=ADMIN)
    for payload in [None, {}, [None]]:
        client.call.return_value = payload
        with pytest.raises(NapCatUnknownError):
            client.get_group_list()
    client.call.return_value = {"messages": [{"message_id": 1}, None]}
    assert client.get_group_history(SOURCE) == [{"message_id": 1}]
    client.call.return_value = {}
    with pytest.raises(NapCatUnknownError):
        client.get_group_history(SOURCE)


def test_new_tables_preserve_existing_data_and_migration_is_idempotent(env):
    service, _, _ = env
    rid = activate(service)
    for _ in range(2):
        with closing(open_database(service.path)) as db:
            assert db.session.get(ForwardRule, rid).status == "active"
            assert 7 in db.session.connection().exec_driver_sql("SELECT version FROM schema_migrations").scalars().all()
            assert list(db.session.scalars(select(ForwardBatch))) == []


def test_uncertain_delivery_is_not_automatically_reclaimed(env):
    service, _, client = env
    bid = closed(env)
    approve(service, bid)
    ledger = DeliveryLedger(service.path, f"forward_publish:{bid}:V1")
    assert ledger.claim("text:0", "text") == "send"
    ForwardWorker(service).publish(bid)
    assert service.query(ADMIN, identifier_value=bid)["status"] == "unknown"
    client.send_group.assert_not_called()


def test_consumed_rule_token_cannot_reactivate_cancelled_rule(env):
    service, _, _ = env
    receipt = service.prepare_rule(ADMIN, "rule", SOURCE, TARGET)
    command = re.search(r"确认规则 F[A-F0-9]+ R[A-F0-9]+", receipt)[0]
    service.handle(ADMIN, command, "confirm")
    service.control(ADMIN, "cancel", command.split()[1], "cancel")
    assert "过期" in service.handle(ADMIN, command, "replay-other-id")
    service.collect(event())
    assert not service.query(ADMIN)["items"]


def test_reconciliation_retains_text_and_other_files(env):
    service, _, _ = env
    activate(service)
    service.collect(
        event(
            post_type="notice",
            notice_type="group_upload",
            message_id=None,
            message=[],
            file={"id": "a", "name": "a.pdf"},
        )
    )
    service.collect(event(files=("a", "b"), text="新通知正文不能丢失"))
    bid = service.query(ADMIN)["items"][0]["id"]
    data = service.query(ADMIN, identifier_value=bid)
    assert {a["file_id"] for a in data["attachments"]} == {"a", "b"}
    assert all(a["message_id"] == "1" for a in data["attachments"])
    assert any(m["text"] == "新通知正文不能丢失" for m in data["messages"])


def test_cq_source_text_remains_literal_text_in_target(env):
    service, _, client = env
    bid = closed(env)
    approve(service, bid, body="原文包含[CQ:at,qq=all]")
    ForwardWorker(service).publish(bid)
    sent = client.send_group.call_args.args[1]
    assert len(sent) == 1 and sent[0]["type"] == "text"
    assert "[CQ:at,qq=all]" in sent[0]["data"]["text"]


def test_invalid_text_ack_and_receipt_write_failure_are_unknown(env):
    service, _, client = env
    bid = closed(env)
    approve(service, bid)
    client.send_group.return_value = None
    ForwardWorker(service).publish(bid)
    assert service.query(ADMIN, identifier_value=bid)["status"] == "unknown"
    # 外部成功、确认回执写入失败时仍保留sending，不能按明确失败自动重试。
    worker = ForwardWorker(service)
    ledger = DeliveryLedger(service.path, "interrupted")
    with patch.object(ledger, "finish", side_effect=OSError("磁盘写失败")):
        with pytest.raises(OSError):
            worker.deliver(ledger, "file", client.forward_single, "1", user_id=ADMIN)
    assert ledger.claim("file", "file") == "unknown"


def test_unknown_retry_preview_expires_and_self_events_are_ignored(env):
    service, clock, client = env
    activate(service)
    service.collect(event(user_id="999"))
    assert not service.query(ADMIN)["items"]
    service.collect(event())
    clock.advance(60)
    ForwardWorker(service).tick()
    bid = service.query(ADMIN)["items"][0]["id"]
    approve(service, bid)
    client.send_group.side_effect = NapCatUnknownError("超时")
    ForwardWorker(service).publish(bid)
    receipt = service.control(ADMIN, "retry", bid, "retry_unknown")
    command = re.search(r"确认补发转发 B[A-F0-9]+ V1 R[A-F0-9]+", receipt)[0]
    clock.advance(1800)
    assert "过期" in service.handle(ADMIN, command, "late")
    assert service.query(ADMIN, identifier_value=bid)["status"] == "unknown"
