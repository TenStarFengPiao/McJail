# 2026-10-14 管理员通知解析故障修复

生产机器人对管理员“你是谁”等普通消息回复通知错误。服务器日志确认 `OpenAIInvalidRequestError`，本地真实模型复现 HTTP 400：DeepSeek Thinking 模式不支持结构化输出所需的强制 `tool_choice`。

通知解析请求现在传入 `extra_body={"thinking": {"type": "disabled"}}`，只影响本次解析，普通知识问答继续原配置。解析异常时普通聊天返回知识问答，明确通知和草稿时间补充提示未生效。解析故障与好友故障独立记录，空名单、非好友及登录异常有对应反馈。

本地201个全量测试通过，覆盖率95.38%；Ruff、格式、锁文件检查与CLI smoke通过。新增回归覆盖普通聊天在解析服务故障时仍进入问答、草稿补充仍提示解析错误、不凭关键词创建任务，以及Thinking设置只针对解析调用。

生产保留发布目录 `/opt/maidanlao-releases/20261014-223732-workspace`，仅更新 `notifications/parser.py` 和 `notifications/service.py`，同步逐文件摘要。备份为 `/var/backups/maidanlao/20261014-224955-notification-fix`，包含原模块、原摘要及SQLite一致性备份。机器人服务重启，未覆盖数据库或修改模型环境配置。

服务器使用临时数据库完成在线验证：“你是谁”进入问答；真实模型区分明天07:30发送与08:00活动；NapCat好友校验通过，50位好友形成临时草稿预览。测试没有确认任务、没有运行发送批次、没有发送QQ消息。

回滚时停止机器人，从备份恢复两个模块及 `release-manifest.json` 后启动机器人；代码回滚无需恢复数据库。
