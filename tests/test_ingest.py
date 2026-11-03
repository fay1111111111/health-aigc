import json

import pytest
from sqlalchemy import select

from app import db, ingest
from app.models import KnowledgeCard, Source

ARTICLE = (
    "成年人每天需要7到8小时睡眠，但存在个体差异。\n"
    "卧室温度保持适宜、光线偏暗，有助于入睡。睡前避免剧烈运动和大量饮酒。\n"
) * 3

SRC = ingest.SourceIn(url="https://example.gov.cn/sleep", title="睡眠科普", publisher="某卫健委",
                      source_type="gov", domain="睡眠", pool="common", license_note="政府公开科普")


def reply(*cards):
    return json.dumps(list(cards), ensure_ascii=False)


@pytest.fixture
def session(client):
    with db.SessionLocal() as s:
        yield s


def test_chunking_drops_short_noise():
    text = "首页 | 导航\n" + ("这是一段足够长的健康科普正文内容，" * 10) + "\n版权所有"
    chunks = ingest.chunk_text(text, size=100)
    assert chunks and all(len(c) >= 80 for c in chunks)


def test_parse_cards_tolerates_code_fence():
    raw = '```json\n[{"topic": "a", "points": "b"}]\n```'
    assert ingest.parse_cards(raw) == [{"topic": "a", "points": "b"}]
    assert ingest.parse_cards("没有内容") == []


def test_ingest_routes_by_risk(session, fake_llm):
    fake_llm.replies = [reply(
        {"topic": "睡眠时长因人而异", "points": "一般建议成年人每晚睡够七到八个钟头，不过每个人需要的睡眠量并不完全一样。",
         "risk_level": "low", "domain": "睡眠"},
        {"topic": "睡眠不足与慢病", "points": "长期缺觉可能和多种慢性疾病的发生有关联，出现明显不适应当及时就医检查。",
         "risk_level": "high", "domain": "慢病预防"},
    )]
    r = ingest.ingest_source(session, fake_llm, SRC, fetcher=lambda url: ARTICLE)
    assert r.status == "done" and r.added == 1 and r.pending == 1

    cards = {c.topic: c for c in session.scalars(select(KnowledgeCard))}
    assert cards["睡眠时长因人而异"].review_status == "approved"
    assert cards["睡眠不足与慢病"].review_status == "pending"
    assert cards["睡眠时长因人而异"].origin == "auto" and cards["睡眠时长因人而异"].product == "common"
    src = session.scalar(select(Source))
    assert src.status == "done" and src.card_count == 2 and src.publisher == "某卫健委"


def test_copied_card_is_dropped(session, fake_llm):
    fake_llm.replies = [reply(
        {"topic": "照抄", "points": "卧室温度保持适宜、光线偏暗，有助于入睡。睡前避免剧烈运动和大量饮酒。", "risk_level": "low"})]
    r = ingest.ingest_source(session, fake_llm, SRC, fetcher=lambda url: ARTICLE)
    assert r.dropped_copy == 1 and r.added == 0


def test_duplicate_card_is_dropped(session, fake_llm, monkeypatch):
    # 模拟真实向量模型：意思一样的文字向量接近。这里让向量只取决于要点，不取决于主题
    original = fake_llm.embed
    monkeypatch.setattr(fake_llm, "embed", lambda texts: original([t.split("\n", 1)[-1] for t in texts]))
    card = {"topic": "睡前少喝酒", "points": "临睡前喝酒看似容易犯困，其实会让后半夜睡得更浅、更容易醒。", "risk_level": "low"}
    same_meaning = dict(card, topic="不要靠喝酒入睡")  # 主题不同、要点一样
    fake_llm.replies = [reply(card, same_meaning)]
    r = ingest.ingest_source(session, fake_llm, SRC, fetcher=lambda url: ARTICLE)
    assert r.added == 1 and r.dropped_dup == 1


def test_fetch_failure_recorded_and_done_sources_skipped(session, fake_llm):
    def boom(url):
        raise ingest.FetchError("HTTP 404")

    r = ingest.ingest_source(session, fake_llm, SRC, fetcher=boom)
    assert r.status == "failed" and "404" in r.error
    assert session.scalar(select(Source)).status == "failed"

    fake_llm.replies = [reply()]
    assert ingest.ingest_source(session, fake_llm, SRC, fetcher=lambda u: ARTICLE).status == "done"
    assert ingest.ingest_source(session, fake_llm, SRC, fetcher=lambda u: ARTICLE).status == "skipped"


def test_pending_cards_not_used_until_approved(client, tokens, fake_llm, session):
    from conftest import h

    fake_llm.replies = [reply(
        {"topic": "中风险知识", "points": "规律的中等强度运动对改善睡眠质量有一定帮助，可以从每天散步开始坚持。",
         "risk_level": "mid", "domain": "身体活动"})]
    ingest.ingest_source(session, fake_llm, SRC, fetcher=lambda url: ARTICLE)

    def generate_prompt():
        fake_llm.calls.clear()
        client.post("/api/generate", headers=h(tokens["operator"]), json={
            "channel": "xiaohongshu", "audience": "c_local", "content_type": "seeding"})
        return fake_llm.calls[0][1]["content"]

    assert "中风险知识" not in generate_prompt()  # 待审核，不参与生成

    pending = client.get("/api/knowledge?status=pending", headers=h(tokens["reviewer"])).json()
    assert [c["topic"] for c in pending] == ["中风险知识"]
    client.post(f"/api/knowledge/{pending[0]['id']}/review", json={"action": "approve"}, headers=h(tokens["reviewer"]))

    assert "中风险知识" in generate_prompt()  # 审核通过后参与生成（通用库 common 被检索）
