"""把 jsonl 格式的知识卡片导入当前产品线的知识库（同名主题会跳过）。

用法（在服务器项目目录下），可以给一个或多个文件，也可以给目录（导入目录里所有 .jsonl）：
    docker compose exec api python -m scripts.import_knowledge products/sleep/knowledge/cards/

每行一张卡片：
    {"topic": "主题", "points": "改写后的要点", "questions": ["用户可能的问法"], "use": "knowledge",
     "audience": "user", "domain": "领域", "risk_level": "low", "boundary": "使用边界", "source": "出处", "tags": []}

use：knowledge 知识 / companion 陪伴参考 / practice 小练习 / referral 转介 / background 只作背景，不进对话
audience：user 用户 / family 家属 / partner 伙伴（只给 B 端）
低风险卡片直接生效；中、高风险卡片进入待审核，老师在后台审核通过后才会用到。
加 --approve-all 则全部直接生效（只在老师已逐条审过时使用）。
"""

import json
import sys
from collections import Counter
from pathlib import Path

from app.db import SessionLocal, init_db
from app.knowledge import CardIn, add_cards
from app.llm import get_llm
from app.products import load_product


def read_cards(paths: list[Path]) -> list[CardIn]:
    files = []
    for p in paths:
        files += sorted(p.glob("*.jsonl")) if p.is_dir() else [p]
    cards = []
    for f in files:
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                cards.append(CardIn(**json.loads(line)))
            except Exception as e:  # noqa: BLE001
                raise SystemExit(f"{f.name} 第 {n} 行有问题：{e}") from e
    return cards


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(1)
    cards = read_cards([Path(a) for a in args])
    init_db()
    product = load_product()
    with SessionLocal() as session:
        status = "approved" if "--approve-all" in sys.argv else None
        rows = []
        for i in range(0, len(cards), 20):  # 分批向量化，避免单次请求太大
            rows += add_cards(session, product.id, cards[i:i + 20], get_llm(), review_status=status)
    by = Counter(r.review_status for r in rows)
    print(f"产品线 {product.id}：读取 {len(cards)} 张，新增 {len(rows)} 张"
          f"（直接生效 {by['approved']}，待审核 {by['pending']}），跳过 {len(cards) - len(rows)} 张（主题已存在）")


if __name__ == "__main__":
    main()
