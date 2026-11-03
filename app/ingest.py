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
    error: str = ""
    notes: list[str] = field(default_factory=list)


# ---------- 抓取与提取正文 ----------

def fetch_text(url: str, timeout: float = 30) -> str:
    try:
        resp = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout, follow_redirects=True)
    except httpx.HTTPError as e:
        raise FetchError(f"无法访问：{e}") from e
    if resp.status_code >= 400:
        raise FetchError(f"HTTP {resp.status_code}")
    ctype = resp.headers.get("content-type", "").lower()
    if "pdf" in ctype or url.lower().endswith(".pdf"):
        return pdf_text(resp.content)
    if ctype.startswith("text/plain") or url.lower().endswith(".txt"):
        return resp.text
    return html_text(resp.content)


def html_text(content: bytes) -> str:
    import trafilatura

    text = trafilatura.extract(content, include_comments=False, include_tables=True, favor_recall=True)
    if not text:
        raise FetchError("没有提取到正文（可能是动态页面或需要登录）")
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
1. 只提取对普通人有用、表述明确的健康知识；广告、新闻动态、会议流程、目录、人名职务等不要提取。
2. 每张卡片讲一个知识点：topic 是 15 字以内的主题；points 是 40 到 120 字的要点。
3. points 必须用你自己的话重新表达，不能照抄原文句子（古籍原句可以在引号里引用不超过 10 个字）。
4. 不要提取具体药物名称、用药剂量、处方和方剂组成。
5. 保留原文里的关键数字（如时长、温度、年龄段），不要编造原文没有的数字和结论。
6. risk_level：
   - low：一般生活方式和常识（作息、环境、饮食习惯、运动习惯等）
   - mid：涉及身体功能和效果的说法、研究结论、特定人群的一般建议
   - high：疾病诊断与治疗、药物、孕产妇和婴幼儿、穴位方药、慢病管理、需要医生指导的内容
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
            cards.append(CardIn(
                topic=str(d.get("topic", "")).strip()[:200],
                points=str(d.get("points", "")).strip(),
                risk_level=d.get("risk_level") if d.get("risk_level") in knowledge.RISK_ORDER else "mid",
                domain=domain,
                tags=[str(t) for t in (d.get("tags") or [])][:6],
                source=source_label(src),
            ))
        except ValidationError:
            continue
    return cards


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


def ingest_source(session: Session, llm: LLM, src: SourceIn, fetcher=fetch_text, force: bool = False) -> IngestResult:
    settings = get_settings()
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

        for chunk in chunks:
            cards = extract_cards(llm, chunk, src)
            result.extracted += len(cards)
            # 照搬原文的卡片丢弃
            clean = []
            for c in cards:
                if compliance._copied_spans(c.points, chunk, settings.copy_ngram):
                    result.dropped_copy += 1
                else:
                    clean.append(c)
            if not clean:
                continue
            # 和库里已有卡片（含本次刚入库的）意思重复的丢弃
            vectors = llm.embed([knowledge.card_text(c.topic, c.points) for c in clean])
            keep_cards, keep_vecs = [], []
            for c, v in zip(clean, vectors):
                _, sim = knowledge.most_similar(session, [src.pool], v, llm)
                dup_in_batch = any(knowledge.cosine(v, kv) >= settings.dedup_similarity for kv in keep_vecs)
                if sim >= settings.dedup_similarity or dup_in_batch:
                    result.dropped_dup += 1
                else:
                    keep_cards.append(c)
                    keep_vecs.append(v)
            rows = knowledge.add_cards(session, src.pool, keep_cards, llm, review_status=None,
                                       origin="auto", source_id=row.id, vectors=keep_vecs)
            result.added += sum(1 for r in rows if r.review_status == "approved")
            result.pending += sum(1 for r in rows if r.review_status == "pending")
            result.dropped_dup += len(keep_cards) - len(rows)  # 同名主题
        row.status, row.error = "done", ""
    except FetchError as e:
        result.status, result.error = "failed", str(e)
        row.status, row.error = "failed", str(e)
    except Exception as e:  # noqa: BLE001  单条资料失败不影响其他资料
        log.exception("处理资料失败：%s", src.url)
        result.status, result.error = "failed", f"{type(e).__name__}: {e}"
        row.status, row.error = "failed", result.error

    from sqlalchemy import func

    from .models import KnowledgeCard

    row.card_count = session.scalar(select(func.count()).select_from(KnowledgeCard).where(
        KnowledgeCard.source_id == row.id, KnowledgeCard.active.is_(True))) or 0
    row.fetched_at = datetime.now(timezone.utc)
    session.commit()
    return result
