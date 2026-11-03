"""下架知识卡片（不删除，只是不再参与检索）。范围是当前产品线能用的全部知识库（含大健康通用库）。

用法（在服务器项目目录下）：
    按主题：docker-compose -p health-aigc exec -T api python -m scripts.deactivate_cards 主题1 主题2 ...
    按编号：docker-compose -p health-aigc exec -T api python -m scripts.deactivate_cards --ids 152 153
"""

import sys

from sqlalchemy import select

from app.db import SessionLocal, init_db
from app.models import KnowledgeCard
from app.products import load_product


def main() -> None:
    args = sys.argv[1:]
    if not args or args == ["--ids"]:
        print(__doc__)
        sys.exit(1)
    by_id = args[0] == "--ids"
    keys = [int(a) for a in args[1:]] if by_id else args
    init_db()
    product = load_product()
    with SessionLocal() as session:
        col = KnowledgeCard.id if by_id else KnowledgeCard.topic
        cards = list(session.scalars(select(KnowledgeCard).where(
            KnowledgeCard.product.in_(product.pools()), col.in_(keys), KnowledgeCard.active.is_(True))))
        for c in cards:
            c.active = False
        session.commit()
    found = {(c.id if by_id else c.topic) for c in cards}
    print(f"已下架 {len(cards)} 张：{'、'.join(f'{c.id} {c.topic}' for c in cards) or '无'}")
    missing = [k for k in keys if k not in found]
    if missing:
        print(f"未找到（或已下架）：{'、'.join(map(str, missing))}")


if __name__ == "__main__":
    main()
