"""仅用于显式离线测试的确定性向量客户端。"""

from __future__ import annotations

import hashlib

from ._shared import _normalize_vector, _tokens


class LocalHashEmbeddingClient:
    """离线向量不与云端模型索引混用。"""

    model_name = "local-hash-embedding-v1"

    def embed(self, texts: list[str]) -> list[list[float]]:
        """按词元哈希生成 128 维向量，保持输入顺序。"""
        vectors = []
        for text in texts:
            vector = [0.0] * 128
            for term in _tokens(text):
                digest = hashlib.sha256(term.encode("utf-8")).digest()
                vector[int.from_bytes(digest[:2], "big") % len(vector)] += 1.0
            vectors.append(_normalize_vector(vector))
        return vectors
