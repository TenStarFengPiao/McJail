# 技术文档：统一 LangChain Agent

## 目标与边界

项目为 QQ / CLI 多功能助手，统一使用 LangChain 1.x `create_agent()`。底层 LangGraph 是传递依赖，项目不维护自定义图、不使用 checkpoint、interrupt 或 Store。不办理班级事务、预约或个人数据查询；群转发仅限管理员私聊预览并确认的批次。RAG、文件、时间、天气和管理员通知查询/预览均是同一个 Agent 的工具。通知确认由 QQ 入口校验真实身份与精确命令，到期发送由独立调度器执行。

Web 后台仅管理知识文档和索引，不提供 HTTP 聊天接口。SQLAlchemy 负责 SQLite 中的知识与普通聊天记录。

## 消息链路

1. QQ 首先截断全部群事件，仅按已确认规则采集；私聊规范化、过滤自身消息、去重并按用户加会话锁。CLI 直接调用同一消息服务。
2. `AssistantService` 创建请求专属数据库工作单元，读取最近八个完整问答轮次，组成 Human / AI 历史。
3. `build_agent` 注册只读 `search_knowledge(query)`、`get_document_file(document_id)`、`get_current_time()` 和 `get_weather(city, district, day, province)`；Agent 根据问题选择检索、文件、时间或天气查询；真实QQ管理员另注册 list_notification_tasks、get_notification_task、prepare_notification。群转发管理还注册 query_group_forwarding、prepare_group_forwarding、control_group_forwarding。CLI 不注册管理工具，用户号不能冒充QQ管理员。天气要求明确市和区/县，逐小时概率和雨量只能解释为预报；文件工具只准备附件。全部工具共享八次调用限制。
4. 框架 middleware 限制最多八次工具调用、十次模型调用；`parallel_tool_calls=False` 与 `max_concurrency=1` 共同保证工具串行。
5. 工具执行混合检索、来源校验和上下文预算装配，返回可读正文及 `artifact.sources`。数据库、Session 和配置密钥不进入工具参数。
6. 请求内收集实际提供给模型的来源，生成完成后再次校验版本/有效性，附加来源标题列表。
7. `prepare_response` 不保存本轮，应用响应包含 `text`、`sources`、可选 `attachments`、`reply_target` 和待提交信息。只要使用通知工具，本轮完整回复私聊管理员；回复目标随快照冻结，旧快照默认原会话目标。QQ 全部发送确认后调用 `commit_turn` 原子保存问题、答案与来源。原件字节不进入聊天记录或模型上下文。

原件请求由 Agent 判断，服务按明确标题、登记别名或纯指代的同会话最近来源定位。未知明确名称不回退历史；否定或矛盾请求阻止附件。多候选要求选择；缺失原件、文档停用、版本变化或校验失败不发送其他文件。详见 FILE_DELIVERY.md。

失败不持久化本轮问题或中间工具消息。通知草稿单独原子保存；模型在预览落盘后失败，仍交付原始预览，不激活任务。无证据时允许 Agent 改写重试，仍无资料则由模型简短说明，完整答复可以保存。全部检索通道异常、模型异常、空回答或超限保留明确失败响应。闲聊、一般常识、措辞修改、语气与格式要求可直接回应；已删除固定闲聊白名单和无来源统一拒答。学校政策和文档事实的证据要求由提示词约束，来源仍校验状态和版本，后置校验不证明答案有据。表达偏好通过最近八轮会话历史传入后续请求，没有长期偏好存储。

同会话串行保证在单个 QQ 进程内成立；多个机器人副本并发消费同一账号不属于当前部署方式。独立 CLI 请求不提供跨进程队列或工作流恢复。

## RAG

检索使用同模型同维度向量召回与关键词 BM25，经 RRF 合并后可选百炼云端 Rerank。云端排序异常沿用 RRF 顺序。任一召回通道失败时保留另一通道证据；两个通道均失败才报告检索故障，没有命中则报告资料不足。不会混入 Hash。

小语料仅在完整证据装得下预算时直接返回整库证据，因此该路径无需在线向量或重排调用；这不能证明外部模型当前可用。两条路径使用相同分类和历史归档范围。已删除条款号推断培养类型、类型过滤、邻块扩展和本地加权排序。资料适用性由维护者在入库前保证，模型仍须依据正文作答。

来源校验批量检查文档/块关联、active 状态、生效日期和版本；返回前结束旧查询快照，重新读取当前版本。它不证明模型答案与原文语义一致；提示词要求根据证据回答并区分普通本科、成人教育和不同版本。

