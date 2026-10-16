# McJail · 麦当牢

![McJail Logo — an open book and a smiling chat bubble](assets/mcjail-logo.png)

An AI-powered campus assistant for QQ.

McJail helps students find school policies, understand notices, and get the documents they need. It combines knowledge retrieval, original document delivery, scheduled notifications, and admin-reviewed group message forwarding to make campus information easier to access and share.

麦当牢是面向校园场景的 QQ 智能助手，支持知识问答、原件与模板交付、管理员通知及群消息审批转发，也提供 CLI 和知识文档管理后台。

从“这条规定是什么意思”到“把原文件发给我”，McJail 将政策查询、资料获取与通知管理放到同一个 QQ 对话入口。学生通过私聊获取帮助，管理员通过预览与明确确认管理通知和群消息发布。

Logo 将打开的书与聊天气泡结合：书页代表知识与文档，笑脸代表友好的对话体验，左侧三道竖线呼应“牢”的名字。详见 [项目介绍与 Logo 设计](docs/INTRODUCTION.md)。

## Features / 主要功能

- Knowledge Q&A: retrieve school policies and notices, with server-generated source references.
- File delivery: send original documents and registered templates through QQ.
- Admin notifications: preview and explicitly confirm immediate, one-time, daily, or weekly notifications.
- Reviewed forwarding: collect source-group messages in fixed 60-second batches for administrator review and explicit publication approval.
- Time and weather: query Beijing time and hourly weather forecasts for Chinese cities and districts.
- Document management: import Markdown/text, attach original files, and rebuild knowledge indexes through CLI or an authenticated web dashboard.

Built with Python, LangChain, SQLAlchemy, SQLite, and NapCat / OneBot 11. The Python package and command names remain `maidanlao`.

## How it works / 工作方式

QQ / CLI 提问 → 统一 LangChain Agent 选择工具 → 根据实际结果回答。RAG、原文件、时间和天气是助手的功能；真实QQ管理员另有通知列表、详情和变更预览工具。通知确认由入口校验，到期发送由独立调度器执行。详见 [统一 Agent 说明](docs/UNIFIED_AGENT.md)。

## 安装与运行

要求 Python 3.11+，推荐使用 uv 按锁文件安装：

```powershell
uv sync --locked --extra dev
Copy-Item .env.example .env
uv run python -m maidanlao.cli smoke
```

填写 `.env` 中的聊天模型、Embedding 密钥、NapCat 地址和管理 Token。默认 Embedding 为 `qwen3.7-text-embedding-flash`，Rerank 为 `qwen3.7-text-rerank`。导入时在线向量失败会报错，检索时可降级到关键词；不会改用 Hash；smoke 使用显式离线 Hash，仅验证工程链路。

```powershell
uv run python -m maidanlao.cli ask --open-id 123 --source private --text "补考报名什么时候截止"
uv run python -m maidanlao.cli run-bot
uv run python -m maidanlao.cli run-admin --host 127.0.0.1 --port 8787
uv run python -m maidanlao.cli run-all
```

`run-all` 只启动机器人和文档后台，任一组件退出会清理另一个。所有群聊均静默，@ 和管理员命令也不回复；群消息只按已确认规则采集。同一私聊用户的消息串行处理，回复使用纯文本，每页最多 2000 字符；模型输出的 Markdown 在保存和发送前转换为普通文本。

## 知识文档

```powershell
uv run python -m maidanlao.cli import-doc --file notice.md --title "补考通知" --source "教学办" --publish-date 2026-10-01 --category "补考通知"
uv run python -m maidanlao.cli list-docs
uv run python -m maidanlao.cli reingest-doc --document-id doc_xxx
uv run python -m maidanlao.cli eval-rag
uv run python -m maidanlao.cli eval-rag-regression
```

导入支持 UTF-8 文本或 Markdown；分类须使用后台下拉菜单中的已有分类。导入和重建同步执行：先在事务外批量计算向量，再用短事务提交原文、分块、关键词和向量；失败保留旧版本。重建提交前校验文档版本，拒绝覆盖计算期间的新版本。

同一知识文档可以同时保存清洗后的 Markdown 和学校原始 PDF / Word：Markdown 用于检索，原件保持原始字节用于交付。可在后台导入时同时选择两份文件，或给已有文档补传原件。原件最大 20 MiB，关联后不可覆盖；新政策原件应作为新文档导入，重建索引不改动原件。

```powershell
uv run python -m maidanlao.cli import-doc --document-id doc_degree_rules_2026 --file degree_rules.md --original-file degree_rules.pdf --title "学士学位授予规定" --source "学校" --publish-date 2026-06-15 --category "毕业资格"
uv run python -m maidanlao.cli attach-original --document-id doc_existing --file original.pdf
```

QQ 用户查询后可以说“把这个规定的原文件发给我”。机器人根据同一会话最近引用的文档 ID 定位原件，支持重启后继续索要；多份引用要求明确标题，版本已变化时要求重新查询。也可直接说“把《学士学位授予规定》的原文件发给我”。缺少原件或发送失败会明确说明。CLI `ask` 仍只输出文本，文件交付由 QQ 入口完成。

模板等无需 Embedding 的文件使用交付资料登记，保存在 `delivery_resources` 中，不创建知识文档、分块或向量。由 Agent 判断是否明确索要文件，仅讨论模板不会自动发送；否定或矛盾请求阻止附件。登记主题词用于定位，`--cohort` 限定届别。旧 `auto_send` 字段和 `--only-on-request` 参数仅保留兼容，不再启用自动附送。

管理员可登记简称：`uv run python -m maidanlao.cli set-doc-aliases --document-id doc_example --alias 查重指南`。明确名称找不到时不回退到历史；纯指代请求才复用同会话来源。模型尾部来源段落会被移除，服务端按真实来源标题统一输出一次“参考来源”。

