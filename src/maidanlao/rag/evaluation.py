from __future__ import annotations

from typing import Any

from maidanlao.persistence.repository import KnowledgeRepository

from ._shared import SENSITIVE_TERMS
from .retriever import HybridRetriever


class RagEvaluationService:
    def __init__(self, repos: KnowledgeRepository, retriever: HybridRetriever | None = None):
        self.repos = repos
        self.retriever = retriever or HybridRetriever(repos)

    def run(self) -> dict[str, Any]:
        cases = self.repos.rag_test_cases.list_all()
        if not cases:
            return {
                "case_count": 0,
                "recall_at_5": 0.0,
                "mrr_at_8": 0.0,
                "leakage_rate": 0.0,
                "passed": False,
                "failures": ["rag_test_cases_empty"],
            }
        recall_hits = 0
        reciprocal_sum = 0.0
        leakage_hits = 0
        failures = []
        for case in cases:
            result = self.retriever.search(case["question"])
            chunk_ids = [chunk["chunk_id"] for chunk in result.evidence_chunks]
            if case["expected_chunk_id"] in chunk_ids[:5]:
                recall_hits += 1
            else:
                failures.append(f"miss_recall@5:case_{case['id']}")
            if case["expected_chunk_id"] in chunk_ids[:8]:
                reciprocal_sum += 1 / (chunk_ids.index(case["expected_chunk_id"]) + 1)
            if case["required_fact"] and case["required_fact"] not in result.final_context:
                failures.append(f"missing_required_fact:case_{case['id']}")
            if any(term in result.final_context.lower() for term in SENSITIVE_TERMS):
                leakage_hits += 1
                failures.append(f"sensitive_leakage:case_{case['id']}")
        case_count = len(cases)
        recall = recall_hits / case_count
        mrr = reciprocal_sum / case_count
        leakage = leakage_hits / case_count
        return {
            "case_count": case_count,
            "recall_at_5": round(recall, 4),
            "mrr_at_8": round(mrr, 4),
            "leakage_rate": round(leakage, 4),
            "passed": recall >= 0.90 and mrr >= 0.75 and leakage == 0,
            "failures": failures,
        }
