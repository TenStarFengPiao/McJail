"""通知管理工具只查询或准备预览；不向模型暴露确认、取消执行和发送接口。"""

import json
import logging
import re
from typing import Literal

from langchain_core.tools import tool

from maidanlao.notifications.contracts import BEIJING, NotificationSpec

logger = logging.getLogger(__name__)


class NotificationSession:
    """请求内管理状态；每轮只有一个变更槽，权限来自可信请求上下文。"""

    def __init__(self, service, context):
        self.service, self.context = service, context
        self.used = False
        self.receipt = ""
        self.error = ""
        self.content = ""
        self.forwarding = None

    @property
    def authorized(self):
        return self.context.transport == "qq" and self.context.user_id == self.service.settings.admin_id

    def authorize(self):
        if not self.authorized:
            raise PermissionError("只有QQ管理员可以管理通知任务。")
        self.service.authorize(self.context.user_id)

    def call(self, function, **kwargs):
        """内部异常不泄露给 QQ；授权后记录本轮必须私聊。"""
        try:
            self.authorize()
            self.used = True
            result = function(self.context.user_id, **kwargs)
            self.error = result.get("error", "") if isinstance(result, dict) else ""
            return result
        except (ValueError, PermissionError) as exc:
            self.error = str(exc)
            return {"error": self.error}
        except Exception as exc:
            logger.warning("notification_tool_failed type=%s", type(exc).__name__)
            self.error = "通知查询或草稿准备失败，请稍后重试；本轮没有确认任务。"
            return {"error": self.error}

    def prompt_context(self):
        """草稿是数据而非指令；当前时刻来自服务端，接收时间用于相对日期解析。"""
        self.authorize()
        return json.dumps(
            {
                "received_at": self.context.received_at.astimezone(BEIJING).isoformat(),
                "current_time": self.service.clock().astimezone(BEIJING).isoformat(),
                "drafts": self.service.drafts(self.context.user_id, self.context.conversation),
            },
            ensure_ascii=False,
        )

    def prepare(self, action, spec=None, identifier=None):
        """所有变更共用原消息的幂等键；重试不得新建第二个草稿。"""
        self.authorize()
        self.used = True
        # 这里只否决明确的否定/讨论表达，不用关键词替代 Agent 的意图选择。
        if re.search(
            r"(?:不要|别|不用|无需|不需要).{0,16}(?:通知|提醒|发送|发给|取消|修改|补发)|(?:举个|解释|讨论|示例|例如|例子)",
            self.content,
        ):
            self.receipt = "本轮是讨论或否定请求，没有修改通知任务。"
            return {"receipt": self.receipt, "activated": False}
        cached = self.service.store.cached(self.context.request_id)
        if cached is None and self.forwarding:
            cached = self.forwarding.cached(self.context.request_id)
        if cached is not None:
            self.receipt = cached
            return {"receipt": cached}
        if self.receipt:
            return {"receipt": self.receipt}
        if action in {"create", "update"}:
            if spec is None:
                raise ValueError("请提供通知内容、接收对象和时间候选。")
            candidate = spec.model_copy(update={"intent": action})
            if identifier:
                candidate = candidate.model_copy(update={"task_id": identifier})
            result = self.service.prepare(
                self.context.user_id, candidate, self.context.request_id, self.context.conversation
            )
        elif action == "cancel":
            if not identifier:
                raise ValueError("请先查询并选择要取消的任务号。")
            result = self.service.prepare_cancel(self.context.user_id, identifier, self.context.request_id)
        elif action == "retry":
            if not identifier:
                raise ValueError("请先查询并选择要补发的执行号。")
            result = self.service.prepare_retry(self.context.user_id, identifier, self.context.request_id)
        else:
            raise ValueError("不支持的通知准备操作。")
        self.receipt = result
        self.error = ""
        return {"receipt": result, "activated": False}


def build_notification_tools(session):
    """普通用户和 CLI 不注册这些工具；工具仍再次验证绑定身份。"""
    if not session.authorized:
        return []

    @tool
    def list_notification_tasks(
        status: str = "pending",
        start: str | None = None,
        end: str | None = None,
        keyword: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> str:
        """查询管理员通知任务，不查询学校政策。有哪些待发/明早有什么提醒时使用。

        status默认pending：已生效计划及未完成批次，不含草稿；all查看全部。
        也可指定draft、clarifying、active、queued、running、completed、partial、missed、cancelled、expired。
        start/end为北京时间ISO日期时间，左闭右开；无偏移按北京时间。
        keyword匹配事项/标题/地点。默认20条，每页最多50，next_page表示后续页。
        """
        return json.dumps(
            session.call(
                session.service.query,
                status=status,
                start=start,
                end=end,
                keyword=keyword,
                page=page,
                page_size=page_size,
            ),
            ensure_ascii=False,
        )

    @tool
    def get_notification_task(identifier: str, include_recipients: bool = False) -> str:
        """查询通知任务号N...或执行号R...的正文、计划和真实发送状态。

        “那条通知发了吗”先通过列表或历史任务号定位，多候选先询问。
        include_recipients只在管理员明确索要逐人回执时设true；接口确认不等于已读。
        """
        return json.dumps(
            session.call(session.service.detail, identifier=identifier, include_recipients=include_recipients),
            ensure_ascii=False,
        )

    @tool
    def prepare_notification(
        action: Literal["create", "update", "cancel", "retry"],
        spec: NotificationSpec | None = None,
        identifier: str | None = None,
    ) -> str:
        """只准备通知变更预览，不执行发送、激活或取消。

        create/update提供完整spec；update必须指定任务号identifier或spec.task_id。
        cancel指定任务号，retry指定执行号。每轮最多一个变更，必须由管理员明确确认。
        否定请求、引用示例、普通问题不得调用。时间按本次received_at解析，不能猜接收者或日程。
        缺少时间/对象用spec.questions追问并保存待补全草稿，绝不自行默认立即发送。
        """
        # call 的第一个参数是绑定身份；闭包不接受模型提供的账号。
        return json.dumps(session.call(lambda _actor: session.prepare(action, spec, identifier)), ensure_ascii=False)

    return [list_notification_tasks, get_notification_task, prepare_notification]
