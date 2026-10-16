"""只读知识工具；单次请求的证据与错误不进入会话持久化状态。"""

import logging
from dataclasses import dataclass, field

from langchain_core.tools import tool

from maidanlao.rag import HybridRetriever
from maidanlao.rag.validation import validate_sources

logger = logging.getLogger(__name__)


@dataclass
class SearchSession:
    """请求内证据集合；不跨请求复用。"""

    retriever: HybridRetriever
    sources: dict[str, dict] = field(default_factory=dict)
    failed: bool = False

    def search(self, query: str) -> tuple[str, dict]:
        """执行检索、来源校验与预算装配；异常返回明确失败信息。"""
        try:
            result = self.retriever.search(query)
            if result.status == "retrieval_failed":
                raise RuntimeError("retrieval_channel_failed")
            sources, problems = validate_sources(self.retriever.repos, result.evidence_chunks)
            sources, context, _ = self.retriever.assemble_context(sources, self.retriever.MAX_CONTEXT_CHARS)
            if not sources:
                return "没有找到有效证据，请改写查询或明确说明资料不足，不能凭印象回答。", {"sources": []}
            for source in sources:
                self.sources[source["chunk_id"]] = source
            return context, {"sources": sources, "excluded_count": len(problems)}
        except Exception as exc:
            self.failed = True
            logger.warning("knowledge_search_failed type=%s", type(exc).__name__)
            return "知识库检索暂时不可用，请明确说明本轮无法核实，不要生成政策结论。", {"sources": []}


def build_search_tool(search: SearchSession):
    """仅 query 对模型可见，仓储与 Session 通过请求闭包注入。"""

    @tool(response_format="content_and_artifact")
    def search_knowledge(query: str) -> tuple[str, dict]:
        """检索学校政策与知识文档。遇到追问先结合历史补全问题，必要时换词再次检索。"""
        return search.search(query)

    return search_knowledge
