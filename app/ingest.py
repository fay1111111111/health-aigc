"""自动入库流水线：抓取公开资料 → 提取正文 → 模型改写成知识卡片 → 照搬检测 → 去重 → 入库。

低风险卡片直接入库可用；中、高风险卡片入库后处于「待审核」，审核通过才会参与检索。
每条资料登记在 sources 表里，备案时可以导出说明语料来源。
"""

import io
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import compliance, knowledge
from .config import get_settings
from .knowledge import CardIn
from .llm import LLM
from .models import Source

log = logging.getLogger(__name__)

DOMAINS = ["睡眠", "营养膳食", "身体活动", "心理健康", "中医养生", "慢病预防", "体重管理",
           "生活方式", "妇幼健康", "老年健康", "健康素养"]

USER_AGENT = "Mozilla/5.0 (compatible; HealthAIGC-KnowledgeBot/1.0)"


class FetchError(RuntimeError):
    pass


@dataclass
class SourceIn:
    url: str
    title: str = ""
    publisher: str = ""
    source_type: str = ""
    domain: str = ""
    pool: str = "common"
    license_note: str = ""


@dataclass
class IngestResult:
    url: str
    status: str
    chunks: int = 0
    extracted: int = 0
    added: int = 0
    pending: int = 0
    dropped_copy: int = 0
    dropped_dup: int = 0
    rewritten: int = 0
    trimmed: int = 0  # 核对时删掉了原文没有的内容
    dropped_unsupported: int = 0  # 核对时发现原文无依据而丢弃
    error: str = ""
    notes: list[str] = field(default_factory=list)
    cards: list = field(default_factory=list)  # 试跑模式下的提取结果


# ---------- 抓取与提取正文 ----------

def fetch_text(url: str, timeout: float = 30) -> str:
    try:
        resp = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout, follow_redirects=True)
    except httpx.HTTPError as e:
        raise FetchError(f"无法访问：{e}") from e
    if resp.status_code in (403, 412, 418, 429, 503):
        raise FetchError(f"HTTP {resp.status_code}（网站拒绝自动访问，请换用其他网站转载的同一份资料）")
    if resp.status_code >= 400:
        raise FetchError(f"HTTP {resp.status_code}")
    ctype = resp.headers.get("content-type", "").lower()
    if "pdf" in ctype or url.lower().endswith(".pdf"):
        text = pdf_text(resp.content)
    elif ctype.startswith("text/plain") or url.lower().endswith(".txt"):
        text = resp.text
    else:
        text = html_text(resp.content)
    if len(_NON_SPACE.sub("", text)) < MIN_TEXT_CHARS:
        raise FetchError(f"正文太短（{len(text.strip())} 字），可能只抓到页面外壳或正文在附件里")
    return text


MIN_TEXT_CHARS = 300
_NON_SPACE = re.compile(r"\s+")


def html_text(content: bytes) -> str:
    import trafilatura

    text = trafilatura.extract(content, include_comments=False, include_tables=True, favor_recall=True) or ""
    if len(text) < MIN_TEXT_CHARS:
        # 正文识别失败时退回整页文字；导航、页脚等杂项由后面的模型提取环节过滤
        full = trafilatura.html2txt(content) or ""
        if len(full) > len(text):
            text = full
    if not text.strip():
        raise FetchError("没有提取到文字（可能是动态页面或需要登录）")
    return text


def pdf_text(content: bytes) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(content))
        text = "\n".join((p.extract_text() or "") for p in reader.pages)
    except Exception as e:  # noqa: BLE001  PDF 格式问题各式各样
        raise FetchError(f"PDF 解析失败：{e}") from e
    if len(text.strip()) < 50:
        raise FetchError("PDF 里没有可提取的文字（可能是扫描件）")
    return text


