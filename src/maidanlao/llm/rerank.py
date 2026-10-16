"""百炼重排适配器：传输与结果校验，不承担候选筛选或数据库访问。"""

from __future__ import annotations

import json
import math
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from maidanlao.config.settings import RerankSettings


class RerankError(RuntimeError):
    """重排请求失败或结果无法与输入候选可靠对应。"""


class DashScopeRerankClient:
    """调用原生 text-rerank 接口；返回每个输入文档对应的相关度。"""

    def __init__(self, settings: RerankSettings):
        self.settings = settings

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        """按输入顺序返回完整分数；异常向调用者传播，不输出密钥或正文。"""
        if not documents:
            return []
        request = self._build_request(query, documents)
        result = self._send_request(request)
        return self._parse_scores(result, len(documents))

    def _build_request(self, query: str, documents: list[str]) -> Request:
        """请求全部候选的排名，确保云端排序不会悄然丢弃证据。"""
        body = {
            "model": self.settings.model,
            "input": {"query": query, "documents": documents},
            "parameters": {"top_n": len(documents), "return_documents": False},
        }
        return Request(
            self.settings.url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.settings.api_key}"},
            method="POST",
        )

    def _send_request(self, request: Request) -> object:
        """执行有超时限制的请求；不把服务端错误正文带入日志。"""
        try:
            with urlopen(request, timeout=self.settings.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise RerankError(f"rerank_http_{exc.code}") from exc
        except (URLError, OSError, ValueError) as exc:
            raise RerankError("rerank_request_failed") from exc

    @staticmethod
    def _parse_scores(result: object, count: int) -> list[float]:
        """按 index 还原顺序，拒绝缺项、重复、越界或非有限分数。"""
        try:
            if not isinstance(result, dict):
                raise ValueError
            items = result["output"]["results"]
            if not isinstance(items, list) or len(items) != count:
                raise ValueError
            scores: dict[int, float] = {}
            for item in items:
                index = item["index"]
                score = item["relevance_score"]
                if type(index) is not int or not 0 <= index < count or index in scores:
                    raise ValueError
                if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
                    raise ValueError
                scores[index] = float(score)
            return [scores[index] for index in range(count)]
        except (KeyError, TypeError, ValueError) as exc:
            raise RerankError("rerank_invalid_response") from exc
