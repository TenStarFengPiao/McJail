"""仅初始化知识库与聊天表；已有业务表不删除、不再播种。"""

from sqlalchemy import inspect

from .models import Base


def initialize_schema(connection) -> None:
    """幂等创建最小 schema，并给旧知识文档增加原文字段。"""
    existing = inspect(connection)
    if existing.has_table("knowledge_documents"):
        columns = {column["name"] for column in existing.get_columns("knowledge_documents")}
        if "raw_text" not in columns:
            connection.exec_driver_sql("ALTER TABLE knowledge_documents ADD COLUMN raw_text TEXT NOT NULL DEFAULT ''")
        if "aliases_json" not in columns:
            connection.exec_driver_sql(
                "ALTER TABLE knowledge_documents ADD COLUMN aliases_json TEXT NOT NULL DEFAULT '[]'"
            )
    if existing.has_table("chat_turns"):
        columns = {column["name"] for column in existing.get_columns("chat_turns")}
        if "request_id" not in columns:
            connection.exec_driver_sql("ALTER TABLE chat_turns ADD COLUMN request_id TEXT")
    Base.metadata.create_all(connection)
    if existing.has_table("delivery_attempts"):
        columns = {column["name"] for column in inspect(connection).get_columns("delivery_attempts")}
        if "payload_json" not in columns:
            connection.exec_driver_sql("ALTER TABLE delivery_attempts ADD COLUMN payload_json TEXT")
    connection.exec_driver_sql("CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_request ON chat_turns(request_id)")
    connection.exec_driver_sql("INSERT OR IGNORE INTO schema_migrations(version) VALUES (2)")
    connection.exec_driver_sql("INSERT OR IGNORE INTO schema_migrations(version) VALUES (3)")
    connection.exec_driver_sql("INSERT OR IGNORE INTO schema_migrations(version) VALUES (4)")
    connection.exec_driver_sql("INSERT OR IGNORE INTO schema_migrations(version) VALUES (5)")
    connection.exec_driver_sql("INSERT OR IGNORE INTO schema_migrations(version) VALUES (6)")
    connection.exec_driver_sql("INSERT OR IGNORE INTO schema_migrations(version) VALUES (7)")