def chunk_text(text: str, size: int = 1800) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    chunks, buf = [], ""
    for p in paras:
        while len(p) > size:  # 超长段落硬切
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.append(p[:size])
            p = p[size:]
        if len(buf) + len(p) + 1 > size and buf:
            chunks.append(buf)
            buf = p
        else:
            buf = f"{buf}\n{p}" if buf else p
    if buf:
        chunks.append(buf)
    return [c for c in chunks if len(c) >= 80]  # 太短的多半是导航、页脚


# ---------- 模型改写成卡片 ----------

EXTRACT_SYSTEM = f"""你是健康科普编辑，负责把资料整理成「知识卡片」，供营销内容生成时作为事实依据。

规则：
1. 只提取对普通人日常保健和生活方式有用、表述明确的知识（作息睡眠、饮食营养、运动、情绪压力、中医养生、体重、季节保健、特定人群的日常注意事项等）。
   不要提取：献血、急救操作、传染病防控、疫苗、医保和就医流程、法律法规、医疗机构介绍、广告、新闻动态、会议流程、目录、人名职务。
2. 每张卡片讲一个知识点：topic 是 15 字以内的主题；points 是 40 到 120 字的要点。
3. points 必须用你自己的话重新表达，不能照抄原文句子（古籍原句可以在引号里引用不超过 10 个字）。
4. 不要提取具体药物名称、用药剂量、处方和方剂组成。
5. 只写原文明确表达的内容。保留原文里的关键数字（如时长、温度、年龄段）；不要补充原文没有的数字、结论、原理解释、中医或西医理论、评价。
6. risk_level：
   - low：一般生活方式和常识（作息、环境、饮食习惯、运动习惯等）
   - mid：涉及身体功能和效果的说法、研究结论、生理指标和医学数值（血压、血糖、心率、体温等）、特定人群的一般建议
   - high：疾病诊断与治疗、药物、孕产妇和婴幼儿、穴位方药、慢病管理、需要医生指导的内容
   拿不准时选更高的一级。
7. domain 从这些里选一个：{"、".join(DOMAINS)}
8. 输出 JSON 数组，不要任何解释：[{{"topic": "...", "points": "...", "risk_level": "low", "domain": "...", "tags": ["..."]}}]
   这段资料没有可提取的内容就输出 []。"""


def parse_cards(raw: str) -> list[dict]:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < start:
        return []
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return []
    return [d for d in data if isinstance(d, dict)]


def extract_cards(llm: LLM, chunk: str, src: SourceIn) -> list[CardIn]:
    user = f"资料来源：{src.publisher}《{src.title}》\n建议领域：{src.domain or '自行判断'}\n\n资料内容：\n{chunk}"
    raw = llm.chat([{"role": "system", "content": EXTRACT_SYSTEM}, {"role": "user", "content": user}], temperature=0.3).text
    cards = []
    for d in parse_cards(raw):
        domain = d.get("domain") if d.get("domain") in DOMAINS else (src.domain or "")
        try:
            cards.append(enforce_risk(CardIn(
                topic=str(d.get("topic", "")).strip()[:200],
                points=str(d.get("points", "")).strip(),
                risk_level=d.get("risk_level") if d.get("risk_level") in knowledge.RISK_ORDER else "mid",
                domain=domain,
                tags=[str(t) for t in (d.get("tags") or [])][:6],
                source=source_label(src),
            )))
        except ValidationError:
            continue
    return cards


# 含这些生理/医学数值或词语的卡片，至少按中风险处理（进待审），不完全依赖模型判断
_MEDICAL_MARKERS = re.compile(
    r"mmHg|mmol|mg/dL|毫摩尔|毫米汞柱|血压|血糖|血脂|胆固醇|心率|脉搏|体温|次/分|毫克|微克|"
    r"疾病|患者|病人|症状|诊断|治疗|药物|用药|服用|手术|医院|就医|孕妇|孕期|婴儿|婴幼儿|新生儿|穴",
    re.IGNORECASE)


def enforce_risk(card: CardIn) -> CardIn:
    if card.risk_level == "low" and _MEDICAL_MARKERS.search(f"{card.topic}{card.points}"):
        card.risk_level = "mid"
    return card


