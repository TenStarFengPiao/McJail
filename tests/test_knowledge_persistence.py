"""知识库原子性、模型索引契约及无损迁移验证。"""

import sqlite3
from contextlib import closing
from unittest.mock import Mock

import pytest
from sqlalchemy import inspect

from maidanlao.persistence.database import initialize_database, open_database
from maidanlao.persistence.models import Base
from maidanlao.persistence.repository import KnowledgeRepository
from maidanlao.persistence.transactions import transaction
from maidanlao.rag import HybridRetriever, KnowledgeIngestService, LocalHashEmbeddingClient
from maidanlao.rag.validation import validate_sources


@pytest.fixture
def repos():
    with closing(open_database(":memory:")) as db:
        yield KnowledgeRepository(db)


def ingest(repos, raw_text="补考报名截止周五。", **kwargs):
    fields = dict(
        title="补考通知",
        source="教学办",
        publish_date="2026-09-10",
        scope="school",
        category="补考通知",
        raw_text=raw_text,
        created_by=0,
    )
    fields.update(kwargs)
    return KnowledgeIngestService(repos, LocalHashEmbeddingClient()).ingest_text(**fields)


def test_new_database_has_only_necessary_tables(repos):
    assert set(inspect(repos.conn.engine).get_table_names()) == set(Base.metadata.tables)
    assert len(Base.metadata.tables) == 16
    assert not repos.knowledge_documents.list_all()
    assert not repos.rag_test_cases.list_all()


def test_legacy_upgrade_preserves_business_checkpoint_and_indexes(tmp_path):
    path = str(tmp_path / "legacy.db")
    with closing(open_database(path)) as db:
        repo = KnowledgeRepository(db)
        doc_id = ingest(repo)
        vectors = db.session.connection().exec_driver_sql("SELECT * FROM knowledge_vector_indexes").fetchall()
    with closing(sqlite3.connect(path)) as old, old:
        old.execute("ALTER TABLE knowledge_documents DROP COLUMN raw_text")
        old.execute("DROP TABLE chat_turns")
        old.execute("CREATE TABLE student_accounts (secret TEXT)")
        old.execute("INSERT INTO student_accounts VALUES ('keep-business')")
        old.execute("CREATE TABLE checkpoints (value TEXT)")
        old.execute("INSERT INTO checkpoints VALUES ('keep-checkpoint')")
    initialize_database(path, force=True)
    with closing(open_database(path)) as upgraded:
        repo = KnowledgeRepository(upgraded)
        assert repo.knowledge_documents.get(doc_id).raw_text == ""
        assert dict(
            upgraded.session.connection().exec_driver_sql("SELECT * FROM knowledge_vector_indexes").fetchone()._mapping
        ) == dict(vectors[0]._mapping)
        assert (
            upgraded.session.connection().exec_driver_sql("SELECT secret FROM student_accounts").fetchone()[0]
            == "keep-business"
        )
        assert (
            upgraded.session.connection().exec_driver_sql("SELECT value FROM checkpoints").fetchone()[0]
            == "keep-checkpoint"
        )
        assert not repo.chat.recent("any")
        assert HybridRetriever(repo, LocalHashEmbeddingClient()).search("补考报名").status == "ok"


def test_raw_text_reindex_preserves_id_increments_version_and_rebuilds(repos):
    raw = "# 补考通知\n\n补考报名截止周五。"
    doc_id = ingest(repos, raw)
    service = KnowledgeIngestService(repos, LocalHashEmbeddingClient())
    assert service.reingest_document(doc_id) == doc_id
    doc = repos.knowledge_documents.get(doc_id)
    assert doc.raw_text == raw and doc.version == 2
    assert repos.knowledge_vectors.count() == 1
    assert "raw_text" not in repos.knowledge_documents.list_all()[0]
    assert repos.knowledge_documents.is_active(doc_id)


@pytest.mark.parametrize("invalid", [RuntimeError("cloud unavailable"), [], [[]], [[float("nan")]]])
def test_import_embedding_failure_is_atomic(repos, invalid):
    broken = Mock(model_name="cloud")
    if isinstance(invalid, Exception):
        broken.embed.side_effect = invalid
    else:
        broken.embed.return_value = invalid
    with pytest.raises((RuntimeError, ValueError)):
        KnowledgeIngestService(repos, broken).ingest_text(
            "新通知", "教学办", "2026-09-10", "school", "补考通知", "新政策。", 0
        )
    assert repos.knowledge_documents.list_all() == []
    assert repos.knowledge_vectors.count() == 0
    assert repos.knowledge_chunks.all_with_documents() == []


def test_reindex_failure_keeps_old_index_and_external_foreign_keys(repos):
    doc_id = ingest(repos)
    repos.conn.session.connection().exec_driver_sql(
        "CREATE TABLE legacy_assets (document_id TEXT REFERENCES knowledge_documents(id))"
    )
    repos.conn.session.connection().exec_driver_sql("INSERT INTO legacy_assets VALUES (?)", (doc_id,))
    repos.conn.session.commit()
    old_vectors = [
        dict(row._mapping)
        for row in repos.conn.session.connection().exec_driver_sql("SELECT * FROM knowledge_vector_indexes")
    ]
    broken = Mock(model_name="new-cloud")
    broken.embed.side_effect = RuntimeError("offline")
    with pytest.raises(RuntimeError):
        KnowledgeIngestService(repos, broken).reingest_document(doc_id)
    assert repos.knowledge_documents.get(doc_id).version == 1
    assert [
        dict(row._mapping)
        for row in repos.conn.session.connection().exec_driver_sql("SELECT * FROM knowledge_vector_indexes")
    ] == old_vectors
    assert (
        repos.conn.session.connection().exec_driver_sql("SELECT document_id FROM legacy_assets").fetchone()[0] == doc_id
    )
    KnowledgeIngestService(repos, LocalHashEmbeddingClient()).reingest_document(doc_id)
    assert repos.knowledge_documents.get(doc_id).version == 2


