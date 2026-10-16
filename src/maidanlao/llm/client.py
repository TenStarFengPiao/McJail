"""LangChain 对话模型与在线向量适配器。"""

from __future__ import annotations

from typing import Any

from maidanlao.config.settings import load_embedding_settings, load_llm_settings


class LangChainDeepSeekClient:
    """提供 OpenAI 兼容聊天模型对象，由 LangChain Agent 统一调用。"""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        temperature: float | None = None,
    ):
        from langchain_openai import ChatOpenAI

        settings = load_llm_settings()
        self.model = ChatOpenAI(
            model=model or settings.model,
            api_key=api_key or settings.api_key,
            base_url=base_url or settings.base_url,
            temperature=settings.temperature if temperature is None else temperature,
        )

    def bind_tools(self, tools: list[Any]):
        """委托客户端绑定工具，不维护第二套模型调用链路。"""
        return self.model.bind_tools(tools)


class LangChainEmbeddingClient:
    """在线向量适配器；model_name 是标识，client 是实际调用对象。"""

    def __init__(self, api_key: str | None = None, model: str | None = None, base_url: str | None = None):
        settings = load_embedding_settings()
        self.model_name = model or settings.model
        self.client = None
        if api_key or settings.api_key:
            from langchain_openai import OpenAIEmbeddings

            self.client = OpenAIEmbeddings(
                model=model or settings.model,
                api_key=api_key or settings.api_key,
                base_url=base_url or settings.base_url,
                tiktoken_enabled=False,
                check_embedding_ctx_length=False,
            )

    def embed(self, texts: list[str]) -> list[list[float]]:
        """按输入顺序返回在线向量；未配置密钥或调用失败均抛出异常。"""
        if self.client is None:
            raise RuntimeError("embedding_api_key_required")
        return self.client.embed_documents(texts)


__all__ = ["LangChainDeepSeekClient", "LangChainEmbeddingClient"]
