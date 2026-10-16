"""模型边界协议；测试可注入离线向量和重排客户端。"""

from typing import Protocol


class EmbeddingClient(Protocol):
    """模型标识与客户端对象分离，向量只能匹配同模型索引。"""

    model_name: str

    def embed(self, texts: list[str]) -> list[list[float]]:
        """返回与输入顺序一致的向量；失败抛出异常。"""
        ...


class RerankClient(Protocol):
    """重排分数与输入文档一一对应。"""

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        """返回按输入顺序排列的相关度，失败抛出异常。"""
        ...
