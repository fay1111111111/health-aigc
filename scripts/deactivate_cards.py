"""按主题下架知识卡片（不删除，只是不再参与检索）。

用法（在服务器项目目录下）：
    docker-compose exec -T api python -m scripts.deactivate_cards 主题1 主题2 ...
"""

import sys

from sqlalchemy import select

from app.db import SessionLocal, init_db
from app.models import KnowledgeCard
from app.products import load_product


def main() -> None:
    topics = sys.argv[1:]
    if not topics:
        print(__doc__)
        sys.exit(1)
    init_db()
    product = load_product()
    with SessionLocal() as session:
        cards = list(session.scalars(select(KnowledgeCard).where(
            KnowledgeCard.product == product.id, KnowledgeCard.topic.in_(topics), KnowledgeCard.active.is_(True))))
        for c in cards:
            c.active = False
        session.commit()
    found = {c.topic for c in cards}
    print(f"已下架 {len(cards)} 张：{'、'.join(sorted(found)) or '无'}")
    missing = [t for t in topics if t not in found]
    if missing:
        print(f"未找到（或已下架）：{'、'.join(missing)}")


if __name__ == "__main__":
    main()
