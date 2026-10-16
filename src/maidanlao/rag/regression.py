"""RAG 回归评测（把 2026-09-10 线上事故固化成用例）。

此前的 ``RagEvaluationService`` 只支持"一道题对应一个 chunk"的简单召回，
无法表达真实故障：**证据在库里，但排序把它压到了 top_k 之外**。

这里补两层：
1. ``evaluate_question`` —— 单题检查：正确文档是否命中、必需事实是否进了上下文；
2. 事故用例集 —— 把用户实际问过的话（含"七选二"这种与原文零字面重叠的说法）
   固化成可重复的断言，改检索前后都能跑出数字。

关键指标 ``answer_rank``：正确证据在重排结果里的名次。它是这轮改动唯一
真正要推动的数字 —— 名次必须落进 ``top_k`` 之内才算修好。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from maidanlao.rag import HybridRetriever

#: 用户真实问过的追问链（2026-09-10 私聊事故）。
#
# 用例的判据刻意**不依赖某一句话的字面**，而是"正确证据有没有进上下文"：
# 排序修好之后，多条相关证据会一起进来，具体哪条排第一并不稳定。
# ``must_not_appear`` 用来抓更严重的问题 —— 答错条款
# （线上把第十四条成人教育条件当成普通本科条件回答了）。
INCIDENT_CASES: tuple[dict[str, Any], ...] = (
    {
        "question": "湘南学院颁发学位证要满足几点",
        "intent": "graduation_requirement",
        "must_appear": ("住院医师", "全日制研究生"),
        "note": "用户实际问法；修复前要靠模型补全，现在完整七项应进证据",
    },
    {
        "question": "那七项有哪些东西",
        "intent": "graduation_requirement",
        "must_appear": ("学科竞赛", "住院医师", "研究生"),
        "note": "追问；修复前意图层熔断 + 绝对阈值把全部候选过滤掉，现在排第 1",
    },
    {
        "question": "七选二是从哪些里面7选2",
        "intent": "graduation_requirement",
        "must_appear": ("住院医师", "研究生"),
        "note": "与原文零字面重叠（原文是“满足下列条件之二者”）；必须命中第八条，不能拿第十四条成人教育条款顶替",
    },
    {
        "question": "学士学位授予条件中的七项条件是哪些",
        "intent": "graduation_requirement",
        "must_appear": ("学科竞赛", "住院医师", "研究生"),
        "note": "把指代补全后的规范化问法，用来对比原始口语问法",
    },
    {
        "question": "湘南学院怎么样才能拿到学位证",
        "intent": "graduation_requirement",
        "must_appear": ("平均成绩",),
        "note": "用户实际问法；问的是普通本科，不能答成人教育条款",
    },
    {
        "question": "湘南学院毕业条件有哪些",
        "intent": "graduation_requirement",
        "must_appear": ("学士学位",),
        "expect_insufficient": True,
        "note": "知识库只有学位文件、没有毕业条件文件。诚实做法是说明只有学位条件"
        "（并提示缺少毕业条件文件），而不是拿成人教育条款顶替普通本科。",
    },
)


@dataclass
class QuestionOutcome:
    question: str
    status: str
    top_score: float
    evidence_count: int
    answer_rank: int | None
    missing_facts: list[str] = field(default_factory=list)
    forbidden_hits: list[str] = field(default_factory=list)
    used_articles: list[str] = field(default_factory=list)
    matched_by_title: bool = False
    note: str = ""

    @property
    def ok(self) -> bool:
        if self.forbidden_hits:
            return False
        if self.status != "ok":
            return False
        return not self.missing_facts

    def as_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "ok": self.ok,
            "status": self.status,
            "top_score": self.top_score,
            "evidence_count": self.evidence_count,
            "answer_rank": self.answer_rank,
            "missing_facts": self.missing_facts,
            "forbidden_hits": self.forbidden_hits,
            "used_articles": self.used_articles,
            "note": self.note,
        }


class AnswerEvidenceLocator:
    """判断某条证据是不是"含答案的那一块"。

    只用字面特征定位：``must_appear`` 全部出现的证据即视为正确证据。
    这样用例与具体 chunk_id 解耦 —— 重新入库换了 chunk 编号也不会失效。
    """

    def __init__(self, must_appear: tuple[str, ...]) -> None:
        self.must_appear = must_appear

    def is_answer(self, text: str) -> bool:
        if not self.must_appear:
            return False
        return all(fact in text for fact in self.must_appear)


class RagRegressionService:
    def __init__(
        self, repos, retriever: HybridRetriever | None = None, cases: tuple[dict[str, Any], ...] | None = None
    ) -> None:
        self.repos = repos
        self.retriever = retriever or HybridRetriever(repos)
        self.cases = cases or INCIDENT_CASES

    def run(self) -> dict[str, Any]:
        outcomes: list[QuestionOutcome] = []
        for case in self.cases:
            outcomes.append(self.evaluate_question(case))
        passed = [o for o in outcomes if o.ok]
        # 排序质量指标：只有"本该答出来"的题才计入 rank。
        ranked = [o.answer_rank for o in outcomes if o.answer_rank is not None]
        return {
            "case_count": len(outcomes),
            "passed": len(passed),
            "failed": [o.as_dict() for o in outcomes if not o.ok],
            "avg_answer_rank": round(sum(ranked) / len(ranked), 3) if ranked else None,
            "worst_answer_rank": max(ranked) if ranked else None,
            "all_passed": len(passed) == len(outcomes),
            "details": [o.as_dict() for o in outcomes],
        }

    def evaluate_question(self, case: dict[str, Any]) -> QuestionOutcome:
        result = self.retriever.search(case["question"])
        must_appear = tuple(case.get("must_appear") or ())
        forbidden = tuple(case.get("must_not_appear") or case.get("forbidden") or ())
        articles = tuple(case.get("expected_articles") or ())

        locator = AnswerEvidenceLocator(must_appear)
        answer_rank = None
        if must_appear:
            for position, evidence in enumerate(result.evidence_chunks, start=1):
                if locator.is_answer(evidence.get("content") or ""):
                    answer_rank = position
                    break

        missing = [fact for fact in must_appear if fact not in result.final_context]
        forbidden_hits = [term for term in forbidden if term in result.final_context]
        # 条款归属校验：答案用的条款必须落在预期集合里（治"拿成人教育条款答普通本科"）。
        used_articles = sorted(
            {article for evidence in result.evidence_chunks for article in (evidence.get("article") or [])}
        )
        if articles:
            wrong_articles = sorted(set(used_articles) - set(articles))
        else:
            wrong_articles = []

        return QuestionOutcome(
            question=case["question"],
            status=result.status,
            top_score=result.top_score,
            evidence_count=len(result.evidence_chunks),
            answer_rank=answer_rank,
            missing_facts=missing,
            forbidden_hits=forbidden_hits + [f"条款:{a}" for a in wrong_articles],
            used_articles=used_articles,
            note=str(case.get("note") or ""),
        )


__all__ = [
    "INCIDENT_CASES",
    "AnswerEvidenceLocator",
    "QuestionOutcome",
    "RagRegressionService",
]
