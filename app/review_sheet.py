"""待审知识卡片的审核表：导出 Excel → 人工填写审核结果 → 导回批量生效。"""

import io

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.datavalidation import DataValidation
from sqlalchemy.orm import Session

from . import knowledge
from .llm import LLM
from .models import KnowledgeCard

SHEET = "待审卡片"
HEADERS = ["编号", "知识库", "领域", "风险等级", "主题", "要点", "出处", "审核结果", "修改后的要点", "备注"]
WIDTHS = [8, 12, 10, 9, 18, 60, 36, 13, 50, 20]
DECISIONS = ["通过", "驳回", "修改后通过"]
RISK = {"low": "低", "mid": "中", "high": "高"}
POOL = {"common": "大健康通用"}

GUIDE = [
    ("知识卡片审核表", True),
    ("", False),
    ("这些是自动从公开资料整理出的中、高风险知识卡片，审核通过后才会用于生成营销文案。", False),
    ("", False),
    ("怎么填", True),
    ("1. 在「待审卡片」表的「审核结果」列下拉选择：通过 / 驳回 / 修改后通过。", False),
    ("2. 选「修改后通过」时，把改好的要点写在「修改后的要点」列。", False),
    ("3. 拿不准的可以先空着，空着的卡片保持待审，下次还会导出。", False),
    ("4. 不要改动「编号」列，也不要删除或调整行的顺序以外的表头。", False),
    ("", False),
    ("什么情况选驳回", True),
    ("· 内容不准确，或和你了解的常识、专业知识不符", False),
    ("· 涉及疾病诊断、治疗、用药，或需要医生指导才能做的事", False),
    ("· 和业务关系不大，用不到营销内容里", False),
    ("· 和其他卡片重复", False),
    ("", False),
    ("什么情况选修改后通过", True),
    ("· 大部分内容可以用，但有个别说法太绝对、夸大，或带了不必要的医学细节", False),
]

THIN = Side(style="thin", color="D0D0D0")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def build(cards: list[KnowledgeCard]) -> bytes:
    wb = Workbook()
    guide = wb.active
    guide.title = "填写说明"
    guide.column_dimensions["A"].width = 90
    for i, (text, bold) in enumerate(GUIDE, 1):
        guide.cell(i, 1, text).font = Font(name="Arial", bold=bold, size=14 if i == 1 else 11)

    ws = wb.create_sheet(SHEET)
    for i, (h, w) in enumerate(zip(HEADERS, WIDTHS), 1):
        c = ws.cell(1, i, h)
        c.font = Font(name="Arial", bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F3864")
        c.alignment = Alignment(vertical="center", wrap_text=True)
        c.border = BORDER
        ws.column_dimensions[c.column_letter].width = w
    input_fill = PatternFill("solid", fgColor="FFF8E1")
    for r, card in enumerate(cards, 2):
        values = [card.id, POOL.get(card.product, card.product), card.domain, RISK.get(card.risk_level, card.risk_level),
                  card.topic, card.points, card.source, "", "", ""]
        for i, v in enumerate(values, 1):
            c = ws.cell(r, i, v)
            c.font = Font(name="Arial")
            c.alignment = Alignment(wrap_text=True, vertical="top")
            c.border = BORDER
            if i >= 8:
                c.fill = input_fill
    if cards:
        dv = DataValidation(type="list", formula1='"' + ",".join(DECISIONS) + '"', allow_blank=True)
        dv.error = "请从下拉列表中选择"
        dv.showErrorMessage = True
        ws.add_data_validation(dv)
        dv.add(f"H2:H{len(cards) + 1}")
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:J{max(len(cards) + 1, 1)}"
    wb.active = 1

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def apply(session: Session, data: bytes, pools: list[str], llm: LLM) -> dict:
    wb = load_workbook(io.BytesIO(data), read_only=True)
    if SHEET not in wb.sheetnames:
        raise ValueError(f"找不到「{SHEET}」工作表，请使用系统导出的审核表")
    rows = list(wb[SHEET].iter_rows(min_row=1, values_only=True))
    header = [str(h or "").strip() for h in rows[0]]
    try:
        col = {name: header.index(name) for name in ("编号", "审核结果", "修改后的要点")}
    except ValueError as e:
        raise ValueError(f"表头缺少必要的列：{e}") from e

    stats = {"通过": 0, "驳回": 0, "修改后通过": 0, "未填写": 0}
    problems: list[str] = []
    edited: list[KnowledgeCard] = []
    for n, row in enumerate(rows[1:], 2):
        def get(name):
            v = row[col[name]] if col[name] < len(row) else None
            return "" if v is None else str(v).strip()

        decision, card_id = get("审核结果"), get("编号")
        if not card_id:
            continue
        if not decision:
            stats["未填写"] += 1
            continue
        if decision not in DECISIONS:
            problems.append(f"第 {n} 行：审核结果「{decision}」无法识别，已跳过")
            continue
        try:
            card = session.get(KnowledgeCard, int(float(card_id)))
        except ValueError:
            card = None
        if card is None or card.product not in pools or not card.active:
            problems.append(f"第 {n} 行：找不到编号 {card_id} 的卡片，已跳过")
            continue
        if decision == "修改后通过":
            new_points = get("修改后的要点")
            if not new_points:
                problems.append(f"第 {n} 行（编号 {card_id}）：选了修改后通过但没写修改后的要点，已跳过")
                continue
            card.points = new_points
            edited.append(card)
        card.review_status = "rejected" if decision == "驳回" else "approved"
        stats[decision] += 1

    if edited:  # 改过的要点要重新生成向量，检索才准
        vectors = llm.embed([knowledge.card_text(c.topic, c.points) for c in edited])
        for c, v in zip(edited, vectors):
            c.embedding = v
    session.commit()
    return {"stats": stats, "problems": problems}
