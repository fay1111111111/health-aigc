"""问答检索：只从已审核的问答清单里作答，不让模型自由发挥。

三档结果：
- answer   问法和某条问答对得上，直接给这条的回答
- suggest  有几条相近但拿不准，列出来让用户自己点
- fallback 清单里没有，如实说明并记录，供后续补充问答
另有 risk：出现自伤、极端情绪等信号时，暂停一切推介，只给求助引导。
"""

import json
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from .client_models import QAItem
from .config import get_settings

CATEGORIES = [
    {"id": "learn", "name": "了解"},
    {"id": "experience", "name": "体验"},
    {"id": "aftercare", "name": "体验之后"},
    {"id": "partner", "name": "伙伴学习"},
]

# 风险信号：命中任意一个就走求助引导。宁可多拦，不可漏。
RISK_TERMS = [
    "不想活", "想死", "去死", "自杀", "自残", "轻生", "活不下去", "活着没意思", "活着没有意义",
    "结束生命", "结束自己", "伤害自己", "割腕", "跳楼", "了结自己", "不如死", "寻死", "遗书", "安乐死",
]
RISK_CODE = "售后-03"
RISK_FALLBACK = (
    "谢谢你告诉我这些。你现在的感受很重要，但这里的对话不能替代专业的心理危机干预或医疗帮助。"
    "如果你有伤害自己的想法，请马上找身边可信的人陪伴，并立即联系当地心理援助热线，或前往附近的医疗机构、急诊就医。"
    "若你处于危险中，请优先联系当地紧急救助。"
)
FALLBACK = (
    "这个问题我这里还没有准备好可靠的回答，已经记下来了，会交给老师补充。"
    "你可以换个说法再问，或者先看看下面这些常见问题。涉及身体不适的情况，请以医生的意见为准。"
)
HELLO = "你好，我是沁养小助手。可以问我颅骶放松是什么、适不适合你、体验前后要注意什么；我只回答已经由老师确认过的内容。"
THANKS = "不客气。还有想了解的，随时问我。"
_HELLO = {"你好", "您好", "hi", "hello", "在吗", "在么", "嗨", "哈喽", "有人吗"}
_THANKS = {"谢谢", "谢谢你", "多谢", "感谢", "好的", "好", "明白了", "知道了", "ok", "嗯", "嗯嗯", "好的谢谢"}
SUGGEST = "我不太确定你问的是哪一个，下面这几条里有你想了解的吗？"

_PUNCT = re.compile(r"[\s\W_]+", re.UNICODE)

ANSWER_SCORE = 0.6
SUGGEST_SCORE = 0.3


@dataclass
class Reply:
    kind: str  # answer / suggest / fallback / risk / chat（问候、道谢）
    text: str
    item: QAItem | None = None
    related: list[QAItem] = field(default_factory=list)


def _norm(text: str) -> str:
    return _PUNCT.sub("", text).lower()


def _bigrams(text: str) -> set[str]:
    return {text[i:i + 2] for i in range(len(text) - 1)}


def is_risk(message: str) -> bool:
    m = _norm(message)
    return any(term in m for term in RISK_TERMS)


def score(query: str, item: QAItem) -> float:
    """0-1。命中「其他问法」里的关键词算强匹配，其余按和标准问题的字面重合度算。"""
    q = _norm(query)
    if not q:
        return 0.0
    question = _norm(item.question)
    if q == question:
        return 1.0
    best = 0.0
    hits = [k for k in item.keywords if _norm(k) and _norm(k) in q]
    if hits:
        longest = max(len(_norm(k)) for k in hits)
        best = min(0.95, 0.6 + 0.05 * longest + 0.05 * (len(hits) - 1))
    qb = _bigrams(q)
    if qb:
        overlap = len(qb & _bigrams(question)) / len(qb)
        best = max(best, overlap * 0.9)
    return best


def published(session: Session, product: str) -> list[QAItem]:
    return list(session.scalars(select(QAItem).where(QAItem.product == product, QAItem.status == "published")))


def answer(session: Session, product: str, message: str, scene: str = "c", code: str = "") -> Reply:
    items = published(session, product)
    by_code = {i.code: i for i in items}

    if is_risk(message):
        risk_item = by_code.get(RISK_CODE)
        return Reply("risk", risk_item.answer if risk_item else RISK_FALLBACK, risk_item)

    # 风险条目只由风险识别触发，不参与普通匹配和推荐
    pool = [i for i in items if i.code != RISK_CODE]

    if code and code in by_code and code != RISK_CODE:
        item = by_code[code]
        return Reply("answer", item.answer, item, _related(pool, item))

    prefer = "partner" if scene == "partner" else None
    plain = _norm(message)
    if plain in _HELLO or plain in _THANKS:
        text = HELLO if plain in _HELLO else THANKS
        return Reply("chat", text, None, top_questions(pool, prefer or "learn", 3) if plain in _HELLO else [])
    ranked = sorted(
        ((score(message, i), i.category == prefer, i.weight, i) for i in pool),
        key=lambda t: t[:3], reverse=True)
    if ranked and ranked[0][0] >= ANSWER_SCORE:
        item = ranked[0][3]
        return Reply("answer", item.answer, item, _related(pool, item))
    close = [t[3] for t in ranked[:3] if t[0] >= SUGGEST_SCORE]
    if close:
        return Reply("suggest", SUGGEST, None, close)
    return Reply("fallback", FALLBACK, None, top_questions(pool, "partner" if prefer else "learn", 3))


def _related(pool: list[QAItem], item: QAItem, n: int = 3) -> list[QAItem]:
    same = [i for i in pool if i.category == item.category and i.code != item.code]
    return sorted(same, key=lambda i: (-i.weight, i.code))[:n]


def top_questions(pool: list[QAItem], category: str, n: int) -> list[QAItem]:
    return sorted((i for i in pool if i.category == category), key=lambda i: (-i.weight, i.code))[:n]


def seed(session: Session, product: str) -> int:
    """把 products/<product>/qa/*.json 里的问答导入数据库。已存在的条目不覆盖，后台改过的内容不会被冲掉。"""
    folder = get_settings().products_dir / product / "qa"
    if not folder.exists():
        return 0
    existing = set(session.scalars(select(QAItem.code).where(QAItem.product == product)))
    added = 0
    for path in sorted(folder.glob("*.json")):
        for row in json.loads(path.read_text(encoding="utf-8")):
            if row["code"] in existing:
                continue
            session.add(QAItem(product=product, **row))
            existing.add(row["code"])
            added += 1
    session.commit()
    return added
