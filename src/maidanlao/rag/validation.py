"""批量校验来源关联、状态、日期和版本，不证明语义蕴含。"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from maidanlao.persistence.models import KnowledgeChunks, KnowledgeDocuments


def validate_sources(repos, evidence):
    valid, problems = [], []
    if not evidence:
        return valid, problems
    today = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    session = repos.conn.session
    documents = {
        doc.id: doc
        for doc in session.scalars(
            select(KnowledgeDocuments)
            .where(KnowledgeDocuments.id.in_({ev.get("document_id") for ev in evidence}))
            .execution_options(populate_existing=True)
        )
    }
    chunks = dict(
        session.execute(
            select(KnowledgeChunks.chunk_id, KnowledgeChunks.document_id).where(
                KnowledgeChunks.chunk_id.in_({ev.get("chunk_id") for ev in evidence})
            )
        ).all()
    )
    for item in evidence:
        doc = documents.get(item.get("document_id"))
        reason = ""
        if doc is None or chunks.get(item.get("chunk_id")) != item.get("document_id"):
            reason = "dangling_source"
        elif (
            doc.status != "active"
            or (doc.effective_start and doc.effective_start[:10] > today)
            or (doc.effective_end and doc.effective_end[:10] < today)
        ):
            reason = "stale_evidence"
        elif item.get("document_version") not in (None, "", doc.version):
            reason = "version_changed"
        if reason:
            problems.append({"chunk_id": item.get("chunk_id"), "reason": reason})
        else:
            valid.append(item)
    return valid, problems
