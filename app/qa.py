"""对话回答。

两种方式（.env 的 CHAT_MODE）：
- llm        大模型根据「审核过的问答 + 知识库卡片」组织回答，只许用给它的资料，资料里没有就说答不了
- retrieval  不调用大模型，只做问答匹配

回答的种类（kind）：
- ai       大模型生成的回答，页面上标「AI 生成」
- answer   审核过的问答原文（点预设问题、中高风险问题、或没开大模型时）
- suggest  有几条相近但拿不准，列出来让用户自己点
- fallback 资料里没有，如实说明并记录，供后续补充
- risk     出现自伤、极端情绪等信号：不经过大模型，只给固定的求助引导
- chat     问候、道谢
"""

import json
import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import compliance, knowledge
from .client_models import QAItem
from .config import get_settings
from .llm import LLM
from .products import Product

log = logging.getLogger(__name__)

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
HELLO = "你好，我是药王山沁养的 AI 问答助手。可以问我颅骶放松是什么、适不适合你、体验前后要注意什么，也可以聊聊睡前怎么慢下来。"
WHOAMI = "我是药王山沁养的 AI 问答助手，不是真人，也不是医生。我会根据老师审核过的资料回答关于颅骶放松和睡眠习惯的问题；涉及身体不适或用药，请以医生的意见为准。"
_WHOAMI = ("你是谁", "你是什么", "你叫什么", "是真人", "是人吗", "是机器人", "是ai", "是人工智能", "是客服吗", "是医生吗", "你是不是人")
THANKS = "不客气。还有想了解的，随时问我。"
_HELLO = {"你好", "您好", "hi", "hello", "在吗", "在么", "嗨", "哈喽", "有人吗"}
_THANKS = {"谢谢", "谢谢你", "多谢", "感谢", "好的", "好", "明白了", "知道了", "ok", "嗯", "嗯嗯", "好的谢谢"}
SUGGEST = "我不太确定你问的是哪一个，下面这几条里有你想了解的吗？"

_PUNCT = re.compile(r"[\s\W_]+", re.UNICODE)

ANSWER_SCORE = 0.6
SUGGEST_SCORE = 0.3
# 这些风险等级的问答（禁忌、孕期、慢性病、不适反应等）即使开了大模型也原样给出，不让模型改写
VERBATIM_RISKS = ("mid", "high")
NO_ANSWER = "【无法回答】"
MAX_AI_CHARS = 400

SYSTEM_PROMPT = """你是「{app}」小程序里的 AI 问答助手。

关于这家门店和服务：
{description}

最重要的一条：只根据用户消息里「资料」部分的内容回答。资料里没有、或者和问题对不上的，只输出 {no_answer} 这几个字，不要自己发挥，不要用常识补充。
资料里标了「待确认」的事项不要给出具体数字或承诺。

以下是你每次回答都要遵守的规则：

{rules}"""

# 规则文件缺失时用的最简规则
DEFAULT_RULES = """- 这是放松与睡眠习惯方面的信息服务，不是医疗服务：不诊断、不给用药建议、不承诺效果。
- 涉及疾病、用药、孕期、身体明显不适的，提醒以医生意见为准。
- 项目名称用「颅骶放松」，它不是按摩。
- 语气温和平实，150 字以内，纯文字。"""


@lru_cache
def assistant_rules(product: str) -> str:
    """大模型要遵守的通用规则，写在 products/<产品线>/qa/assistant_rules.md，老师和运营可以直接改。"""
    path = get_settings().products_dir / product / "qa" / "assistant_rules.md"
    if not path.exists():
        return DEFAULT_RULES
    lines = path.read_text(encoding="utf-8").splitlines()
    # 文件开头到第一个「## 」之前是给人看的说明，不交给模型
    start = next((i for i, line in enumerate(lines) if line.startswith("## ")), 0)
    return "\n".join(lines[start:]).strip() or DEFAULT_RULES


@lru_cache
def chat_rules(product: str) -> dict:
    path = get_settings().products_dir / product / "qa" / "chat_rules.yaml"
    data = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}
    return {"block": list(data.get("block") or []), "patterns": [re.compile(p) for p in data.get("block_patterns") or []]}


def blocked_terms(text: str, product: str, references: list[str]) -> list[str]:
    """大模型的回答里有没有不该出现的说法，或大段照搬知识库原文。返回命中的内容，空列表表示通过。"""
    rules = chat_rules(product)
    hits = [t for t in rules["block"] if t in text]
    hits += [m.group(0) for p in rules["patterns"] for m in [p.search(text)] if m]
    n = get_settings().copy_ngram
    for ref in references:
        hits += compliance._copied_spans(text, ref, n)
    return hits


