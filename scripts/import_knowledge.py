"""把 jsonl 格式的知识卡片导入当前产品线的知识库（同名主题会跳过）。

用法（在服务器项目目录下）：
    docker compose exec api python -m scripts.import_knowledge products/sleep/knowledge/starter_cards.jsonl

每行一张卡片：
    {"topic": "主题", "points": "改写后的要点", "source": "出处", "risk_level": "low", "tags": ["标签"]}
"""

import json
import sys
from pathlib import Path

from app.db import SessionLocal, init_db
from app.knowledge import CardIn, add_cards
from app.llm import get_llm
from app.products import load_product


def main() -> None:
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    path = Path(sys.argv[1])
    cards = [CardIn(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    init_db()
    product = load_product()
    with SessionLocal() as session:
        rows = add_cards(session, product.id, cards, get_llm())
    print(f"产品线 {product.id}：读取 {len(cards)} 张，新增 {len(rows)} 张，跳过 {len(cards) - len(rows)} 张（主题已存在）")


if __name__ == "__main__":
    main()