VERIFY_SYSTEM = """你是严谨的核对员。下面给出一段原资料，以及从中提取的知识卡片。请逐条核对：
- 卡片里如果有原资料中没有的内容（额外的原理解释、理论、评价、数字、结论），删掉这些部分，其余保持不变，不要补充任何新内容。
- 如果卡片的主要内容在原资料里找不到依据，标记 keep 为 false。
输出 JSON 数组，顺序和数量与输入的卡片一致：[{"keep": true, "points": "核对修正后的要点"}]"""


def verify_cards(llm: LLM, chunk: str, cards: list[CardIn], result: "IngestResult") -> list[CardIn]:
    """逐条对照原文核对，删掉原文没有的内容；原文找不到依据的卡片丢弃。"""
    if not cards:
        return []
    payload = json.dumps([{"topic": c.topic, "points": c.points} for c in cards], ensure_ascii=False)
    raw = llm.chat([{"role": "system", "content": VERIFY_SYSTEM},
                    {"role": "user", "content": f"原资料：\n{chunk}\n\n知识卡片：\n{payload}"}], temperature=0).text
    verdicts = parse_cards(raw)
    if len(verdicts) != len(cards):
        result.notes.append("核对结果格式不对，本段卡片按原样保留，请审核时留意")
        return cards
    out = []
    for c, v in zip(cards, verdicts):
        if v.get("keep") is False:
            result.dropped_unsupported += 1
            result.notes.append(f"核对丢弃（原文无依据）：「{c.topic}」")
            continue
        points = str(v.get("points") or "").strip()
        if points and points != c.points:
            result.trimmed += 1
            c = enforce_risk(c.model_copy(update={"points": points}))
        out.append(c)
    return out


REWRITE_SYSTEM = """下面这些知识卡片的 points 和原资料的句子重合太多。请逐条用你自己的话重新表达，意思和关键数字不变，40 到 120 字。
输出 JSON 数组，顺序与输入一致，每项只包含 topic 和 points：[{"topic": "...", "points": "..."}]"""


def rewrite_copied(llm: LLM, cards: list[CardIn]) -> list[CardIn]:
    payload = json.dumps([{"topic": c.topic, "points": c.points} for c in cards], ensure_ascii=False)
    raw = llm.chat([{"role": "system", "content": REWRITE_SYSTEM}, {"role": "user", "content": payload}],
                   temperature=0.5).text
    out = []
    for c, d in zip(cards, parse_cards(raw)):
        points = str(d.get("points", "")).strip()
        if points:
            out.append(c.model_copy(update={"points": points}))
    return out


def source_label(src: SourceIn) -> str:
    title = f"《{src.title}》" if src.title else ""
    return f"{src.publisher}{title} {src.url}".strip()[:500]


# ---------- 整条资料入库 ----------

def upsert_source(session: Session, src: SourceIn) -> Source:
    row = session.scalar(select(Source).where(Source.url == src.url))
    if row is None:
        row = Source(url=src.url)
        session.add(row)
    for k in ("title", "publisher", "source_type", "domain", "pool", "license_note"):
        setattr(row, k, getattr(src, k))
    session.commit()
    return row


def _split_copied(cards: list[CardIn], chunk: str, n: int) -> tuple[list[CardIn], list[CardIn]]:
    clean, copied = [], []
    for c in cards:
        (copied if compliance._copied_spans(c.points, chunk, n) else clean).append(c)
    return clean, copied