@dataclass
class Reply:
    kind: str  # ai / answer / suggest / fallback / risk / chat
    text: str
    item: QAItem | None = None
    related: list[QAItem] = field(default_factory=list)
    model: str = ""


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


def answer(session: Session, product: Product, message: str, scene: str = "c", code: str = "",
           llm: LLM | None = None) -> Reply:
    items = published(session, product.id)
    by_code = {i.code: i for i in items}

    # 风险信号最先判断，永远不经过大模型
    if is_risk(message):
        risk_item = by_code.get(RISK_CODE)
        return Reply("risk", risk_item.answer if risk_item else RISK_FALLBACK, risk_item)

    # 风险条目只由风险识别触发，不参与普通匹配和推荐
    pool = [i for i in items if i.code != RISK_CODE]

    # 点的是预设问题：给那一条审核过的原文
    if code and code in by_code and code != RISK_CODE:
        item = by_code[code]
        return Reply("answer", item.answer, item, _related(pool, item))

    prefer = "partner" if scene == "partner" else None
    plain = _norm(message)
    if len(plain) <= 16 and any(k in plain for k in _WHOAMI):
        return Reply("chat", WHOAMI)
    if plain in _HELLO or plain in _THANKS:
        text = HELLO if plain in _HELLO else THANKS
        return Reply("chat", text, None, top_questions(pool, prefer or "learn", 3) if plain in _HELLO else [])

    ranked = sorted(
        ((score(message, i), i.category == prefer, i.weight, i) for i in pool),
        key=lambda t: t[:3], reverse=True)
    best_score, best = (ranked[0][0], ranked[0][3]) if ranked else (0.0, None)
    close = [t[3] for t in ranked[:3] if t[0] >= SUGGEST_SCORE]

    # 中高风险的问答对上了，原样给出
    if best and best_score >= ANSWER_SCORE and best.risk in VERBATIM_RISKS:
        return Reply("answer", best.answer, best, _related(pool, best))

    if llm is not None and get_settings().chat_mode == "llm":
        reply = _ai_answer(session, product, message, close, pool, llm)
        if reply is not None:
            return reply

    # 没开大模型，或大模型答不了 / 没通过检查：退回问答匹配
    if best and best_score >= ANSWER_SCORE:
        return Reply("answer", best.answer, best, _related(pool, best))
    if close:
        return Reply("suggest", SUGGEST, None, close)
    return Reply("fallback", FALLBACK, None, top_questions(pool, "partner" if prefer else "learn", 3))


def _ai_answer(session: Session, product: Product, message: str, qa_hits: list[QAItem], pool: list[QAItem],
               llm: LLM) -> Reply | None:
    """让大模型根据资料组织回答。答不了、出错、没通过检查都返回 None，由调用方退回问答匹配。"""
    settings = get_settings()
    try:
        cards = knowledge.search(session, product.pools(), message, llm, k=settings.chat_knowledge_top_k,
                                 max_risk=product.generation_max_risk)
    except Exception as e:  # noqa: BLE001  知识库不可用时只用问答清单
        log.warning("知识库检索失败：%s", e)
        cards = []
    if not qa_hits and not cards:
        return None

    parts = []
    for i in qa_hits:
        pending = f"\n（待确认：{i.pending}）" if i.pending else ""
        parts.append(f"[审核过的问答] 问：{i.question}\n答：{i.answer}{pending}")
    for c in cards:
        parts.append(f"[知识卡片] {c.topic}：{c.points}")
    system = SYSTEM_PROMPT.format(app="药王山沁养", description=product.description, no_answer=NO_ANSWER,
                                  rules=assistant_rules(product.id))
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": "资料：\n\n" + "\n\n".join(parts) + f"\n\n用户的问题：{message}"},
    ]
    try:
        result = llm.chat(messages, temperature=0.3)
    except Exception as e:  # noqa: BLE001
        log.warning("对话生成失败，退回问答匹配：%s", e)
        return None
    text = result.text.strip()
    if not text or NO_ANSWER in text or "无法回答" in text[:12]:
        return None
    hits = blocked_terms(text, product.id, [c.points for c in cards])
    if hits or len(text) > MAX_AI_CHARS:
        log.warning("对话回答没通过检查（%s），退回问答匹配", "、".join(hits) or "过长")
        return None
    top = qa_hits[0] if qa_hits else None
    related = _related(pool, top) if top else []
    return Reply("ai", text, top, related, result.model)


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
