# 文本问答入口定位

旧 invoke_text 与自定义图入口已删除。当前调用关系：

- CLI ask → service.AssistantService.handle_text → handle_message。
- NapCatBot._process_message → build_response_for_text → AssistantService.handle_message。
- handle_message → agent.factory.build_agent → agent.tools.search_knowledge → rag.retriever.HybridRetriever。
- 成功输出 AssistantResponse(text, sources)，由 CLI 打印或 QQ 分页引用发送。

详细数据契约和失败行为见 TECHNICAL_DOCUMENTATION.md。