def test_legacy_rebuild_from_chunks_and_source_file(repos, tmp_path):
    doc_id = ingest(repos)
    repos.conn.session.connection().exec_driver_sql(
        "UPDATE knowledge_documents SET raw_text = '', file_path = '', normalized_file_path = '' WHERE id = ?",
        (doc_id,),
    )
    repos.conn.session.commit()
    KnowledgeIngestService(repos, LocalHashEmbeddingClient()).reingest_document(doc_id)
    assert "补考报名" in repos.knowledge_documents.get(doc_id).raw_text
    path = tmp_path / "source.md"
    path.write_text("补考报名改为周六。", encoding="utf-8")
    repos.conn.session.connection().exec_driver_sql(
        "UPDATE knowledge_documents SET raw_text = '', normalized_file_path = ? WHERE id = ?", (str(path), doc_id)
    )
    repos.conn.session.commit()
    KnowledgeIngestService(repos, LocalHashEmbeddingClient()).reingest_document(doc_id)
    assert "周六" in repos.knowledge_documents.get(doc_id).raw_text
    with pytest.raises(ValueError, match="document_not_found"):
        KnowledgeIngestService(repos, LocalHashEmbeddingClient()).reingest_document("missing")


def test_embedding_model_name_dimension_and_cloud_failure(repos):
    doc_id = ingest(repos)
    row = repos.knowledge_chunks.all_with_documents()[0]
    repos.knowledge_vectors.upsert(row["chunk_id"], row["collection_name"], "new-model", "hash", [1.0, 0.0])
    query = Mock(model_name="new-model")
    query.embed.return_value = [[1.0, 0.0]]
    retriever = HybridRetriever(repos, query)
    retriever.direct_context = False
    result = retriever.search("补考报名")
    assert result.diagnostics["vector_count"] == 1
    query.embed.return_value = [[1.0, 0.0, 0.0]]
    assert retriever.search("补考报名").diagnostics["vector_count"] == 0
    query.model_name = "other"
    query.embed.return_value = [[1.0, 0.0]]
    assert retriever.search("补考报名").diagnostics["vector_count"] == 0
    query.embed.side_effect = RuntimeError("cloud unavailable")
    errors = retriever.search("补考报名").diagnostics["channel_errors"]
    assert errors == [{"channel": "vector", "error": "RuntimeError"}]
    assert repos.knowledge_documents.get(doc_id).version == 1


@pytest.mark.parametrize(
    "update,reason",
    [
        ("status = 'archived'", "stale_evidence"),
        ("effective_start = '2999-01-01'", "stale_evidence"),
        ("effective_end = '2000-01-01'", "stale_evidence"),
        ("version = 2", "version_changed"),
    ],
)
def test_sources_reject_inactive_expired_and_changed_versions(repos, update, reason):
    doc_id = ingest(repos)
    result = HybridRetriever(repos, LocalHashEmbeddingClient()).search("报名")
    repos.conn.session.connection().exec_driver_sql(f"UPDATE knowledge_documents SET {update} WHERE id = ?", (doc_id,))
    repos.conn.session.commit()
    valid, problems = validate_sources(repos, result.evidence_chunks)
    assert not valid and problems[0]["reason"] == reason


def test_dangling_source_is_rejected(repos):
    valid, errors = validate_sources(repos, [{"document_id": "missing", "chunk_id": "missing"}])
    assert not valid and errors[0]["reason"] == "dangling_source"


def test_nested_transaction_rolls_back_only_inner_and_rejects_unowned_write(repos):
    db = repos.conn
    with transaction(db):
        db.session.connection().exec_driver_sql("CREATE TABLE example (value TEXT)")
        db.session.connection().exec_driver_sql("INSERT INTO example VALUES ('outer')")
        with pytest.raises(RuntimeError):
            with transaction(db):
                db.session.connection().exec_driver_sql("INSERT INTO example VALUES ('inner')")
                raise RuntimeError()
    assert [r[0] for r in db.session.connection().exec_driver_sql("SELECT * FROM example")] == ["outer"]
    from maidanlao.persistence.models import ChatTurn

    db.session.add(ChatTurn(conversation_key="test", question="unowned", answer="", sources_json="[]"))
    with pytest.raises(RuntimeError, match="unowned_transaction"):
        with transaction(db):
            pass
    db.session.rollback()
    assert db.session.connection().exec_driver_sql("SELECT COUNT(*) FROM example").fetchone()[0] == 1


def test_sqlalchemy_session_contract_and_urls(tmp_path):
    with closing(open_database("sqlite:///" + str(tmp_path / "db.sqlite"))) as db:
        db.session.connection().exec_driver_sql("CREATE TABLE numbers (value INTEGER)")
        result = db.session.connection().exec_driver_sql("INSERT INTO numbers VALUES (?)", [(1,), (2,)])
        assert result.rowcount == 2
        db.session.commit()
        rows = db.session.connection().exec_driver_sql("SELECT value FROM numbers").fetchall()
        assert len(rows[0]) == 1 and dict(rows[0]._mapping) == {"value": 1}
        assert db.session.connection().exec_driver_sql("SELECT 1 WHERE 0").fetchone() is None
        assert db.session.connection().exec_driver_sql("INSERT INTO numbers VALUES (3)").lastrowid
        db.session.rollback()
        db.close()
    with pytest.raises(RuntimeError, match="requires_adapter"):
        open_database("postgresql://user:secret@example.com/db")
