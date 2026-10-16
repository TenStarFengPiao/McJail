from __future__ import annotations

from dataclasses import dataclass

from ._shared import REQUIRED_METADATA, map_category_to_doc_type


@dataclass(frozen=True)
class NormalizedDocument:
    metadata: dict[str, str]
    markdown: str


class DocumentNormalizer:
    def normalize_text(self, raw_text: str, metadata: dict[str, str] | None = None) -> NormalizedDocument:
        provided = dict(metadata or {})
        body_metadata, body = self._extract_front_matter(raw_text)
        merged = {**body_metadata, **provided}
        if "category" in merged:
            mapped = map_category_to_doc_type(merged["category"])
            merged.setdefault("doc_type", mapped.doc_type)
            merged.setdefault("source_level", mapped.source_level)
            merged.setdefault("collection_name", mapped.collection_name)
        merged.setdefault("status", "active")
        merged.setdefault("version", "1")
        missing = sorted(REQUIRED_METADATA - set(merged))
        if missing:
            raise ValueError("metadata_missing:" + ",".join(missing))
        markdown = self._render_front_matter(merged) + "\n\n" + body.strip()
        return NormalizedDocument(merged, markdown)

    def _extract_front_matter(self, text: str) -> tuple[dict[str, str], str]:
        stripped = text.strip()
        if not stripped.startswith("---"):
            return {}, text
        end = stripped.find("\n---", 3)
        if end == -1:
            return {}, text
        raw_meta = stripped[3:end].strip()
        body = stripped[end + 4 :].strip()
        metadata = {}
        for line in raw_meta.splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                metadata[key.strip()] = value.strip().strip('"')
        return metadata, body

    def _render_front_matter(self, metadata: dict[str, str]) -> str:
        return "---\n" + "\n".join(f"{key}: {metadata[key]}" for key in sorted(metadata)) + "\n---"