def process_chunk(session: Session, llm: LLM, chunk: str, src: SourceIn, result: IngestResult,
                  seen: list[tuple[list[float], str]]) -> tuple[list[CardIn], list[list[float]]]:
    """提取一段资料的卡片，处理照搬和重复，返回可以入库的卡片和向量。"""
    settings = get_settings()
    cards = extract_cards(llm, chunk, src)
    result.extracted += len(cards)
    cards = verify_cards(llm, chunk, cards, result)

    # 照搬原文的卡片改写一次，仍然照搬的丢弃
    clean, copied = _split_copied(cards, chunk, settings.copy_ngram)
    if copied:
        fixed = [enforce_risk(c) for c in rewrite_copied(llm, copied)]
        fixed_clean, still_copied = _split_copied(fixed, chunk, settings.copy_ngram)
        result.rewritten += len(fixed_clean)
        result.dropped_copy += len(copied) - len(fixed_clean)
        clean += fixed_clean
    if not clean:
        return [], []

    # 和库里已有卡片、本条资料前面已提取的卡片意思重复的丢弃
    vectors = llm.embed([knowledge.card_text(c.topic, c.points) for c in clean])
    keep_cards, keep_vecs = [], []
    for c, v in zip(clean, vectors):
        db_card, sim = knowledge.most_similar(session, [src.pool], v, llm)
        match = db_card.topic if db_card is not None else ""
        for kv, kt in seen + list(zip(keep_vecs, [k.topic for k in keep_cards])):
            s = knowledge.cosine(v, kv)
            if s > sim:
                sim, match = s, kt
        if sim >= settings.dedup_similarity:
            result.dropped_dup += 1
            result.notes.append(f"去重：「{c.topic}」与「{match}」相似度 {sim:.2f}")
        else:
            if sim >= settings.dedup_similarity - 0.08:
                result.notes.append(f"相近但保留：「{c.topic}」与「{match}」相似度 {sim:.2f}")
            keep_cards.append(c)
            keep_vecs.append(v)
    return keep_cards, keep_vecs


def ingest_source(session: Session, llm: LLM, src: SourceIn, fetcher=fetch_text, force: bool = False,
                  dry_run: bool = False, seen: list[tuple[list[float], str]] | None = None) -> IngestResult:
    """dry_run=True 时只提取、不入库、不改变资料状态，提取结果放在 result.cards 里供查看。

    seen：同一次运行里前面资料已保留卡片的（向量, 主题），跨资料去重用（试跑时尤其需要，
    因为试跑的卡片不进数据库，数据库去重看不到它们）。
    """
    settings = get_settings()
    row = None
    if not dry_run:
        row = upsert_source(session, src)
        if row.status == "done" and not force:
            return IngestResult(src.url, "skipped", notes=["已处理过"])

    result = IngestResult(src.url, "done")
    try:
        text = fetcher(src.url)
        chunks = chunk_text(text)[:settings.ingest_max_chunks]
        result.chunks = len(chunks)
        if not chunks:
            raise FetchError("正文太短，没有可用内容")

        if seen is None:
            seen = []
        for chunk in chunks:
            keep_cards, keep_vecs = process_chunk(session, llm, chunk, src, result, seen)
            seen.extend(zip(keep_vecs, [c.topic for c in keep_cards]))
            if dry_run:
                result.cards += keep_cards
                result.added += sum(1 for c in keep_cards if c.risk_level == "low")
                result.pending += sum(1 for c in keep_cards if c.risk_level != "low")
                continue
            rows = knowledge.add_cards(session, src.pool, keep_cards, llm, review_status=None,
                                       origin="auto", source_id=row.id, vectors=keep_vecs)
            result.added += sum(1 for r in rows if r.review_status == "approved")
            result.pending += sum(1 for r in rows if r.review_status == "pending")
            result.dropped_dup += len(keep_cards) - len(rows)  # 同名主题
        if row is not None:
            row.status, row.error = "done", ""
    except FetchError as e:
        result.status, result.error = "failed", str(e)
    except Exception as e:  # noqa: BLE001  单条资料失败不影响其他资料
        log.exception("处理资料失败：%s", src.url)
        result.status, result.error = "failed", f"{type(e).__name__}: {e}"

    if row is not None:
        from sqlalchemy import func

        from .models import KnowledgeCard

        if result.status == "failed":
            row.status, row.error = "failed", result.error
        row.card_count = session.scalar(select(func.count()).select_from(KnowledgeCard).where(
            KnowledgeCard.source_id == row.id, KnowledgeCard.active.is_(True))) or 0
        row.fetched_at = datetime.now(timezone.utc)
        session.commit()
    return result
