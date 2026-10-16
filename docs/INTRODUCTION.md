# McJail · 麦当牢

![McJail Logo](../assets/mcjail-logo.png)

> An AI-powered campus assistant for QQ.

## 项目介绍

McJail（麦当牢）是一款面向校园场景的 QQ 智能助手，帮助学生查询学校政策、理解通知并获取所需文档。它将知识检索、原文件与模板交付、定时通知以及管理员审批的群消息转发整合到日常聊天中，让校园信息更容易查找、理解和传达。

学生可以私聊提问，查看回答所附的参考来源，并继续索要对应原件。管理员可以查询通知任务、准备发送预览，在明确确认后启用通知；来源群消息则按规则收集，经过审阅与确认后发布。

## English introduction

McJail is an AI-powered campus assistant for QQ that helps students find school policies, understand notices, and get the documents they need. It combines knowledge retrieval, original document delivery, scheduled notifications, and admin-reviewed group message forwarding to make campus information easier to access and share.

## 主要功能

- **校园知识问答**：检索学校政策与通知，并附上参考来源。
- **资料交付**：通过 QQ 提供已关联的原始文档和登记模板。
- **管理员通知**：支持立即、单次、每天和每周通知，预览后明确确认生效。
- **群消息审批转发**：按固定 60 秒窗口收集来源群消息，由管理员审阅、预览和确认发布。
- **时间与天气**：查询北京时间和中国市区县逐小时天气预报。
- **知识文档管理**：通过 CLI 或鉴权后台导入文本、关联原件与重建索引。

## Logo 设计

图标由打开的书、聊天气泡和笑脸组成，使用明黄色与深墨蓝色，采用圆角与粗轮廓，便于作为项目标识或 QQ 头像使用。

| 元素 | 含义 |
| --- | --- |
| 打开的书 | 学校知识、政策与原始文档 |
| 聊天气泡 | 在 QQ 对话中获取帮助 |
| 三道竖线 | 呼应“牢”的谐音，也融入书页结构 |
| 笑脸 | 亲切、耐心的助手形象 |

Logo 通过内置 imagegen 生成，原图为透明底 PNG，文件位于 [`assets/mcjail-logo.png`](../assets/mcjail-logo.png)。使用时保持宽高比例，避免拉伸。

### 生成提示词

```text
Use case: logo-brand. Asset type: original logo icon for McJail (麦当牢), a friendly AI-powered campus assistant for QQ. Primary request: design one distinctive compact logo combining an open book and a rounded chat bubble into a friendly geometric mascot; three simple vertical rounded page strokes subtly evoke the 'jail' pun while the open upper edge conveys accessible knowledge. The face is two small eyes and a subtle confident smile integrated into the book/bubble, clever and mature rather than childish. Flat vector-like raster artwork, strong silhouette, consistent bold rounded geometry, balanced negative space, readable at avatar sizes. One centered mark with generous padding on a truly transparent background. Use a restrained two-color high-contrast design; warm optimistic accent and dark ink body selected harmoniously. No words, no letters, no wordmark, no gradients, no shadows, no 3D, no mockup, no watermark. Original mark; do not use golden arches or existing fast-food logos.
```

## 技术栈

Python · LangChain · SQLAlchemy · SQLite · NapCat / OneBot 11

安装、配置和运行方式见 [README](../README.md)。
