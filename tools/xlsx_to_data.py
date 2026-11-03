"""把「数据收集表」Excel 转成系统能导入的文件（在电脑上运行，需要 pip install openpyxl）。

用法：
    python tools/xlsx_to_data.py 数据收集表.xlsx sleep v1

输出：
    products/<产品线>/knowledge/cards_<版本>.jsonl   知识卡片，服务器上用 scripts.import_knowledge 导入
    products/<产品线>/examples.yaml                   风格样例（追加，按正文去重）
"""

import json
import sys
from pathlib import Path

import openpyxl
import yaml

ROOT = Path(__file__).resolve().parent.parent
RISK = {"低": "low", "中": "mid", "高": "high"}
CHANNEL = {"小红书": "xiaohongshu", "大众点评/美团团购": "dianping", "抖音/视频号口播": "douyin_script",
           "线下物料": "offline_print", "朋友圈/私域": "wechat_moments"}
CTYPE = {"种草笔记": "seeding", "团购详情": "group_buy", "门店活动文案": "store_event",
         "海报/传单文案": "poster", "招商文案": "franchise", "培训话术": "training"}


def cell(v) -> str:
    return "" if v is None else str(v).strip()


def rows(ws):
    # 第 1 行表头，第 2 行是示例，从第 3 行开始
    for r in ws.iter_rows(min_row=3, values_only=True):
        if any(cell(v) for v in r):
            yield [cell(v) for v in r]


def main() -> None:
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)
    wb = openpyxl.load_workbook(sys.argv[1], read_only=True)
    product, version = sys.argv[2], sys.argv[3]
    pdir = ROOT / "products" / product

    cards, problems = [], []
    for i, r in enumerate(rows(wb["知识卡片"]), start=3):
        topic, points, source, risk, tags = (r + [""] * 5)[:5]
        if not topic or not points:
            problems.append(f"知识卡片第 {i} 行缺少主题或要点，已跳过")
            continue
        cards.append({"topic": topic, "points": points, "source": source,
                      "risk_level": RISK.get(risk, "mid"),
                      "tags": [t.strip() for t in tags.replace(",", "、").split("、") if t.strip()]})
    out = pdir / "knowledge" / f"cards_{version}.jsonl"
    out.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cards), encoding="utf-8")

    ex_path = pdir / "examples.yaml"
    examples = yaml.safe_load(ex_path.read_text(encoding="utf-8")) or [] if ex_path.exists() else []
    seen = {e["text"] for e in examples}
    added = 0
    for i, r in enumerate(rows(wb["风格样例"]), start=3):
        channel, ctype, _, text = (r + [""] * 4)[:4]
        if not text:
            continue
        if channel not in CHANNEL or ctype not in CTYPE:
            problems.append(f"风格样例第 {i} 行渠道或内容类型不在下拉列表里，已跳过")
            continue
        if text not in seen:
            examples.append({"channel": CHANNEL[channel], "content_type": CTYPE[ctype], "text": text})
            seen.add(text)
            added += 1
    if added:
        ex_path.write_text(yaml.safe_dump(examples, allow_unicode=True, sort_keys=False, width=1000), encoding="utf-8")

    print(f"知识卡片：{len(cards)} 张 → {out.relative_to(ROOT)}")
    print(f"风格样例：新增 {added} 条")
    for p in problems:
        print("  ·", p)


if __name__ == "__main__":
    main()
