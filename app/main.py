import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import auth, client_api, generation, knowledge, qa
from .config import get_settings
from .db import SessionLocal, get_session, init_db
from .llm import LLM, LLMError, get_llm
from .models import Generation, KnowledgeCard, User
from .products import load_product, product_channels

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    product = load_product()  # 启动时就校验产品线配置，配置有误直接报错
    with SessionLocal() as session:
        qa.seed(session, product.id)  # 导入问答清单（已存在的不覆盖）
    yield


app = FastAPI(title="大健康 AIGC 平台", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(client_api.router)
app.include_router(client_api.admin)
get_settings().upload_dir.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=get_settings().upload_dir), name="uploads")


def llm_dep() -> LLM:
    return get_llm()


# ---------- 数据结构 ----------

class GenerateIn(BaseModel):
    channel: str
    audience: str
    content_type: str
    brief: str = Field("", max_length=2000)


class EditIn(BaseModel):
    edited_output: str = Field(min_length=1)


class ReviewIn(BaseModel):
    action: Literal["approve", "reject"]
    note: str = ""


class UserIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    role: Literal["operator", "reviewer", "admin"]


def gen_out(g: Generation) -> dict:
    return {
        "id": g.id, "channel": g.channel, "audience": g.audience, "content_type": g.content_type,
        "brief": g.brief, "output": g.output, "edited_output": g.edited_output, "final_text": g.final_text,
        "compliance": g.compliance, "status": g.status, "review_note": g.review_note,
        "model_used": g.model_used, "rewrites": g.rewrites, "created_by": g.created_by,
        "reviewed_by": g.reviewed_by, "created_at": g.created_at, "reviewed_at": g.reviewed_at,
    }


def card_out(c: KnowledgeCard) -> dict:
    return {"id": c.id, "pool": c.product, "domain": c.domain, "topic": c.topic, "points": c.points,
            "source": c.source, "risk_level": c.risk_level, "tags": c.tags, "origin": c.origin,
            "review_status": c.review_status, "questions": c.questions or [], "use": c.use,
            "audience": c.audience, "boundary": c.boundary}


def get_gen(session: Session, gen_id: int) -> Generation:
    g = session.get(Generation, gen_id)
    if g is None or g.product != load_product().id:
        raise HTTPException(404, "找不到这条内容")
    return g


# ---------- 基础 ----------

@app.get("/health")
def health():
    return {"ok": True, "product": load_product().id}


@app.get("/api/me")
def me(user: User = Depends(auth.current_user)):
    return {"id": user.id, "name": user.name, "role": user.role}


@app.get("/api/meta")
def meta(_: User = Depends(auth.current_user)):
    """小程序表单用：当前产品线可选的渠道、受众、内容类型。"""
    p = load_product()
    return {
        "product": {"id": p.id, "name": p.name},
        "channels": [{"id": c.id, "name": c.name} for c in product_channels(p)],
        "audiences": [a.model_dump() for a in p.audiences],
        "content_types": [t.model_dump() for t in p.content_types],
    }


# ---------- 生成与审核 ----------

@app.post("/api/generate")
def generate(body: GenerateIn, user: User = Depends(auth.current_user),
             session: Session = Depends(get_session), llm: LLM = Depends(llm_dep)):
    try:
        g = generation.generate(session, llm, load_product(), user.id,
                                body.channel, body.audience, body.content_type, body.brief)
    except generation.GenerationError as e:
        raise HTTPException(400, str(e)) from e
    except LLMError as e:
        raise HTTPException(502, str(e)) from e
    return gen_out(g)


@app.get("/api/generations")
def list_generations(status: str | None = None, mine: bool = False, limit: int = 50,
                     user: User = Depends(auth.current_user), session: Session = Depends(get_session)):
    q = select(Generation).where(Generation.product == load_product().id)
    if status:
        q = q.where(Generation.status == status)
    if mine or user.role == "operator":
        q = q.where(Generation.created_by == user.id)
    rows = session.scalars(q.order_by(Generation.id.desc()).limit(min(limit, 200)))
    return [gen_out(g) for g in rows]


@app.get("/api/generations/{gen_id}")
def get_generation(gen_id: int, user: User = Depends(auth.current_user), session: Session = Depends(get_session)):
    g = get_gen(session, gen_id)
    if user.role == "operator" and g.created_by != user.id:
        raise HTTPException(403, "没有权限")
    return gen_out(g)


@app.patch("/api/generations/{gen_id}")
def edit_generation(gen_id: int, body: EditIn, user: User = Depends(auth.current_user),
                    session: Session = Depends(get_session)):
    g = get_gen(session, gen_id)
    if user.role == "operator" and g.created_by != user.id:
        raise HTTPException(403, "没有权限")
    if g.status == "approved":
        raise HTTPException(409, "已审核通过的内容不能再修改，请重新生成")
    g.edited_output = body.edited_output
    g.compliance = generation.recheck(session, load_product(), g)
    g.status = "pending"
    session.commit()
    return gen_out(g)


