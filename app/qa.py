"""对话回答。

两种方式（.env 的 CHAT_MODE）：
- llm        大模型根据「审核过的问答 + 知识库卡片」组织回答，只许用给它的资料，资料里没有就说答不了
- retrieval  不调用大模型，只做问答匹配

回答的种类（kind）：
- ai         大模型根据资料生成的回答，页面上标「AI 生成」
- companion  倾听陪伴：用户在说感受、心事时，大模型按「倾听与陪伴」规则回应，不需要资料
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
    "结束生命", "结束自己", "结束这一切", "伤害自己", "割腕", "跳楼", "跳下去", "了结自己", "不如死", "寻死",
    "遗书", "安乐死", "一了百了", "死了算了", "不想再醒来", "永远睡过去", "没有我会更好", "没我更好",
    "吃多少安眠药", "安眠药吃多少", "一瓶安眠药", "烧炭",
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
# 用户在说心事，但大模型不可用或回答没过检查时用的固定回应
COMFORT = "我在，慢慢说。不用急着把事情讲清楚，想到哪儿说到哪儿就好。如果此刻只想安静一会儿，也可以先慢慢呼气，比吸气再长一点，做几次。"
_FEELING = (
    "难过", "难受", "伤心", "委屈", "想哭", "哭了", "焦虑", "烦躁", "心烦", "很烦", "压力", "好累", "太累", "心累", "孤独", "孤单",
    "害怕", "恐惧", "紧张", "心慌", "不安", "失落", "空虚", "迷茫", "无助", "自责", "愧疚", "生气", "愤怒", "憋屈", "没人理解",
    "睡不着", "失眠", "胡思乱想", "停不下来", "静不下来", "脑子很乱", "心里很乱", "心里堵",
)
SUGGEST = "我不太确定你问的是哪一个，下面这几条里有你想了解的吗？"

_PUNCT = re.compile(r"[\s\W_]+", re.UNICODE)

ANSWER_SCORE = 0.6
SUGGEST_SCORE = 0.3
# 这些风险等级的问答（禁忌、孕期、慢性病、不适反应等）即使开了大模型也原样给出，不让模型改写
VERBATIM_RISKS = ("mid", "high")
NO_ANSWER = "【无法回答】"
TAG_INFO, TAG_COMPANION, TAG_CRISIS = "【信息】", "【陪伴】", "【危机】"
MAX_AI_CHARS = 400
MAX_COMPANION_CHARS = 200
WATCH_CODE = "关注"
# C 端对话只用写给用户和家属的卡片；写给伙伴的留给 B 端
CHAT_AUDIENCES = ("user", "family")
CARD_LABELS = {"knowledge": "知识卡片", "companion": "陪伴参考", "practice": "小练习", "referral": "转介参考"}  # 记在对话记录的 qa_code 里，后台据此列出「需要留意」的对话

SYSTEM_PROMPT = """你是「{app}」小程序里的 AI 问答助手。

关于这家门店和服务：
{description}

每次回答，第一行只写下面三个标记之一，然后换行写正文：

{tag_info}  用户在问事情（服务、体验、睡眠知识、学习等）。
  只根据用户消息里「资料」部分的内容回答。资料里没有、或者和问题对不上的，正文只写 {no_answer}，不要自己发挥，不要用常识补充。
  资料里标了「待确认」的事项不要给出具体数字或承诺。资料后面写了「使用边界」的，严格照边界用。
{tag_companion}  用户在说自己的感受、压力、心事，或者睡不着的烦躁。
  按下面「倾听与陪伴」的规则回应。资料里的[陪伴参考][小练习][转介参考]可以借用其中的视角和做法，用自己的话说，不照搬原文，不提书名和人名；对不上就不用。
{tag_crisis}  用户流露出伤害自己、不想活下去、想消失的念头，哪怕说得很隐晦。
  正文留空，什么都不要写，系统会接手。

拿不准是问事情还是说心事时，按说心事处理：先接住感受，再顺带回答。
对方在接着前面的话往下说、回答你上一句的提问，或者只回了「嗯」「是」「好」这样的短话时，用 {tag_companion} 接着聊，回应要接得上前面说过的内容，不要重新开头，也不要重复前面说过的话。

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
    return {
        "block": list(data.get("block") or []),
        "patterns": [re.compile(p) for p in data.get("block_patterns") or []],
        "companion_patterns": [re.compile(p) for p in data.get("companion_block_patterns") or []],
        "watch": [_norm(t) for t in data.get("watch") or []],
    }


def needs_watch(message: str, product: str) -> bool:
    """不到危机的程度，但值得多关切一句、并让人工留意的说法。"""
    m = _norm(message)
    return any(t and t in m for t in chat_rules(product)["watch"])


def is_feeling(message: str) -> bool:
    m = _norm(message)
    return any(t in m for t in _FEELING)


