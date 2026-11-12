from sqlalchemy import JSON, create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator

from .config import get_settings


class Base(DeclarativeBase):
    pass


class Embedding(TypeDecorator):
    """PostgreSQL 上用 pgvector 列；其他数据库（测试用的 SQLite）存成 JSON 数组。"""

    impl = JSON
    cache_ok = True

    def __init__(self, dim: int):
        super().__init__()
        self.dim = dim

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from pgvector.sqlalchemy import Vector

            return dialect.type_descriptor(Vector(self.dim))
        return dialect.type_descriptor(JSON())

    def process_result_value(self, value, dialect):
        return None if value is None else [float(x) for x in value]


def make_engine(url: str):
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    return create_engine(url, pool_pre_ping=True)


engine = make_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


# 已部署的数据库升级：给旧表补上后来新增的列（只加不删，可重复执行）
_UPGRADES = [
    "ALTER TABLE knowledge_cards ADD COLUMN IF NOT EXISTS domain VARCHAR(32) DEFAULT ''",
    "ALTER TABLE knowledge_cards ADD COLUMN IF NOT EXISTS source_id INTEGER REFERENCES sources(id)",
    "ALTER TABLE knowledge_cards ADD COLUMN IF NOT EXISTS origin VARCHAR(16) DEFAULT 'manual'",
    "ALTER TABLE knowledge_cards ADD COLUMN IF NOT EXISTS review_status VARCHAR(16) DEFAULT 'approved'",
    "CREATE INDEX IF NOT EXISTS ix_knowledge_cards_review_status ON knowledge_cards (review_status)",
    "ALTER TABLE knowledge_cards ADD COLUMN IF NOT EXISTS questions JSON DEFAULT '[]'",
    "ALTER TABLE knowledge_cards ADD COLUMN IF NOT EXISTS use VARCHAR(16) DEFAULT 'knowledge'",
    "ALTER TABLE knowledge_cards ADD COLUMN IF NOT EXISTS audience VARCHAR(16) DEFAULT 'user'",
    "ALTER TABLE knowledge_cards ADD COLUMN IF NOT EXISTS boundary TEXT DEFAULT ''",
    "CREATE INDEX IF NOT EXISTS ix_knowledge_cards_use ON knowledge_cards (use)",
]


def init_db(bind=None) -> None:
    from . import client_models, models  # noqa: F401  注册所有表

    bind = bind or engine
    if bind.dialect.name == "postgresql":
        with bind.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(bind)
    if bind.dialect.name == "postgresql":
        with bind.begin() as conn:
            for sql in _UPGRADES:
                conn.execute(text(sql))


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
