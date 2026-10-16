"""写用例使用 SQLAlchemy 原生事务和保存点。"""

from contextlib import contextmanager

from sqlalchemy.orm import SessionTransactionOrigin


@contextmanager
def transaction(db):
    session = db.session
    current = session.get_transaction()
    if current is not None and current.origin == SessionTransactionOrigin.AUTOBEGIN:
        if session.new or session.dirty or session.deleted:
            raise RuntimeError("unowned_transaction")
        # 结束先前查询快照，再开始独立写事务。
        session.rollback()
        current = None
    with session.begin_nested() if current is not None else session.begin():
        yield
