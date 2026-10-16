"""Agentic RAG 集中配置；环境变量与客户端对象分离。"""

from __future__ import annotations

import os
from dataclasses import dataclass

from maidanlao.config.env import ensure_env_loaded

ensure_env_loaded()


@dataclass(frozen=True)
class LLMSettings:
    """Configuration for the OpenAI-compatible chat model endpoint."""

    model: str
    api_key: str
    base_url: str
    temperature: float

    @classmethod
    def from_env(cls) -> LLMSettings:
        return cls(
            model=os.getenv("LLM_MODEL", "deepseek-v4-flash"),
            api_key=os.getenv("LLM_API_KEY", ""),
            base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            temperature=float(os.getenv("LLM_TEMPERATURE", "0.3")),
        )

    def as_chat_openai_kwargs(self) -> dict[str, object]:
        return {
            "model": self.model,
            "api_key": self.api_key,
            "base_url": self.base_url,
            "temperature": self.temperature,
        }


def load_llm_settings() -> LLMSettings:
    return LLMSettings.from_env()


@dataclass(frozen=True)
class EmbeddingSettings:
    model: str
    api_key: str
    base_url: str

    @classmethod
    def from_env(cls) -> EmbeddingSettings:
        return cls(
            model=os.getenv("EMBEDDING_MODEL", "qwen3.7-text-embedding-flash"),
            api_key=os.getenv("EMBEDDING_API_KEY", ""),
            base_url=os.getenv(
                "EMBEDDING_BASE_URL",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            ).rstrip("/"),
        )

    def as_openai_embeddings_kwargs(self) -> dict[str, object]:
        return {
            "model": self.model,
            "api_key": self.api_key,
            "base_url": self.base_url,
        }


def load_embedding_settings() -> EmbeddingSettings:
    return EmbeddingSettings.from_env()


@dataclass(frozen=True)
class RerankSettings:
    """百炼原生重排接口配置；未配置密钥时沿用 RRF 顺序。"""

    model: str
    api_key: str
    url: str
    timeout: float

    @classmethod
    def from_env(cls) -> RerankSettings:
        """独立密钥优先，否则复用 Embedding 密钥；URL 为完整接口地址。"""
        return cls(
            model=os.getenv("RERANK_MODEL", "qwen3.7-text-rerank"),
            api_key=os.getenv("RERANK_API_KEY") or os.getenv("EMBEDDING_API_KEY", ""),
            # 百炼原生路径包含服务名与任务名；省略末段会返回 task can not be null。
            url=os.getenv(
                "RERANK_URL", "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
            ),
            timeout=float(os.getenv("RERANK_TIMEOUT", "15")),
        )


__all__ = [
    "EmbeddingSettings",
    "LLMSettings",
    "load_embedding_settings",
    "load_llm_settings",
]


def database_path(explicit: str | None = None) -> str:
    """统一数据库优先级：显式参数、DATABASE_URL、MAIDANLAO_DB、本地默认。"""
    return explicit or os.getenv("DATABASE_URL") or os.getenv("MAIDANLAO_DB", "maidanlao.sqlite3")
