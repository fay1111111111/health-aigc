"""知识库：知识卡片的入库、向量化和检索。"""

import math

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .llm import LLM
from .models import KnowledgeCard


class CardIn(BaseModel):
    topic: str = Field(min_length=1, max_length=200)
    points: str = Field(min_length=1)
    source: str = ""
    risk_level: str = Field("low", pattern="^(low|mid|high)$")
    tags: list[str] = Field(default_factory=list)


def card_text(topic: str, points: str) -> str:
    return f"{topic}\n{points}"


def add_cards(session: Session, product: str, cards: list[CardIn], llm: LLM, skip_existing: bool = True) -> list[KnowledgeCard]:
    if skip_existing:
        existing = set(session.scalars(
            select(KnowledgeCard.topic).where(KnowledgeCard.product == product, KnowledgeCard.active.is_(True))))
        cards = [c for c in cards if c.topic not in existing]
    if not cards:
        return []
    vectors = llm.embed([card_text(c.topic, c.points) for c in cards])
    rows = [KnowledgeCard(product=product, embedding=v, **c.model_dump()) for c, v in zip(cards, vectors)]
    session.add_all(rows)
    session.commit()
    return rows


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


RISK_ORDER = ["low", "mid", "high"]


def search(session: Session, product: str, query: str, llm: LLM, k: int = 5,
           max_risk: str = "high") -> list[KnowledgeCard]:
    allowed = RISK_ORDER[:RISK_ORDER.index(max_risk) + 1]
    base = select(KnowledgeCard).where(
        KnowledgeCard.product == product, KnowledgeCard.active.is_(True), KnowledgeCard.embedding.is_not(None),
        KnowledgeCard.risk_level.in_(allowed))
    qvec = llm.embed([query])[0]

    if session.get_bind().dialect.name == "postgresql":
        # 用 pgvector 的余弦距离在数据库里排序
        from pgvector.sqlalchemy import Vector
        from sqlalchemy import cast

        col = cast(KnowledgeCard.embedding, Vector(len(qvec)))
        return list(session.scalars(base.order_by(col.cosine_distance(qvec)).limit(k)))

    cards = list(session.scalars(base))
    cards.sort(key=lambda c: _cosine(c.embedding, qvec), reverse=True)
    return cards[:k]
