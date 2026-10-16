"""复用文件库 Engine；每个请求拥有独立 SQLAlchemy Session。"""

import threading
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import create_engine, event
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool, StaticPool

from .migrations import initialize_schema

_ENGINES = {}
_ENGINE_LOCK = threading.Lock()


def _create_engine(path):
    engine = create_engine(
        URL.create("sqlite+pysqlite", database=path),
        connect_args={"timeout": 30, "check_same_thread": False},
        poolclass=StaticPool if path == ":memory:" else NullPool,
        hide_parameters=True,
    )

    @event.listens_for(engine, "connect")
    def configure(connection, _):
        connection.isolation_level = None
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA busy_timeout = 30000")
        if path != ":memory:":
            cursor.execute("PRAGMA journal_mode = WAL")
            cursor.execute("PRAGMA synchronous = NORMAL")
        cursor.close()

    @event.listens_for(engine, "begin")
    def begin(connection):
        # QQ 与后台可同时启动；迁移先拿写锁，避免两个进程同时建表。
        connection.exec_driver_sql(
            "BEGIN IMMEDIATE"
            if connection.get_execution_options().get("schema_init")
            or connection.get_execution_options().get("write_lock")
            else "BEGIN"
        )

    try:
        with engine.connect().execution_options(schema_init=True) as connection, connection.begin():
            initialize_schema(connection)
    except BaseException:
        engine.dispose()
        raise
    return engine


def initialize_database(path, *, force=False):
    """文件库每个进程初始化一次；force 用于显式升级已有数据库。"""
    path = str(Path(path).resolve())
    with _ENGINE_LOCK:
        engine = _ENGINES.get(path)
        if engine is None:
            engine = _create_engine(path)
            if len(_ENGINES) >= 32:
                _ENGINES.pop(next(iter(_ENGINES))).dispose()
            _ENGINES[path] = engine
        elif force:
            with engine.connect().execution_options(schema_init=True) as connection, connection.begin():
                initialize_schema(connection)
        return engine


class Database:
    """工作单元仅管理 Session；内存库另外拥有独立 Engine。"""

    def __init__(self, path):
        self.path = path
        self.engine = _create_engine(path) if path == ":memory:" else initialize_database(path)
        self.session = Session(self.engine, expire_on_commit=True, close_resets_only=False)

    def close(self):
        self.session.close()
        if self.path == ":memory:":
            self.engine.dispose()


def open_database(path):
    path = str(path)
    if path.startswith("sqlite:///"):
        path = path.removeprefix("sqlite:///")
    if path.startswith(("postgres://", "postgresql://")):
        parsed = urlparse(path)
        raise RuntimeError(f"postgresql_runtime_requires_adapter:{parsed.hostname or 'unknown-host'}")
    if path != ":memory:":
        path = str(Path(path).resolve())
    return Database(path)
