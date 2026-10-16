# 工程目录

```text
src/maidanlao/
├── agent/
│   ├── factory.py              # 统一create_agent、提示词与框架调用限制
│   ├── context.py              # 入口绑定真实身份与消息时间
│   ├── notifications.py        # 通知查询、详情和变更预览工具
│   ├── group_forwarding.py     # 群转发规则、批次查询和预览工具
│   ├── tools.py                # 检索工具和请求内证据
│   ├── files.py                # 只读文件准备、否定保护及别名/指代定位
│   └── utilities.py            # 北京时间、市区逐小时天气工具与失败保护
├── rag/
│   ├── ingestion.py            # 导入、原文恢复和事务重建
│   ├── normalizer.py           # Markdown 元数据规范化
│   ├── chunker.py              # 按长度分块和标题条款锚点
│   ├── retriever.py            # 混合召回及证据选择
│   ├── reranker.py             # RRF 排名融合
│   ├── context.py              # 去重、字符预算和完整段落摘录
│   ├── validation.py           # 来源关联、状态、日期和版本校验
│   ├── originals.py            # 原件文件名、大小和 SHA256 校验
│   ├── embeddings.py           # 显式注入的离线 Hash
│   ├── models.py               # 检索 DTO，独立于 ORM
│   ├── evaluation.py           # 召回评测
│   └── regression.py           # 文档事实检索回归
├── persistence/
│   ├── database.py             # 共享文件库 Engine、独立 Session
│   ├── models.py               # 必要表的 SQLAlchemy 映射
│   ├── repository.py           # 知识与完整聊天轮次仓储
│   ├── delivery.py             # 回答快照、逐项交付回执及重试去重
│   ├── migrations.py           # 非破坏性结构升级
│   └── transactions.py         # 原生 Session 事务和保存点
├── notifications/
│   ├── contracts.py            # 通知DTO、北京时间和周期日期计算
│   ├── service.py              # 权限、追问、预览、确认、取消和补发
│   ├── store.py                # 短事务、任务快照、租约与发送进度
│   └── scheduler.py            # 五秒扫描、逐人发送和执行报告
├── group_forwarding/
│   ├── service.py              # 固定60秒窗口、规则、预览与精确确认
│   └── worker.py               # 私聊原文交付、原生文件转发、确定性发布
├── llm/
│   ├── client.py               # ChatOpenAI、在线 Embedding
│   ├── rerank.py               # 百炼原生 Rerank
│   └── protocols.py            # 注入客户端契约
├── adapters/weather.py         # 区县坐标校验与Open-Meteo小时预报
├── adapters/napcat/
│   ├── bot.py                  # 连接、去重、会话锁、分页回复和原件发送
│   ├── client.py               # OneBot HTTP 请求与响应校验
│   ├── events.py               # QQ 事件规范化
│   └── rendering.py            # 引用文本消息段
├── api/
│   ├── server.py               # 文档管理 HTTP 服务与页面
│   ├── auth.py                 # Token 鉴权
│   └── routes/health.py        # 存活检查
├── config/
│   ├── settings.py             # 模型、向量、重排及数据库配置
│   └── env.py                  # 环境变量优先的 .env 加载
├── text.py                     # 模型 Markdown 转 QQ 纯文本
├── service.py                  # 历史 → Agent → 来源校验 → 完整问答保存
├── cli.py                      # 运维命令和两组件启动
└── main.py / __main__.py        # 统一入口
tests/                          # 保留链路的离线契约与集成测试
deploy/                         # QQ、后台两份 systemd 模板
pyproject.toml / uv.lock         # 依赖声明及可复现安装
```

`service.py` 负责消息用例；`agent` 选择检索、文件、实时查询与已授权的管理能力；`rag` 返回普通字典证据；`persistence` 负责数据生命周期。API 与 QQ 不暴露 ORM 对象给模型，Agent 仅可准备已授权的业务变更，不注册确认或发送工具。

没有 graph、nodes、edges、runner、业务服务容器或长期记忆。管理员通知调度与群转发工作器在机器人进程内运行，不恢复旧提醒服务或部署模板；兼容旧库不要求继续保留旧功能的 Python 映射。
