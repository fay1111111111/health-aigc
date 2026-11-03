from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .config import get_settings
from .db import Base, Embedding


def now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16))  # operator / reviewer / admin
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Source(Base):
    """资料来源登记：每条自动抓取的资料从哪来、什么性质，备案时用于说明语料来源。"""

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(String(1000), unique=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    publisher: Mapped[str] = mapped_column(String(200), default="")  # 发布机构
    source_type: Mapped[str] = mapped_column(String(32), default="")  # gov / association / classic / international / paper
    license_note: Mapped[str] = mapped_column(String(300), default="")  # 使用依据，如“政府公开科普”“公有领域古籍”
    domain: Mapped[str] = mapped_column(String(32), default="")  # 领域，如 睡眠、营养
    pool: Mapped[str] = mapped_column(String(32), default="common")  # 入哪个知识库
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending / done / failed
    error: Mapped[str] = mapped_column(Text, default="")
    card_count: Mapped[int] = mapped_column(Integer, default=0)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class KnowledgeCard(Base):
    """知识卡片：公开资料改写后的要点，生成时作为事实依据检索。

    product 字段是知识库名：产品线自己的库（如 sleep）或所有产品线共享的大健康通用库 common。
    """

    __tablename__ = "knowledge_cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product: Mapped[str] = mapped_column(String(32), index=True)
    domain: Mapped[str] = mapped_column(String(32), default="")
    topic: Mapped[str] = mapped_column(String(200))
    points: Mapped[str] = mapped_column(Text)  # 改写后的要点
    source: Mapped[str] = mapped_column(String(500), default="")  # 出处
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"), nullable=True)
    risk_level: Mapped[str] = mapped_column(String(8), default="low")  # low / mid / high
    tags: Mapped[list] = mapped_column(JSON, default=list)
    embedding: Mapped[list | None] = mapped_column(Embedding(get_settings().embedding_dim), nullable=True)
    origin: Mapped[str] = mapped_column(String(16), default="manual")  # manual 人工整理 / auto 自动提取
    review_status: Mapped[str] = mapped_column(String(16), default="approved", index=True)  # approved / pending / rejected
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Generation(Base):
    """一次内容生成及其审核记录。"""

    __tablename__ = "generations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product: Mapped[str] = mapped_column(String(32), index=True)
    channel: Mapped[str] = mapped_column(String(32))
    audience: Mapped[str] = mapped_column(String(32))
    content_type: Mapped[str] = mapped_column(String(32))
    brief: Mapped[str] = mapped_column(Text, default="")  # 用户补充的要点

    output: Mapped[str] = mapped_column(Text)  # 模型生成的原稿
    edited_output: Mapped[str | None] = mapped_column(Text, nullable=True)  # 人工修改后的稿
    compliance: Mapped[dict] = mapped_column(JSON, default=dict)  # 最近一次检测结果
    card_ids: Mapped[list] = mapped_column(JSON, default=list)  # 本次参考的知识卡片
    model_used: Mapped[str] = mapped_column(String(64), default="")
    rewrites: Mapped[int] = mapped_column(Integer, default=0)

    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending / approved / rejected
    review_note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def final_text(self) -> str:
        return self.edited_output if self.edited_output is not None else self.output
