"""证据上下文装配：去重、预算控制与完整段落摘录。"""

from __future__ import annotations

import re
from typing import Any

from ._shared import strip_front_matter


def render_evidence_block(index: int, evidence: dict[str, Any]) -> str:
    """把入选证据渲染为模型可读正文，编号与入选列表保持一致。"""
    return (
        f"[证据 {index + 1}]\nsource_title: {evidence['source_title']}\n"
        f"section: {evidence['section_path']}\ncategory: {evidence['category']}\n"
        f"rerank_score: {evidence['rerank_score']}\ncontent:\n{strip_front_matter(evidence['content'])}"
    )


def assemble_context(evidence: list[dict[str, Any]], max_chars: int) -> tuple[list[dict], str, list[dict]]:
    """按输入优先级组装证据，不修改输入对象。

    Returns:
        入选证据、模型上下文、排除原因。字符预算包含标题和分隔符。
        普通块整块取舍，超大块只摘取能完整容纳的段落并标注原文位置。
    """
    selected, blocks, omitted = [], [], []
    seen = set()
    used = 0
    for original in evidence:
        item = dict(original)
        item.setdefault("chunk_id", str(item.get("evidence_id", len(selected))))
        content = strip_front_matter(item["content"]).strip()
        signature = (content, item.get("scope"), item.get("document_version"))
        if signature in seen:
            omitted.append({"chunk_id": item["chunk_id"], "reason": "duplicate"})
            continue
        seen.add(signature)
        item["evidence_id"] = f"证据 {len(selected) + 1}"
        block = render_evidence_block(len(selected), item)
        available = max_chars - used - (2 if blocks else 0)
        if len(block) > available and len(block) <= max_chars:
            omitted.append({"chunk_id": item["chunk_id"], "reason": "context_budget"})
            continue
        if len(block) > available:
            paragraphs = list(re.finditer(r"[^\n]+", content))
            excerpts = []
            for paragraph in paragraphs:
                candidate = excerpts + [f"[字符 {paragraph.start()}:{paragraph.end()}] {paragraph.group()}"]
                trial = {**item, "content": "\n".join(candidate)}
                if len(render_evidence_block(len(selected), trial)) <= available:
                    excerpts = candidate
            if not excerpts:
                omitted.append({"chunk_id": item["chunk_id"], "reason": "context_budget"})
                continue
            item["content"] = "\n".join(excerpts)
            item["excerpted"] = True
            block = render_evidence_block(len(selected), item)
            omitted.append({"chunk_id": item["chunk_id"], "reason": "paragraph_excerpt"})
        selected.append(item)
        blocks.append(block)
        used += len(block) + (2 if len(blocks) > 1 else 0)
    return selected, "\n\n".join(blocks), omitted
