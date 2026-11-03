import io

from openpyxl import load_workbook
from sqlalchemy import select

from app import db, knowledge, review_sheet
from app.models import KnowledgeCard


def make_cards(fake_llm):
    with db.SessionLocal() as s:
        cards = [knowledge.CardIn(topic=f"卡片{i}", points=f"第{i}张卡片的要点内容", risk_level="mid", domain="睡眠")
                 for i in range(4)]
        rows = knowledge.add_cards(s, "common", cards, fake_llm, review_status="pending", origin="auto")
        return [r.id for r in rows]


def fill(data: bytes, decisions: dict[int, tuple[str, str]]) -> bytes:
    wb = load_workbook(io.BytesIO(data))
    ws = wb[review_sheet.SHEET]
    for row in ws.iter_rows(min_row=2):
        d = decisions.get(row[0].value)
        if d:
            row[7].value, row[8].value = d
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_export_and_apply(client, fake_llm):
    ids = make_cards(fake_llm)
    with db.SessionLocal() as s:
        pending = list(s.scalars(select(KnowledgeCard).where(KnowledgeCard.review_status == "pending")))
        data = review_sheet.build(pending)

    ws = load_workbook(io.BytesIO(data))[review_sheet.SHEET]
    assert [c.value for c in ws[1]] == review_sheet.HEADERS
    assert ws.max_row == 5 and ws.cell(2, 4).value == "中"

    filled = fill(data, {ids[0]: ("通过", None), ids[1]: ("驳回", None),
                         ids[2]: ("修改后通过", "改过的要点，更准确的说法")})
    with db.SessionLocal() as s:
        result = review_sheet.apply(s, filled, ["sleep", "common"], fake_llm)
    assert result["stats"] == {"通过": 1, "驳回": 1, "修改后通过": 1, "未填写": 1}

    with db.SessionLocal() as s:
        got = {c.id: c for c in s.scalars(select(KnowledgeCard))}
    assert got[ids[0]].review_status == "approved"
    assert got[ids[1]].review_status == "rejected"
    assert got[ids[2]].review_status == "approved" and got[ids[2]].points == "改过的要点，更准确的说法"
    assert got[ids[2]].embedding == fake_llm.embed([knowledge.card_text("卡片2", "改过的要点，更准确的说法")])[0]
    assert got[ids[3]].review_status == "pending"


def test_apply_reports_problems(client, fake_llm):
    ids = make_cards(fake_llm)
    with db.SessionLocal() as s:
        data = review_sheet.build(list(s.scalars(select(KnowledgeCard))))
    filled = fill(data, {ids[0]: ("修改后通过", None), ids[1]: ("随便", None)})
    with db.SessionLocal() as s:
        result = review_sheet.apply(s, filled, ["sleep", "common"], fake_llm)
    assert len(result["problems"]) == 2
    with db.SessionLocal() as s:
        assert s.get(KnowledgeCard, ids[0]).review_status == "pending"