上下文默认 4200 字符，包含来源标题及分隔符。普通块完整取舍；超大块摘取能完整容纳的段落，并注明原文字符位置。每次检索有独立预算，请求最多八次检索，不缓存检索结果。

Embedding 客户端的 `model_name` 是字符串，`client` 是 LangChain 向量客户端对象。索引保存实际模型名和向量 JSON，查询仅比较相同模型名及相同维度。导入拒绝空向量、非有限值和同文档内变化的维度。离线测试显式注入 `LocalHashEmbeddingClient`，离线向量不代表线上语义效果。

## 持久化与迁移

新库只创建：

| 表 | 内容 |
|---|---|
| knowledge_documents | 来源、状态、版本、原文、登记别名及源文件路径 |
| knowledge_originals | 文档 ID、不可覆盖的原件文件名、二进制字节和 SHA256 |
| delivery_resources | 无需索引的模板原件、标题、来源、主题词、届别和自动发送规则 |
| knowledge_chunks | 正文块及来源；旧语义标签列仅保留数据库兼容，不参与推断或筛选 |
| knowledge_vector_indexes | 实际模型对应的向量 |
| knowledge_keyword_indexes | 关键词 / BM25 索引 |
| rag_test_cases | 检索评测期望 |
| chat_turns | 完整问题、答案、来源、会话键和幂等请求 ID |
| delivery_attempts | 回答快照、逐项交付回执及失败/结果未知状态，不包含原件字节 |
| schema_migrations | 兼容升级记录 |

会话键编码为 JSON 数组 `[消息来源, 群号或null, 用户号]`，避免分隔符碰撞。私聊不同用户、群与私聊、不同群、同群不同用户均隔离。

升级旧库按需新增聊天表、原文、原件表、文档别名、聊天请求 ID 和交付回执表，保留旧业务表、checkpoint 和原文件，不读取旧 checkpoint 历史、不执行自动向量重建。当前兼容对象为升级前已具备知识库字段的项目数据库。

文件库 Engine 在进程内复用，每个请求拥有独立 Session；结构只在首次初始化时检查，显式升级可调用 initialize_database(path, force=True)。SQLite 使用 WAL、外键和 30 秒 busy timeout，文件库不保留连接池连接。已删除旧 execute/Record/QueryResult 适配、自定义事务深度和 Session 锁。仓储写操作使用原生 Session 事务，入库原语只 flush。导入和重建先在事务外批量计算向量，再在短事务中原子写入；重建按预期版本检查并发修改。没有新增任务队列。

重建保留文档 ID 并更新版本，原子替换分块和索引，兼容旧附件表的外键。原文恢复优先级为数据库原文、normalized_file_path、file_path、已有块。最后一种方式只能恢复已有分块正文，不能保证等同原始文件。

## 文档后台

使用标准库 `ThreadingHTTPServer`。导入支持 JSON、URL 表单和 multipart 的 UTF-8 `.txt/.md` 文件。普通请求上限 2 MiB；multipart 请求上限 22 MiB，其中原件上限 20 MiB。原始 PDF / Word 仅通过 multipart 的 `original_file` 字段上传，不作为 UTF-8 检索正文解码。

| 方法与路径 | 行为 |
|---|---|
| GET /admin | 转义元数据的文档管理页面 |
| GET /admin/documents | 返回 documents 数组，不含大段原文 |
| POST /admin/documents/import | 导入并建立全部索引，成功 201 |
| POST /admin/documents/{id}/reindex | 同步重建，成功 200、缺失 404 |
| POST /admin/documents/{id}/original | 给已有文档关联原件；重复上传同名同字节幂等，拒绝覆盖 |
| GET /admin/documents/{id}/original | 鉴权并校验后下载原件字节，缺失 404、完整性失败 409 |
| GET /health | Token 已配置返回 200，否则 503 |

除健康检查外，保留的后台路由要求 `Authorization: Bearer TOKEN` 或 query token。未知或已退出路由返回 404。健康接口仅表示服务存活与管理 Token 已配置，不检查 QQ 或云端模型。

导入字段：`title`、`source`、ISO 日期 `publish_date`、支持的 `category`、`raw_text` 必填；`scope` 默认 school。文件上传替代 raw_text。分类与原始向量请求不由浏览器随意指定，使用后端配置的模型。校验错误 400、请求过大 413、索引服务异常 503。

可选 `document_id` 使用 1 至 100 位字母、数字、下划线或连字符；留空自动生成。可同时上传 `original_file`，文件名限 PDF、DOCX 或 DOC。原件和检索正文共享文档 ID，随索引导入一起提交或回滚；单独补传原件不更改文档检索版本。新原件应导入为新文档，原件表拒绝静默覆盖。

