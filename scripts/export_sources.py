"""导出资料来源登记表（CSV），备案时说明语料来源用。

用法（在服务器项目目录下）：
    docker-compose -p health-aigc exec -T api python -m scripts.export_sources > sources-$(date +%Y%m%d).csv
"""

import csv
import sys

from sqlalchemy import func, select

from app.db import SessionLocal, init_db
from app.models import KnowledgeCard, Source


def main() -> None:
    init_db()
    w = csv.writer(sys.stdout)
    w.writerow(["序号", "资料名称", "发布机构", "来源类型", "使用依据", "领域", "知识库", "链接",
                "处理状态", "生成卡片数", "处理时间"])
    with SessionLocal() as session:
        counts = dict(session.execute(select(KnowledgeCard.source_id, func.count()).where(
            KnowledgeCard.active.is_(True), KnowledgeCard.review_status != "rejected").group_by(KnowledgeCard.source_id)).all())
        for s in session.scalars(select(Source).order_by(Source.id)):
            w.writerow([s.id, s.title, s.publisher, s.source_type, s.license_note, s.domain, s.pool, s.url,
                        s.status, counts.get(s.id, 0), s.fetched_at.strftime("%Y-%m-%d %H:%M") if s.fetched_at else ""])


if __name__ == "__main__":
    main()
