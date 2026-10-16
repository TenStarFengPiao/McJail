from __future__ import annotations

import math
import re

from maidanlao.rag.models import DocTypeMapping, KnowledgeChunk

CATEGORY_MAP = {
    "补考管理": DocTypeMapping("school_policy", "school", "kb_school_policy"),
    "重修管理": DocTypeMapping("school_policy", "school", "kb_school_policy"),
    "毕业资格": DocTypeMapping("school_policy", "school", "kb_school_policy"),
    "补考通知": DocTypeMapping("college_notice", "college", "kb_college_notice"),
    "重修通知": DocTypeMapping("college_notice", "college", "kb_college_notice"),
    "毕业论文安排": DocTypeMapping("college_notice", "college", "kb_college_notice"),
    "答辩安排": DocTypeMapping("college_notice", "college", "kb_college_notice"),
    "培养方案": DocTypeMapping("major_plan", "major", "kb_major_plan"),
    "学分要求": DocTypeMapping("major_plan", "major", "kb_major_plan"),
    "毕业条件说明": DocTypeMapping("major_plan", "major", "kb_major_plan"),
    "班级正式通知": DocTypeMapping("class_notice", "class", "kb_class_notice"),
    "过期政策": DocTypeMapping("history_archive", "history", "kb_history_archive"),
    "旧通知": DocTypeMapping("history_archive", "history", "kb_history_archive"),
    "已解决问答": DocTypeMapping("resolved_qa", "class", "kb_resolved_qa"),
}


REQUIRED_METADATA = {
    "title",
    "source",
    "publish_date",
    "scope",
    "category",
    "doc_type",
    "source_level",
    "collection_name",
    "status",
    "version",
}


HISTORY_TERMS = {"历史", "以前", "往届", "旧版"}


SENSITIVE_TERMS = {"password", "secret", "token", "raw_log", "system_prompt", "身份证", "手机号", "成绩"}


def map_category_to_doc_type(category: str) -> DocTypeMapping:
    if category not in CATEGORY_MAP:
        raise ValueError("unsupported_category")
    return CATEGORY_MAP[category]


def _tokens(text: str) -> list[str]:
    """词元：ASCII 单词 + 单个汉字。

    保留单字切分是为了 **不改变 chunk 体积语义**（``_token_count`` 决定分块边界，
    历史数据都是按这个口径切的）。用于"匹配"的场景请用 :func:`_match_tokens`。
    """
    words = re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fa5]", text.lower())
    return words


_CJK_RUN = re.compile(r"[\u4e00-\u9fa5]+")


_ASCII_RUN = re.compile(r"[A-Za-z0-9]+")


def _keyword_tokens(text: str) -> list[str]:
    """中文双字及 ASCII 单词，保留重复项用于 BM25 词频。"""
    terms = [match.group(0) for match in _ASCII_RUN.finditer(text.lower())]
    for match in _CJK_RUN.finditer(text):
        run = match.group(0)
        terms.extend([run] if len(run) == 1 else [run[i : i + 2] for i in range(len(run) - 1)])
    return terms


def _match_tokens(text: str) -> set[str]:
    """关键词索引和查询使用相同分词口径。"""
    return set(_keyword_tokens(text))


def _token_count(text: str) -> int:
    return len(_tokens(text))


def _normalize_vector(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    size = min(len(left), len(right))
    numerator = sum(left[index] * right[index] for index in range(size))
    left_norm = math.sqrt(sum(value * value for value in left[:size]))
    right_norm = math.sqrt(sum(value * value for value in right[:size]))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)


def annotate_chunk(chunk: KnowledgeChunk, head_chars: int) -> None:
    """给块正文加标题和条款号锚点前缀。

    锚点 = `【文档标题｜条款号…】`。标题必须留 —— 它是真实检索线索
    （去掉后召回通过数下降，实测 4/6 → 3/6）；条款号取块内全部，
    因为块按字数切，只标首个会导致查另一条时找不到这一块。

    """
    articles = re.findall(r"第[一二三四五六七八九十百零〇\d]+条", chunk.chunk_text)
    prefix_parts = []
    title = (chunk.section_path or "").strip()
    if title:
        prefix_parts.append(title[:head_chars])
    if articles:
        prefix_parts.append("、".join(dict.fromkeys(articles))[:80])
    if prefix_parts:
        prefix = "【" + "｜".join(prefix_parts) + "】\n"
        if not chunk.chunk_text.startswith(prefix):
            chunk.chunk_text = prefix + chunk.chunk_text
    chunk.chunk_token_count = _token_count(chunk.chunk_text)


def strip_front_matter(text: str) -> str:
    """去掉规范化时插入的 YAML 元数据头。

    元数据是结构化字段（title/category/...），已经存在 ``knowledge_documents`` 里，
    再放进 chunk 正文只会稀释语义信号 —— 线上事故中每块约 250 字是元数据，
    而这些块的向量相似度只有 0.25~0.33。检索侧用本函数剥离，
    入库侧则由 :meth:`KnowledgeIngestService.ingest_text` 保证不再写入。
    """
    stripped = (text or "").lstrip()
    if not stripped.startswith("---"):
        return text
    end = stripped.find("\n---", 3)
    if end == -1:
        return text
    return stripped[end + 4 :].lstrip()
