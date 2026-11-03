"""待审知识卡片审核表：导出和导回。

用法（在服务器项目目录 /opt/tcm-aigc 下）：
    导出：
      docker-compose -p health-aigc exec -T api python -m scripts.review_sheet export /tmp/review.xlsx
      docker cp health-aigc_api_1:/tmp/review.xlsx /root/知识卡片审核表.xlsx
    导回（填好之后）：
      docker cp /root/知识卡片审核表-已填.xlsx health-aigc_api_1:/tmp/review-done.xlsx
      docker-compose -p health-aigc exec -T api python -m scripts.review_sheet apply /tmp/review-done.xlsx

导出的是当前产品线能用的全部知识库（含大健康通用库）里所有待审卡片。
导回后：通过 → 参与生成；驳回 → 不再使用；修改后通过 → 用改后的要点并通过；空着 → 保持待审。
"""

import sys
from pathlib import Path

from sqlalchemy import select

from app import review_sheet
from app.db import SessionLocal, init_db
from app.llm import get_llm
from app.models import KnowledgeCard
from app.products import load_product


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] not in ("export", "apply"):
        print(__doc__)
        sys.exit(1)
    action, path = sys.argv[1], Path(sys.argv[2])
    init_db()
    pools = load_product().pools()
    with SessionLocal() as session:
        if action == "export":
            cards = list(session.scalars(select(KnowledgeCard).where(
                KnowledgeCard.product.in_(pools), KnowledgeCard.active.is_(True),
                KnowledgeCard.review_status == "pending").order_by(KnowledgeCard.domain, KnowledgeCard.id)))
            path.write_bytes(review_sheet.build(cards))
            print(f"已导出 {len(cards)} 张待审卡片到 {path}")
            return
        result = review_sheet.apply(session, path.read_bytes(), pools, get_llm())
    s = result["stats"]
    print(f"通过 {s['通过']} 张，驳回 {s['驳回']} 张，修改后通过 {s['修改后通过']} 张，未填写 {s['未填写']} 张（保持待审）")
    for p in result["problems"]:
        print("  ·", p)


if __name__ == "__main__":
    main()
