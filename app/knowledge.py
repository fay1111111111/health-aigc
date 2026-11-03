"""知识库：知识卡片的入库、向量化和检索。

知识库分两层：
- 产品线自己的库（product = 产品线 id，如 sleep）
- 大健康通用库（product = common），所有产品线共享
生成时按产品线配置的 knowledge_pools 一起检索，只用审核通过的卡片。
"""

import math

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .llm import LLM
from .models import KnowledgeCard

COMMON_POOL = "common"
RISK_ORDER = ["low", "mid", "high"]


class CardIn(BaseModel):
    topic: str = Field(min_length=1, max_length=200)
    points: str = Field(min_length=1)
    source: str = ""
    risk_level: str = Field("low", pattern="^(low|mid|high)$")
    domain: str = ""
    tags: list[str] = Field(default_factory=list)


def card_text(topic: str, points: str) -> str:
    return f"{topic}\n{points}"


def existing_topics(session: Session, pool: str) -> set[str]:
    return set(session.scalars(select(KnowledgeCard.topic).where(
        KnowledgeCard.product == pool, KnowledgeCard.active.is_(True),
        KnowledgeCard.review_status != "rejected")))


def add_cards(session: Session, pool: str, cards: list[CardIn], llm: LLM, skip_existing: bool = True,
              review_status: str | None = "approved", origin: str = "manual", source_id: int | None = None,
              vectors: list[list[float]] | None = None) -> list[KnowledgeCard]:
    """入库。review_status=None 时按风险等级决定：低风险直接通过，中高风险待人工审核。"""
    if skip_existing:
        seen = existing_topics(session, pool)
        keep = [i for i, c in enumerate(cards) if c.topic not in seen]
        cards = [cards[i] for i in keep]
        if vectors is not None:
            vectors = [vectors[i] for i in keep]
    if not cards:
        return []
    if vectors is None:
        vectors = llm.embed([card_text(c.topic, c.points) for c in cards])
    rows = [
        KnowledgeCard(product=pool, embedding=v, origin=origin, source_id=source_id,
                      review_status=review_status or ("approved" if c.risk_level == "low" else "pending"),
                      **c.model_dump())
        for c, v in zip(cards, vectors)
    ]
    session.add_all(rows)
    session.commit()
    return rows


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def search(session: Session, pools: list[str] | str, query: str, llm: LLM, k: int = 5,
           max_risk: str = "high", include_pending: bool = False, qvec: list[float] | None = None) -> list[KnowledgeCard]:
    pools = [pools] if isinstance(pools, str) else pools
    allowed = RISK_ORDER[:RISK_ORDER.index(max_risk) + 1]
    statuses = ["approved", "pending"] if include_pending else ["approved"]
    base = select(KnowledgeCard).where(
        KnowledgeCard.product.in_(pools), KnowledgeCard.active.is_(True), KnowledgeCard.embedding.is_not(None),
        KnowledgeCard.risk_level.in_(allowed), KnowledgeCard.review_status.in_(statuses))
    if qvec is None:
        qvec = llm.embed([query])[0]

    if session.get_bind().dialect.name == "postgresql":
        # 用 pgvector 的余弦距离在数据库里排序
        from pgvector.sqlalchemy import Vector
        from sqlalchemy import cast

        col = cast(KnowledgeCard.embedding, Vector(len(qvec)))
        return list(session.scalars(base.order_by(col.cosine_distance(qvec)).limit(k)))

    cards = list(session.scalars(base))
    cards.sort(key=lambda c: cosine(c.embedding, qvec), reverse=True)
    return cards[:k]


def most_similar(session: Session, pools: list[str], vec: list[float], llm: LLM) -> tuple[KnowledgeCard | None, float]:
    """找出库里和这条向量最像的卡片（去重用），返回卡片和相似度。"""
    hits = search(session, pools, "", llm, k=1, include_pending=True, qvec=vec)
    if not hits:
        return None, 0.0
    return hits[0], cosine(hits[0].embedding, vec)
