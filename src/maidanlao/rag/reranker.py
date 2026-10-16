"""纯函数排序：RRF 通道融合，不访问数据库。"""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def rrf_merge(ranked_lists: list[list[dict[str, Any]]], limit: int) -> list[dict[str, Any]]:
    """融合通道名次，保留通道分数及排名用于诊断。"""
    by_chunk: dict[str, dict[str, Any]] = {}
    scores: defaultdict[str, float] = defaultdict(float)
    for channel_index, ranked in enumerate(ranked_lists):
        channel = ("vector", "bm25")[channel_index] if channel_index < 2 else f"channel_{channel_index}"
        for rank, item in enumerate(ranked, start=1):
            candidate = by_chunk.setdefault(
                item["chunk_id"], {**item, "channel_ranks": {}, "channel_count": len(ranked_lists)}
            )
            candidate[f"{channel}_score"] = item["score"]
            candidate["channel_ranks"][channel] = rank
            scores[item["chunk_id"]] += 1 / (60 + rank)
    merged = [{**by_chunk[chunk_id], "rrf_score": score} for chunk_id, score in scores.items()]
    merged.sort(key=lambda item: (-item["rrf_score"], item["chunk_id"]))
    return merged[:limit]
