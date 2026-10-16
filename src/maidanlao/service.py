"""问答分为准备与提交：QQ 完成交付后才提交成功历史，不保存半轮记录。"""

import json
import logging
from contextlib import closing
from dataclasses import dataclass, field
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage

from maidanlao.agent.factory import build_agent
from maidanlao.agent.files import FileSession
from maidanlao.agent.notifications import NotificationSession
from maidanlao.agent.tools import SearchSession
from maidanlao.agent.utilities import UtilitySession
from maidanlao.config.settings import RerankSettings, database_path
from maidanlao.llm.client import LangChainDeepSeekClient, LangChainEmbeddingClient
from maidanlao.llm.rerank import DashScopeRerankClient
from maidanlao.persistence.database import open_database
from maidanlao.persistence.repository import KnowledgeRepository
from maidanlao.rag import HybridRetriever
from maidanlao.rag.models import OriginalAttachment
from maidanlao.rag.validation import validate_sources
from maidanlao.text import plain_text, strip_internal_metadata, with_references

logger = logging.getLogger(__name__)
_AUTO = object()


@dataclass(frozen=True)
class PendingTurn:
    """已准备但未交付的本轮信息；不包含数据库连接，不自行保存。"""

    conversation_key: str
    question: str
    request_id: str | None = None


@dataclass
class AssistantResponse:
    """回答、来源与待交付附件；pending_turn 仅在成功生成后设置。"""

    text: str
    sources: list[dict] = field(default_factory=list)
    attachments: list[OriginalAttachment] = field(default_factory=list)
    pending_turn: PendingTurn | None = None
    reply_target: str = "conversation"

    def __post_init__(self):
        self.text = plain_text(self.text)


def conversation_key(user_id: str, group_id: str | None, source: str) -> str:
    """按用户、群及入口隔离；JSON 编码避免分隔符碰撞。"""
    if source not in {"private", "group"} or not user_id or (source == "group" and not group_id):
        raise ValueError("invalid_conversation_identity")
    return json.dumps([source, group_id if source == "group" else None, user_id], ensure_ascii=False)