@app.post("/api/generations/{gen_id}/review")
def review_generation(gen_id: int, body: ReviewIn, user: User = Depends(auth.require("reviewer")),
                      session: Session = Depends(get_session)):
    g = get_gen(session, gen_id)
    if body.action == "approve" and not g.compliance.get("passed"):
        raise HTTPException(409, "内容仍含禁用表述，请先修改再通过")
    g.status = "approved" if body.action == "approve" else "rejected"
    g.review_note = body.note
    g.reviewed_by = user.id
    g.reviewed_at = datetime.now(timezone.utc)
    session.commit()
    return gen_out(g)


@app.get("/api/generations/{gen_id}/export")
def export_generation(gen_id: int, user: User = Depends(auth.current_user), session: Session = Depends(get_session)):
    g = get_gen(session, gen_id)
    if user.role == "operator" and g.created_by != user.id:
        raise HTTPException(403, "没有权限")
    if g.status != "approved":
        raise HTTPException(409, "审核通过后才能导出")
    return {"text": f"{g.final_text}\n\n{load_product().ai_label}"}


# ---------- 知识库 ----------

class CardReviewIn(BaseModel):
    action: Literal["approve", "reject"]


def get_card(session: Session, card_id: int) -> KnowledgeCard:
    c = session.get(KnowledgeCard, card_id)
    if c is None or c.product not in load_product().pools():
        raise HTTPException(404, "找不到这张卡片")
    return c


@app.get("/api/knowledge")
def list_knowledge(q: str = "", status: Literal["approved", "pending", "rejected"] = "approved", limit: int = 100,
                   user: User = Depends(auth.require("reviewer")),
                   session: Session = Depends(get_session), llm: LLM = Depends(llm_dep)):
    """status=pending 是待人工审核的中高风险卡片。"""
    pools = load_product().pools()
    if q.strip() and status == "approved":
        cards = knowledge.search(session, pools, q, llm, k=20, uses=None)
    else:
        cards = session.scalars(select(KnowledgeCard).where(
            KnowledgeCard.product.in_(pools), KnowledgeCard.active.is_(True),
            KnowledgeCard.review_status == status).order_by(KnowledgeCard.id.desc()).limit(min(limit, 500)))
    return [card_out(c) for c in cards]


@app.get("/api/knowledge/stats")
def knowledge_stats(user: User = Depends(auth.require("reviewer")), session: Session = Depends(get_session)):
    from sqlalchemy import func

    rows = session.execute(select(
        KnowledgeCard.product, KnowledgeCard.review_status, func.count()).where(
        KnowledgeCard.product.in_(load_product().pools()), KnowledgeCard.active.is_(True)).group_by(
        KnowledgeCard.product, KnowledgeCard.review_status))
    return [{"pool": p, "review_status": s, "count": n} for p, s, n in rows]


@app.post("/api/knowledge")
def add_knowledge(cards: list[knowledge.CardIn], pool: str | None = None, user: User = Depends(auth.require("admin")),
                  session: Session = Depends(get_session), llm: LLM = Depends(llm_dep)):
    target = pool or load_product().id
    if target not in load_product().pools():
        raise HTTPException(400, f"只能写入这些知识库：{load_product().pools()}")
    try:
        rows = knowledge.add_cards(session, target, cards, llm)
    except LLMError as e:
        raise HTTPException(502, str(e)) from e
    return {"added": len(rows), "skipped": len(cards) - len(rows)}


@app.post("/api/knowledge/{card_id}/review")
def review_knowledge(card_id: int, body: CardReviewIn, user: User = Depends(auth.require("reviewer")),
                     session: Session = Depends(get_session)):
    c = get_card(session, card_id)
    c.review_status = "approved" if body.action == "approve" else "rejected"
    session.commit()
    return card_out(c)


@app.delete("/api/knowledge/{card_id}")
def remove_knowledge(card_id: int, user: User = Depends(auth.require("admin")), session: Session = Depends(get_session)):
    c = get_card(session, card_id)
    c.active = False
    session.commit()
    return {"ok": True}


# ---------- 账号 ----------

@app.post("/api/users")
def add_user(body: UserIn, user: User = Depends(auth.require("admin")), session: Session = Depends(get_session)):
    new_user, token = auth.create_user(session, body.name, body.role)
    return {"id": new_user.id, "name": new_user.name, "role": new_user.role, "token": token}


@app.get("/api/users")
def list_users(user: User = Depends(auth.require("admin")), session: Session = Depends(get_session)):
    rows = session.scalars(select(User).order_by(User.id))
    return [{"id": u.id, "name": u.name, "role": u.role, "active": u.active} for u in rows]


@app.post("/api/users/{user_id}/deactivate")
def deactivate_user(user_id: int, user: User = Depends(auth.require("admin")),
                    session: Session = Depends(get_session)):
    """停用账号（令牌立即失效）。令牌泄露或人员离开时使用。"""
    try:
        auth.deactivate_user(session, user_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}