def blocked_terms(text: str, product: str, references: list[str], companion: bool = False) -> list[str]:
    """大模型的回答里有没有不该出现的说法，或大段照搬知识库原文。返回命中的内容，空列表表示通过。"""
    rules = chat_rules(product)
    hits = [t for t in rules["block"] if t in text]
    patterns = rules["patterns"] + (rules["companion_patterns"] if companion else [])
    hits += [m.group(0) for p in patterns for m in [p.search(text)] if m]
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
    watch: bool = False  # 需要人工留意


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
           llm: LLM | None = None, history: list[tuple[str, str]] | None = None, talking: bool = False) -> Reply:
    """history 是这位用户最近几轮的（用户说的, 助手答的），让大模型接得上前面的话。
    talking=True 表示上一轮是陪伴回应、对方正在说心事：这时「嗯」「好」也交给大模型接，答不了也不甩推荐问题。"""
    items = published(session, product.id)
    by_code = {i.code: i for i in items}
    risk_item = by_code.get(RISK_CODE)
    crisis = Reply("risk", risk_item.answer if risk_item else RISK_FALLBACK, risk_item)

    # 风险信号最先判断，永远不经过大模型
    if is_risk(message):
        return crisis

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
    use_llm = llm is not None and get_settings().chat_mode == "llm"
    if (plain in _HELLO or plain in _THANKS) and not (talking and use_llm):
        text = HELLO if plain in _HELLO else THANKS
        return Reply("chat", text, None, top_questions(pool, prefer or "learn", 3) if plain in _HELLO else [])

    watch = needs_watch(message, product.id)
    ranked = sorted(
        ((score(message, i), i.category == prefer, i.weight, i) for i in pool),
        key=lambda t: t[:3], reverse=True)
    best_score, best = (ranked[0][0], ranked[0][3]) if ranked else (0.0, None)
    close = [t[3] for t in ranked[:3] if t[0] >= SUGGEST_SCORE]

    # 中高风险的问答对上了，原样给出
    if best and best_score >= ANSWER_SCORE and best.risk in VERBATIM_RISKS and not watch:
        return Reply("answer", best.answer, best, _related(pool, best))

    if use_llm:
        reply = _ai_answer(session, product, message, close, pool, llm, history or [], watch)
        if reply is CRISIS:
            return crisis
        if reply is not None:
            return reply

    # 没开大模型，或大模型答不了 / 没通过检查
    if watch or ((talking or is_feeling(message)) and best_score < ANSWER_SCORE):
        return Reply("companion", COMFORT, watch=watch)  # 在说心事：给一句固定的回应，不拿问答去堵
    if best and best_score >= ANSWER_SCORE:
        return Reply("answer", best.answer, best, _related(pool, best))
    if close:
        return Reply("suggest", SUGGEST, None, close)
    return Reply("fallback", FALLBACK, None, top_questions(pool, "partner" if prefer else "learn", 3))


CRISIS = object()  # _ai_answer 的特殊返回值：大模型判断是危机信号


def _ai_answer(session: Session, product: Product, message: str, qa_hits: list[QAItem], pool: list[QAItem],
               llm: LLM, history: list[tuple[str, str]], watch: bool):
    """让大模型回答或陪伴。返回 Reply；判断为危机返回 CRISIS；答不了、出错、没通过检查返回 None，由调用方兜底。"""
    settings = get_settings()
    try:
        cards = knowledge.search(session, product.pools(), message, llm, k=settings.chat_knowledge_top_k,
                                 max_risk=product.generation_max_risk, audiences=CHAT_AUDIENCES)
    except Exception as e:  # noqa: BLE001  知识库不可用时只用问答清单
        log.warning("知识库检索失败：%s", e)
        cards = []

    parts = []
    for i in qa_hits:
        pending = f"\n（待确认：{i.pending}）" if i.pending else ""
        parts.append(f"[审核过的问答] 问：{i.question}\n答：{i.answer}{pending}")
    for c in cards:
        label = CARD_LABELS.get(c.use, "知识卡片")
        family = "（写给家属的）" if c.audience == "family" else ""
        limit = f"\n（使用边界：{c.boundary}）" if c.boundary else ""
        parts.append(f"[{label}]{family} {c.topic}：{c.points}{limit}")
    material = "\n\n".join(parts) if parts else "（这次没有找到相关资料）"
    system = SYSTEM_PROMPT.format(app="药王山沁养", description=product.description, no_answer=NO_ANSWER,
                                  rules=assistant_rules(product.id), tag_info=TAG_INFO,
                                  tag_companion=TAG_COMPANION, tag_crisis=TAG_CRISIS)
    if watch:
        system += "\n\n提醒：用户这句话里有需要留意的说法。请格外温和，先接住感受，并自然地问一句对方现在是否安全、身边有没有可以说话的人；如果你判断对方有伤害自己的念头，输出危机标记。"
    messages = [{"role": "system", "content": system}]
    for said, replied in history:
        messages.append({"role": "user", "content": said})
        messages.append({"role": "assistant", "content": replied})
    messages.append({"role": "user", "content": f"资料：\n\n{material}\n\n用户这次说的话：{message}"})
    try:
        result = llm.chat(messages, temperature=0.4)
    except Exception as e:  # noqa: BLE001
        log.warning("对话生成失败：%s", e)
        return None

    text = result.text.strip()
    if text.startswith(TAG_CRISIS):
        return CRISIS
    companion = text.startswith(TAG_COMPANION)
    for tag in (TAG_INFO, TAG_COMPANION):
        text = text.removeprefix(tag)
    text = text.strip()
    if TAG_CRISIS in text:
        return CRISIS
    if not text or NO_ANSWER in text or "无法回答" in text[:12]:
        return None

    if companion:
        hits = blocked_terms(text, product.id, [c.points for c in cards], companion=True)
        if hits or len(text) > MAX_COMPANION_CHARS:
            log.warning("陪伴回应没通过检查（%s）", "、".join(hits) or "过长")
            return Reply("companion", COMFORT, watch=watch)
        return Reply("companion", text, model=result.model, watch=watch)

    if not parts:
        return None  # 没有资料却给出了信息类回答，不采用
    hits = blocked_terms(text, product.id, [c.points for c in cards])
    if hits or len(text) > MAX_AI_CHARS:
        log.warning("对话回答没通过检查（%s）", "、".join(hits) or "过长")
        return None
    top = qa_hits[0] if qa_hits else None
    return Reply("ai", text, top, _related(pool, top) if top else [], result.model, watch)


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