class AssistantService:
    """统一 Agent 选择知识、文件、实时查询与通知预览；发送与确认留在入口。"""

    def __init__(
        self,
        db_path=None,
        model=None,
        embedding_client=None,
        rerank_client=_AUTO,
        *,
        weather_client=None,
        notification_service=None,
        forwarding_service=None,
    ):
        self.db_path = db_path or database_path()
        self.model = model
        self.weather_client = weather_client
        self.notification_service = notification_service
        self.forwarding_service = forwarding_service
        self.embedding_client = embedding_client if embedding_client is not None else LangChainEmbeddingClient()
        settings = RerankSettings.from_env()
        self.rerank_client = (
            (DashScopeRerankClient(settings) if settings.api_key and settings.model else None)
            if rerank_client is _AUTO
            else rerank_client
        )

    def prepare_response(
        self,
        user_open_id: str,
        group_id: str | None,
        message_source: str,
        content: str,
        request_id: str | None = None,
        *,
        context=None,
    ) -> AssistantResponse:
        """加载历史并准备回答，不提交本轮；检索或模型失败不留下历史。"""
        key = conversation_key(user_open_id, group_id, message_source)
        if context and (
            context.user_id != user_open_id or context.conversation != key or context.request_id != request_id
        ):
            raise ValueError("request_context_mismatch")
        if not content.strip():
            return AssistantResponse("请输入你想查询的问题。")
        with closing(open_database(self.db_path)) as db:
            repos = KnowledgeRepository(db)
            turns = [
                SimpleNamespace(question=turn.question, answer=turn.answer, sources_json=turn.sources_json)
                for turn in repos.chat.recent(key)
            ]
            # 历史快照已加载；先结束读事务，让通知工具的独立短事务可以写入。
            db.session.rollback()
            notifications = (
                NotificationSession(self.notification_service, context)
                if context and self.notification_service
                else None
            )
            if notifications is not None and not notifications.authorized:
                notifications = None
            if notifications:
                notifications.content = content
                notifications.forwarding = self.forwarding_service
            cached = notifications.service.store.cached(request_id) if notifications else None
            if cached is None and notifications and self.forwarding_service:
                cached = self.forwarding_service.cached(request_id)
            if cached is not None:
                response, success = AssistantResponse(cached, reply_target="private"), True
            else:
                response, success = self._answer_question(repos, db, turns, content, notifications)
            if notifications and notifications.used:
                response.reply_target = "private"
            if success:
                response.pending_turn = PendingTurn(key, content, request_id)
            return response

    def commit_turn(self, response: AssistantResponse) -> None:
        """入口确认全部交付成功后原子保存；同一 QQ 消息重复提交幂等。"""
        if response.pending_turn is None:
            return
        pending = response.pending_turn
        with closing(open_database(self.db_path)) as db:
            KnowledgeRepository(db).chat.append(
                pending.conversation_key, pending.question, response.text, response.sources, pending.request_id
            )

    def handle_message(
        self, user_open_id: str, group_id: str | None, message_source: str, content: str
    ) -> AssistantResponse:
        """同步调用者收到纯文本即提交；附件必须由发送入口确认后显式提交。"""
        response = self.prepare_response(user_open_id, group_id, message_source, content)
        if response.pending_turn and not response.attachments:
            try:
                self.commit_turn(response)
            except Exception as exc:
                logger.warning("chat_save_failed type=%s", type(exc).__name__)
                return AssistantResponse("本轮处理失败，请稍后再试。")
        return response

    def _answer_question(self, repos, db, turns, content, notifications=None):
        """运行 Agent，拒绝失效来源与模型绕过文件定位的请求。"""
        messages = []
        for turn in turns:
            # 原件ID/版本仍由 FileSession 读取 sources_json；不把内部JSON拼进助手历史供模型模仿。
            messages.extend(
                [
                    HumanMessage(content=turn.question),
                    AIMessage(content=strip_internal_metadata(turn.answer)),
                ]
            )
        messages.append(HumanMessage(content=content))
        search = SearchSession(HybridRetriever(repos, self.embedding_client, rerank_client=self.rerank_client))
        files = FileSession(repos, turns, content, search)
        utilities = UtilitySession(self.weather_client)
        try:
            model = self.model if self.model is not None else LangChainDeepSeekClient().model
            # 未授权入口仍按原接口构建；通知工具只来自绑定的QQ请求上下文。
            agent = (
                build_agent(model, search, files, utilities, notifications)
                if notifications
                else build_agent(model, search, files, utilities)
            )
            db.session.rollback()
            result = agent.invoke({"messages": messages}, config={"max_concurrency": 1, "recursion_limit": 64})
            if notifications and notifications.used:
                # 已保存预览必须原样交付，即使同轮其他工具失败，也不能把草稿误说成未创建。
                if notifications.receipt:
                    return AssistantResponse(notifications.receipt, reply_target="private"), True
                if notifications.error:
                    return AssistantResponse(notifications.error, reply_target="private"), False
            if search.failed:
                return AssistantResponse("知识库检索暂时不可用，本轮无法核实，请稍后再试。"), False
            if files.notice:
                return AssistantResponse(files.notice), False
            if utilities.notice:
                # 缺少地点、服务失败或缺测时直接返回工具契约提示，避免模型据空结果臆测降雨时间。
                return AssistantResponse(utilities.notice), False
            sources = list(search.sources.values())
            db.session.rollback()
            sources, changed = validate_sources(repos, sources)
            if changed:
                return AssistantResponse("引用资料已发生变化，本轮无法确认，请重新提问。"), False
            # 原件来源不要求 chunk_id，但必须重新校验当前文档及原件版本。
            for document_id in list(files.attachments):
                files.get(document_id)
                if files.notice:
                    return AssistantResponse(files.notice), False
            sources.extend(files.sources.values())
            answer = plain_text(result["messages"][-1].text)
            if files.attachments:
                # 文件措辞由服务端生成，防止模型声称尚未发生的发送成功。
                answer = "准备发送以下原文件：\n" + "\n".join(a.filename for a in files.attachments.values())
                sources = list(files.sources.values())
            if not answer.strip():
                return AssistantResponse("本轮没有生成有效答复，请稍后再试。"), False
            answer = with_references(answer, sources)
            target = "private" if notifications and notifications.used else "conversation"
            return AssistantResponse(answer, sources, list(files.attachments.values()), reply_target=target), True
        except Exception as exc:
            db.session.rollback()
            name = type(exc).__name__
            logger.warning("agent_request_failed type=%s", name)
            if notifications and notifications.used:
                # 草稿事务与模型调用独立；模型中断也要返回已落盘的原始预览。
                receipt = notifications.receipt or notifications.service.store.cached(notifications.context.request_id)
                if receipt is None and notifications.forwarding:
                    receipt = notifications.forwarding.cached(notifications.context.request_id)
                if receipt:
                    return AssistantResponse(receipt, reply_target="private"), True
                return AssistantResponse(
                    notifications.error or "本轮通知查询未完成，请稍后再试。", reply_target="private"
                ), False
            if name in {"ToolCallLimitExceededError", "ModelCallLimitExceededError"}:
                return AssistantResponse("本轮已达到工具调用上限，尚未完成核实，请缩小问题范围后重试。"), False
            return AssistantResponse("本轮处理失败，请稍后再试。"), False

    def handle_text(self, user_open_id: str, group_id: str | None, message_source: str, content: str) -> str:
        """CLI 只交付文本；附件请求告知改用 QQ，不留下虚假的交付历史。"""
        response = self.handle_message(user_open_id, group_id, message_source, content)
        if response.attachments:
            return "已定位原文件；CLI 不发送附件，请在 QQ 中索要：\n" + "\n".join(
                a.filename for a in response.attachments
            )
        return response.text
