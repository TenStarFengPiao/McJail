from __future__ import annotations

import re

from maidanlao.rag.models import KnowledgeChunk

from ._shared import _token_count, _tokens, annotate_chunk


class DocumentChunker:
    #: 每块正文前缀的上下文锚点长度上限（标题 + 条款号）。
    ANCHOR_HEAD_CHARS = 120

    def split_text(self, document_id: str, section_id: str, section_path: str, text: str) -> list[KnowledgeChunk]:
        units = [u for u in re.split(r"(?<=[。；;.!?？])|\n+", text) if u.strip()]
        chunks: list[KnowledgeChunk] = []
        current = ""
        for unit in units:
            if _token_count(current + unit) <= 500:
                current += unit.rstrip() + "\n"
            else:
                chunks.append(self._chunk(document_id, section_id, section_path, "text", current, len(chunks)))
                overlap = "".join(_tokens(current)[-80:])
                current = overlap + unit.rstrip() + "\n"
        if current.strip():
            if chunks and _token_count(current) < 120 and chunks[-1].chunk_token_count + _token_count(current) <= 650:
                chunks[-1].chunk_text += current
                chunks[-1].chunk_token_count = _token_count(chunks[-1].chunk_text)
            else:
                chunks.append(self._chunk(document_id, section_id, section_path, "text", current, len(chunks)))
        if len(chunks) == 1:
            chunks[0].next_chunk_id = None
        chunks = self._link(chunks)
        for chunk in chunks:
            annotate_chunk(chunk, self.ANCHOR_HEAD_CHARS)
        return chunks

    _ARTICLE_RE = re.compile(r"第[一二三四五六七八九十百零〇\d]+条")

    def split_table(
        self, document_id: str, section_id: str, section_path: str, rows: list[list[str]]
    ) -> list[KnowledgeChunk]:
        if len(rows) <= 20:
            return self._link(
                [self._chunk(document_id, section_id, section_path, "table", self._markdown_table(rows), 0)]
            )
        chunks: list[KnowledgeChunk] = []
        start = 0
        while start < len(rows):
            end = min(start + 15, len(rows))
            chunks.append(
                self._chunk(
                    document_id, section_id, section_path, "table", self._markdown_table(rows[start:end]), len(chunks)
                )
            )
            if end == len(rows):
                break
            start = end - 2
        return self._link(chunks)

    def _chunk(
        self, document_id: str, section_id: str, section_path: str, chunk_type: str, text: str, index: int
    ) -> KnowledgeChunk:
        return KnowledgeChunk(
            document_id,
            f"{document_id}_chunk_{index:03d}",
            index,
            chunk_type,
            text,
            _token_count(text),
            section_path,
            section_id,
            section_id,
        )

    def _link(self, chunks: list[KnowledgeChunk]) -> list[KnowledgeChunk]:
        for index, chunk in enumerate(chunks):
            chunk.chunk_index = index
            chunk.prev_chunk_id = chunks[index - 1].chunk_id if index > 0 else None
            chunk.next_chunk_id = chunks[index + 1].chunk_id if index < len(chunks) - 1 else None
        return chunks

    def _markdown_table(self, rows: list[list[str]]) -> str:
        if not rows:
            return ""
        header = "| " + " | ".join(rows[0]) + " |"
        sep = "| " + " | ".join("---" for _ in rows[0]) + " |"
        body = ["| " + " | ".join(row) + " |" for row in rows[1:]]
        return "\n".join([header, sep, *body])