QQ 先准备回答，再发送附件和分页文本，全部接口确认成功后才原子保存完整历史。`delivery_attempts` 保留回答快照与逐项回执；同一事件重试跳过已确认项目，网络异常或进程中断的项目不盲目重发。确认代表接口成功，不代表用户已读。部分失败分别说明已确认与失败项目。CLI 不发送附件，也不保存附件交付成功记录。详细规则见 [文件交付说明](docs/FILE_DELIVERY.md)。

```powershell
uv run python -m maidanlao.cli import-resource --resource-id res_opening_report_2027 --file "开题报告样式2027.docx" --title "2027届毕业论文开题报告模板" --source "提供的模板文件" --keyword "开题报告" --keyword "开题模板" --cohort 2027
uv run python -m maidanlao.cli list-resources
```

后台 `/admin` 支持列表、文本/文件导入和指定文档重建。可使用 `/admin?token=YOUR_TOKEN` 打开页面，程序请求推荐 `Authorization: Bearer YOUR_TOKEN`。接口只有：

- `GET /admin/documents`
- `POST /admin/documents/import`
- `POST /admin/documents/{id}/reindex`
- `POST /admin/documents/{id}/original`：给已有文档关联原件
- `GET /admin/documents/{id}/original`：经后台鉴权下载原件
- `GET /admin`、`GET /health`

旧业务、旧附件路由及 HTTP 聊天路由均返回 404；文档原件使用上述新接口。

## 运行约定

现在几点、今天几号等问题调用服务器时钟，统一返回北京时间。天气按中国市和区/县查询，例如“郴州市苏仙区明天几点可能下雨”；缺少地点先追问，不能推测用户所在地。支持今天起七天内逐小时降水概率、预计雨量与温度，时段均为北京时间。OpenStreetMap 定位核对市区，Open-Meteo 提供预报，不需要额外 API 密钥；预报不能保证精确的下雨开始时间，故障时明确说明。工具只查询，不影响通知调度。详见 [时间与天气工具](docs/TIME_WEATHER.md)。

管理员 QQ `3244104494` 仅可私聊机器人配置通知；管理回执统一私聊。支持自然语言立即发送、单次、每天和每周提醒，先预览，再回复 `确认通知 任务号` 才生效。全员指机器人的全部 QQ 好友，名单在确认时固定；每次通知所有接收者收到完全相同的正文和分页。会议提醒按“会议提醒／时间／地点／事项”格式输出，日期使用北京时间的绝对日期。

例如：“明天7:30提醒所有同学，明天上午8点去四教参加毕业答辩会议。”可以自然语言查询“有哪些通知待发”“明天早上有什么提醒”“那条会议通知发了吗”，Agent 按实际任务状态回答。自然语言取消先展示预览，回复 `确认取消通知 任务号` 才生效；仍兼容 `查看通知任务`、`查看通知 任务号`、`取消通知 任务号` 等精确命令。通知调度随 `run-bot` 启动，重启可恢复；30分钟内补发，超过则跳过。网络结果未知不自动重发，管理员可预览并明确确认补发。管理员查询和预览在回复成功后保存管理员自己的对话；通知执行不会给接收者写入虚假问答。统一 Agent 只注册通知查询和预览工具，不注册确认或发送工具。CLI 的用户号不能授予QQ管理员权限。详见 [管理员通知说明](docs/NOTIFICATIONS.md)。

Agent 每轮最多八次工具调用（检索、文件、时间、天气与通知工具共享），工具串行执行；提示词要求学校政策与文档事实根据有效来源回答。闲聊、一般常识、措辞修改、语气和格式要求无需检索，不使用固定闲聊白名单或无来源统一拒答。没有资料时由模型简短说明并保存完整回复；检索失败、模型失败、空回答或超限不保存半轮历史。表达偏好随最近八轮历史传入后续问答，不提供长期偏好存储。

最近八个完整问答轮次由 SQLAlchemy 从 `chat_turns` 加载，会话按消息来源、群号（私聊为空）、用户号隔离。不读旧 checkpoint，不恢复工作流。LangChain 底层仍使用 LangGraph，但项目没有自定义图、checkpoint、interrupt 或长期 Store。

## 升级与验证

代码不自动重建向量，也不删除存量业务表。首次连接旧库按需新增聊天表、文档原文字段、原件表、文档别名、聊天请求 ID、交付回执表、两张通知表及四张群转发表；旧 checkpoint 文件保留。

生产升级前停止服务并备份 SQLite（含未合并的 WAL）和源文档。在独立临时库确认在线 Embedding 可用后，再显式重建生产知识索引、重启服务。切换模型前备份，以免旧向量被重建替换。

```powershell
uv run python -m pytest --cov=src/maidanlao --cov-report=term-missing --cov-fail-under=80
uv run python -m ruff check src tests
uv run python -m ruff format --check src tests
uv lock --check
uv run python -m maidanlao.cli smoke
```

详细说明：[技术文档](docs/TECHNICAL_DOCUMENTATION.md)、[目录职责](docs/PROJECT_STRUCTURE.md)、[工程规范](docs/ENGINEERING_STANDARDS.md)、[请求流程](docs/AGENT_REQUEST_FLOW.md)。

群消息转发：管理员私聊配置来源群和目标群，来源群首条消息起固定60秒收集原文与文件，先私聊管理员审阅；要求生成预览并精确确认版本后才发布到目标群。所有群聊保持静默。文件只使用原消息单条原生转发，不下载上传。使用步骤与限制见 [群消息审批转发](docs/GROUP_FORWARDING.md)。