交付资料通过 CLI `import-resource` 登记，`list-resources` 列出元数据，不参与 Embedding 或分块。Agent 通过登记标题和主题词定位明确索要的资料，纯指代请求可复用同会话来源；仅讨论模板不再自动附送。旧 auto_send 字段保留兼容。知识文档可通过 `set-doc-aliases` 登记简称。文件保持原始字节，准备和重试时验证 SHA256。

QQ 文本回复禁止 Markdown。提示词要求纯文本，服务层转换常见标题、强调、列表、链接、表格和代码围栏；发送层在分页之前再次转换，聊天记录保存实际纯文本答案。

参考来源由服务端统一输出：去掉模型自写的独立尾部来源段落，再按实际工具来源标题去重。正文中的文档引用保留，避免连续出现两段“参考来源”。

知识库原件只在 QQ 私聊发送，使用 `upload_private_file` 的 `base64://` 资源，传输原始字节。附件和分页文本按项记录回执，全部 confirmed 后提交完整问答。重试跳过已确认项目，恢复相同回答；超时或 sending 状态停止盲目重发。部分失败分别说明已确认与失败项目，confirmed 不代表用户已读。CLI 不交付附件，也不保存附件成功记录。

示例：

```json
{
  "title": "补考通知",
  "source": "教学办",
  "publish_date": "2026-10-01",
  "category": "补考通知",
  "scope": "school",
  "raw_text": "补考报名截止到周五。"
}
```

## 配置、命令与部署

配置加载不覆盖系统环境变量。数据库优先级：显式参数 → DATABASE_URL → MAIDANLAO_DB → maidanlao.sqlite3。支持本地路径及 sqlite:/// URL；没有实现 PostgreSQL 运行适配。

聊天模型默认 deepseek-v4-flash；Embedding 默认 qwen3.7-text-embedding-flash；Rerank 默认 qwen3.7-text-rerank。Rerank 密钥为空时复用 Embedding 密钥，未配置密钥或模型为空时使用 RRF 顺序。完整示例见根目录 `.env.example`。

CLI 保留 ask、run-bot、run-admin、run-all、smoke、eval-rag、eval-rag-regression、reingest-doc，新增 import-doc 和 list-docs。`run-all` 只管理两个子进程。具体参数见 README 或各命令 --help。

部署模板只有 deploy/maidanlao-bot.service 和 maidanlao-admin.service。替换模板路径，数据库指向可写数据目录。生产升级顺序：

1. 停止旧服务和旧提醒 timer，备份 SQLite（必要时使用 SQLite 在线备份或停机合并 WAL），备份源文档。
2. 在临时库验证新 Embedding 在线可用，不能用 Hash smoke 代替此步骤。
3. 安装锁定依赖，按需通过 list-docs 列出生产文档，再显式逐篇 reingest-doc。
4. 启动 QQ 和精简后台，确认检索来源和重启后的追问。旧业务表与 checkpoint 文件继续留存。
5. 回滚时恢复备份数据库与相应旧代码；单纯切换模型名不会恢复已经替换的向量。

代码整改和离线验证不连接真实 QQ、不调用真实模型、不重建生产库。

## 验证

测试覆盖真实 create_agent 工具循环、八次检索上限与串行工具、重启追问及会话隔离、失败不保存半轮、索引模型与维度、Rerank 降级、来源校验、上下文预算、导入/重建原子性、旧库数据保留、后台鉴权及 QQ 消息回复。

CI 保持 Python 3.11/3.12/3.13 与 Linux/Windows 矩阵，锁定安装、Ruff 和格式检查、全量测试与 80% 覆盖率门槛。离线测试不承诺提供方实际支持所配置的模型名称，线上连通性需要部署前单独验证。

## 统一通知工具

通知查询与预览由统一 Agent 选择，独立结构化解析器已移除。自然语言不再先按通知关键词或固定查询句式分流。精确确认、取消和旧查询命令保留入口兼容；通知工具没有实际确认、发送权限。通知预览由服务生成原文，覆盖模型对预览或执行结果的改写。详见 [统一 Agent 接口与边界](UNIFIED_AGENT.md)。

当前群入口全局静默：任何群消息均不进入问答或确认命令链路。来源群按确认规则收集固定60秒窗口，管理员私聊要求生成预览并确认版本后，由独立工作器发布。统一 Agent 新增 query_group_forwarding、prepare_group_forwarding、control_group_forwarding，仅真实QQ管理员获得；模型没有确认或发送工具。新增四张群转发表，迁移版本7，新库16表；通知调度不变。详细交互、原生转发限制及失败恢复见 [群转发说明](GROUP_FORWARDING.md)。
