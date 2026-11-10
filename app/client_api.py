"""对外小程序接口，统一挂在 /api/c 下。

登录用微信 openid（wx.login 的 code 换取），不要求手机号。
睡眠记录和自评属于敏感个人信息，必须先单独同意（health consent）才能写入。
"""

import hashlib
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from . import auth, qa
from .client_models import ChatMessage, ClientSession, ClientUser, PartnerLead, QAItem, SelfAssessment, SleepRecord
from .config import get_settings
from .db import get_session
from .models import User, now
from .products import load_product

router = APIRouter(prefix="/api/c", tags=["对外小程序"])
admin = APIRouter(prefix="/api/client-admin", tags=["对外小程序 · 后台"])

ROLES = ("self", "family", "partner")
_TIME = r"^([01]\d|2[0-3]):[0-5]\d$"
_DAY = r"^\d{4}-\d{2}-\d{2}$"
_AVATAR_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
MAX_AVATAR_BYTES = 2 * 1024 * 1024


# ---------- 登录 ----------

def wx_openid(code: str) -> str:
    """用小程序 wx.login 拿到的 code 向微信换 openid。"""
    s = get_settings()
    if not (s.wx_appid and s.wx_secret):
        if s.wx_dev_login:
            # 仅本地联调用：没配置 AppID 时按 code 生成一个固定的测试身份
            return "dev-" + hashlib.sha256(code.encode()).hexdigest()[:24]
        raise HTTPException(503, "服务器还没有配置小程序 AppID 和密钥")
    try:
        r = httpx.get("https://api.weixin.qq.com/sns/jscode2session", timeout=10, params={
            "appid": s.wx_appid, "secret": s.wx_secret, "js_code": code, "grant_type": "authorization_code"})
        data = r.json()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, "暂时连不上微信登录服务，请稍后再试") from e
    if not data.get("openid"):
        raise HTTPException(401, "微信登录失败，请重新进入小程序")
    return data["openid"]


def openid_resolver():
    return wx_openid


def current_client(authorization: str = Header(default=""), session: Session = Depends(get_session)) -> ClientUser:
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(401, "请先登录")
    row = session.scalar(select(ClientSession).where(ClientSession.token_hash == auth.hash_token(token)))
    if row is None or _aware(row.expires_at) < now():
        raise HTTPException(401, "登录已过期，请重新进入小程序")
    user = session.get(ClientUser, row.user_id)
    if user is None:
        raise HTTPException(401, "账号不存在")
    return user


def need_health_consent(user: ClientUser = Depends(current_client)) -> ClientUser:
    if user.health_consent_at is None:
        raise HTTPException(403, "need_health_consent")
    return user


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def user_out(u: ClientUser) -> dict:
    return {
        "id": u.id, "nickname": u.nickname, "avatar_url": u.avatar_url, "role": u.role,
        "registered": bool(u.privacy_consent_at and u.role),
        "health_consent": u.health_consent_at is not None,
    }


class LoginIn(BaseModel):
    code: str = Field(min_length=1, max_length=128)


@router.post("/login")
def login(body: LoginIn, session: Session = Depends(get_session), openid_of=Depends(openid_resolver)):
    openid = openid_of(body.code)
    user = session.scalar(select(ClientUser).where(ClientUser.openid == openid))
    if user is None:
        user = ClientUser(openid=openid)
        session.add(user)
        session.flush()
    user.last_login_at = now()
    session.execute(delete(ClientSession).where(ClientSession.user_id == user.id, ClientSession.expires_at < now()))
    token = secrets.token_urlsafe(32)
    session.add(ClientSession(user_id=user.id, token_hash=auth.hash_token(token),
                              expires_at=now() + timedelta(days=get_settings().client_session_days)))
    session.commit()
    return {"token": token, "user": user_out(user)}


# ---------- 我的资料 ----------

class RegisterIn(BaseModel):
    role: Literal["self", "family", "partner"]
    nickname: str = Field("", max_length=32)
    agree_privacy: bool


class ProfileIn(BaseModel):
    role: Literal["self", "family", "partner"] | None = None
    nickname: str | None = Field(None, max_length=32)


class ConsentIn(BaseModel):
    agree: bool


@router.get("/me")
def me(user: ClientUser = Depends(current_client)):
    return user_out(user)


@router.post("/register")
def register(body: RegisterIn, user: ClientUser = Depends(current_client), session: Session = Depends(get_session)):
    if not body.agree_privacy:
        raise HTTPException(400, "需要先阅读并同意用户协议和隐私政策")
    user.role = body.role
    user.nickname = body.nickname.strip() or user.nickname or "微信用户"
    user.privacy_consent_at = user.privacy_consent_at or now()
    session.commit()
    return user_out(user)


@router.patch("/me")
def update_me(body: ProfileIn, user: ClientUser = Depends(current_client), session: Session = Depends(get_session)):
    if body.role is not None:
        user.role = body.role
    if body.nickname is not None and body.nickname.strip():
        user.nickname = body.nickname.strip()
    session.commit()
    return user_out(user)


@router.post("/me/avatar")
def upload_avatar(file: UploadFile, user: ClientUser = Depends(current_client), session: Session = Depends(get_session)):
    ext = _AVATAR_TYPES.get(file.content_type or "")
    if ext is None:
        raise HTTPException(400, "头像只支持 JPG、PNG、WebP 图片")
    data = file.file.read(MAX_AVATAR_BYTES + 1)
    if len(data) > MAX_AVATAR_BYTES:
        raise HTTPException(400, "头像图片不能超过 2MB")
    folder = Path(get_settings().upload_dir) / "avatars"
    folder.mkdir(parents=True, exist_ok=True)
    _remove_avatar(user)
    name = f"{user.id}-{secrets.token_hex(8)}{ext}"
    (folder / name).write_bytes(data)
    user.avatar_url = f"/uploads/avatars/{name}"
    session.commit()
    return user_out(user)


def _remove_avatar(user: ClientUser) -> None:
    if user.avatar_url.startswith("/uploads/avatars/"):
        old = Path(get_settings().upload_dir) / "avatars" / Path(user.avatar_url).name
        old.unlink(missing_ok=True)


@router.post("/me/health-consent")
def health_consent(body: ConsentIn, user: ClientUser = Depends(current_client), session: Session = Depends(get_session)):
    """同意 / 撤回「记录睡眠与自评信息」。撤回时一并删除已有记录。"""
    if body.agree:
        user.health_consent_at = user.health_consent_at or now()
    else:
        user.health_consent_at = None
        session.execute(delete(SleepRecord).where(SleepRecord.user_id == user.id))
        session.execute(delete(SelfAssessment).where(SelfAssessment.user_id == user.id))
    session.commit()
    return user_out(user)


@router.delete("/me")
def delete_me(user: ClientUser = Depends(current_client), session: Session = Depends(get_session)):
    """注销账号：删除这个用户的全部数据。"""
    _remove_avatar(user)
    for model in (ChatMessage, SleepRecord, SelfAssessment, PartnerLead, ClientSession):
        session.execute(delete(model).where(model.user_id == user.id))
    session.delete(user)
    session.commit()
    return {"ok": True}


# ---------- 问答对话 ----------

class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=300)
    scene: Literal["c", "partner"] = "c"
    code: str = Field("", max_length=16)  # 点的是预设问题时带上编号，直接取那一条


class FeedbackIn(BaseModel):
    helpful: bool


def _q(item: QAItem) -> dict:
    return {"code": item.code, "question": item.question}


def chat_out(m: ChatMessage, related: list[QAItem] | None = None) -> dict:
    return {"id": m.id, "question": m.question, "answer": m.answer, "kind": m.kind,
            "helpful": m.helpful, "created_at": m.created_at, "related": [_q(i) for i in related or []]}


@router.get("/home")
def home(scene: Literal["c", "partner"] = "c", _: ClientUser = Depends(current_client),
         session: Session = Depends(get_session)):
    """首页的常见问题，按分类、按权重从高到低。"""
    pool = [i for i in qa.published(session, load_product().id) if i.code != qa.RISK_CODE]
    wanted = ["partner"] if scene == "partner" else ["learn", "experience", "aftercare"]
    names = {c["id"]: c["name"] for c in qa.CATEGORIES}
    return {"categories": [
        {"id": c, "name": names[c], "questions": [_q(i) for i in qa.top_questions(pool, c, 20)]} for c in wanted]}


@router.post("/chat")
def chat(body: ChatIn, user: ClientUser = Depends(current_client), session: Session = Depends(get_session)):
    product = load_product().id
    reply = qa.answer(session, product, body.message, body.scene, body.code)
    msg = ChatMessage(product=product, user_id=user.id, scene=body.scene, question=body.message.strip(),
                      answer=reply.text, kind=reply.kind, qa_code=reply.item.code if reply.item else "")
    session.add(msg)
    session.commit()
    # 风险信号下不给任何推荐问题，页面只显示求助引导
    return chat_out(msg, [] if reply.kind == "risk" else reply.related)


@router.post("/chat/{message_id}/feedback")
def feedback(message_id: int, body: FeedbackIn, user: ClientUser = Depends(current_client),
             session: Session = Depends(get_session)):
    msg = session.get(ChatMessage, message_id)
    if msg is None or msg.user_id != user.id:
        raise HTTPException(404, "找不到这条对话")
    msg.helpful = body.helpful
    session.commit()
    return {"ok": True}


@router.get("/chat/history")
def history(limit: int = 30, user: ClientUser = Depends(current_client), session: Session = Depends(get_session)):
    rows = session.scalars(select(ChatMessage).where(ChatMessage.user_id == user.id)
                           .order_by(ChatMessage.id.desc()).limit(max(1, min(limit, 100))))
    return [chat_out(m) for m in reversed(list(rows))]


@router.delete("/chat/history")
def clear_history(user: ClientUser = Depends(current_client), session: Session = Depends(get_session)):
    session.execute(delete(ChatMessage).where(ChatMessage.user_id == user.id, ChatMessage.kind != "risk"))
    session.commit()
    return {"ok": True}


# ---------- 睡眠记录 ----------

class SleepIn(BaseModel):
    day: str = Field(pattern=_DAY)
    bedtime: str = Field(pattern=_TIME)
    wake_time: str = Field(pattern=_TIME)
    quality: int = Field(ge=1, le=5)
    note: str = Field("", max_length=300)


def _minutes(t: str) -> int:
    return int(t[:2]) * 60 + int(t[3:])


def sleep_out(r: SleepRecord) -> dict:
    duration = (_minutes(r.wake_time) - _minutes(r.bedtime)) % (24 * 60)
    return {"id": r.id, "day": r.day, "bedtime": r.bedtime, "wake_time": r.wake_time,
            "quality": r.quality, "note": r.note, "minutes": duration}


@router.post("/sleep-records")
def save_sleep(body: SleepIn, user: ClientUser = Depends(need_health_consent), session: Session = Depends(get_session)):
    try:
        day = date.fromisoformat(body.day)
    except ValueError as e:
        raise HTTPException(400, "日期不正确") from e
    if day > date.today() + timedelta(days=1):
        raise HTTPException(400, "不能记录未来的日期")
    row = session.scalar(select(SleepRecord).where(SleepRecord.user_id == user.id, SleepRecord.day == body.day))
    if row is None:
        row = SleepRecord(user_id=user.id, day=body.day)
        session.add(row)
    row.bedtime, row.wake_time, row.quality, row.note = body.bedtime, body.wake_time, body.quality, body.note.strip()
    session.commit()
    return sleep_out(row)


@router.get("/sleep-records")
def list_sleep(limit: int = 14, user: ClientUser = Depends(need_health_consent), session: Session = Depends(get_session)):
    rows = session.scalars(select(SleepRecord).where(SleepRecord.user_id == user.id)
                           .order_by(SleepRecord.day.desc()).limit(max(1, min(limit, 90))))
    return [sleep_out(r) for r in rows]


@router.delete("/sleep-records/{record_id}")
def delete_sleep(record_id: int, user: ClientUser = Depends(need_health_consent), session: Session = Depends(get_session)):
    row = session.get(SleepRecord, record_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "找不到这条记录")
    session.delete(row)
    session.commit()
    return {"ok": True}


# ---------- 自评与回访 ----------

class AssessIn(BaseModel):
    kind: Literal["before", "after", "next_day"]
    relax: int = Field(ge=1, le=5)
    sleep: int = Field(ge=1, le=5)
    note: str = Field("", max_length=300)


def assess_out(a: SelfAssessment) -> dict:
    return {"id": a.id, "kind": a.kind, "relax": a.relax, "sleep": a.sleep, "note": a.note, "created_at": a.created_at}


@router.post("/assessments")
def save_assessment(body: AssessIn, user: ClientUser = Depends(need_health_consent),
                    session: Session = Depends(get_session)):
    row = SelfAssessment(user_id=user.id, kind=body.kind, relax=body.relax, sleep=body.sleep, note=body.note.strip())
    session.add(row)
    session.commit()
    return assess_out(row)


@router.get("/assessments")
def list_assessments(limit: int = 30, user: ClientUser = Depends(need_health_consent),
                     session: Session = Depends(get_session)):
    rows = session.scalars(select(SelfAssessment).where(SelfAssessment.user_id == user.id)
                           .order_by(SelfAssessment.id.desc()).limit(max(1, min(limit, 100))))
    return [assess_out(a) for a in rows]


# ---------- 伙伴意向 ----------

class LeadIn(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    org_type: str = Field(min_length=1, max_length=32)
    city: str = Field("", max_length=32)
    contact: str = Field(min_length=5, max_length=64)
    interest: Literal["learn", "cooperate"] = "learn"
    note: str = Field("", max_length=500)
    agree_contact: bool


def lead_out(x: PartnerLead) -> dict:
    return {"id": x.id, "name": x.name, "org_type": x.org_type, "city": x.city, "contact": x.contact,
            "interest": x.interest, "note": x.note, "status": x.status, "created_at": x.created_at}


@router.post("/leads")
def create_lead(body: LeadIn, user: ClientUser = Depends(current_client), session: Session = Depends(get_session)):
    if not body.agree_contact:
        raise HTTPException(400, "需要先同意我们用你留下的方式联系你")
    if not re.search(r"\w", body.contact):
        raise HTTPException(400, "请填写可以联系到你的方式")
    recent = session.scalar(select(func.count()).select_from(PartnerLead).where(
        PartnerLead.user_id == user.id, PartnerLead.created_at > now() - timedelta(days=1)))
    if recent >= 3:
        raise HTTPException(429, "今天已经登记过了，我们会尽快联系你")
    row = PartnerLead(product=load_product().id, user_id=user.id, name=body.name.strip(), org_type=body.org_type.strip(),
                      city=body.city.strip(), contact=body.contact.strip(), interest=body.interest, note=body.note.strip())
    session.add(row)
    session.commit()
    return lead_out(row)


@router.get("/leads")
def my_leads(user: ClientUser = Depends(current_client), session: Session = Depends(get_session)):
    rows = session.scalars(select(PartnerLead).where(PartnerLead.user_id == user.id).order_by(PartnerLead.id.desc()))
    return [lead_out(x) for x in rows]


# ---------- 后台（内部账号使用，沿用底座的角色权限） ----------

class QAEditIn(BaseModel):
    question: str | None = Field(None, min_length=1, max_length=300)
    answer: str | None = Field(None, min_length=1)
    keywords: list[str] | None = None
    weight: int | None = Field(None, ge=1, le=5)
    pending: str | None = None
    status: Literal["published", "draft"] | None = None


def qa_out(i: QAItem) -> dict:
    return {"id": i.id, "code": i.code, "category": i.category, "question": i.question, "answer": i.answer,
            "keywords": i.keywords, "audience": i.audience, "weight": i.weight, "risk": i.risk,
            "course_level": i.course_level, "pending": i.pending, "status": i.status, "updated_at": i.updated_at}


@admin.get("/qa")
def admin_list_qa(_: User = Depends(auth.require("reviewer")), session: Session = Depends(get_session)):
    rows = session.scalars(select(QAItem).where(QAItem.product == load_product().id).order_by(QAItem.id))
    return [qa_out(i) for i in rows]


@admin.patch("/qa/{qa_id}")
def admin_edit_qa(qa_id: int, body: QAEditIn, _: User = Depends(auth.require("reviewer")),
                  session: Session = Depends(get_session)):
    item = session.get(QAItem, qa_id)
    if item is None or item.product != load_product().id:
        raise HTTPException(404, "找不到这条问答")
    if body.status == "draft" and item.code == qa.RISK_CODE:
        raise HTTPException(400, "风险信号的应答不能下线")
    for key, value in body.model_dump(exclude_none=True).items():
        setattr(item, key, value)
    session.commit()
    return qa_out(item)


@admin.get("/overview")
def admin_overview(_: User = Depends(auth.require("reviewer")), session: Session = Depends(get_session)):
    """运营看的数据：用户数、对话评价、没答上的问题、待跟进的风险信号、伙伴意向。"""
    product = load_product().id

    def count(model, *where):
        return session.scalar(select(func.count()).select_from(model).where(*where))

    mine = ChatMessage.product == product
    unanswered = session.scalars(select(ChatMessage).where(mine, ChatMessage.kind.in_(["fallback", "suggest"]))
                                 .order_by(ChatMessage.id.desc()).limit(50))
    unhelpful = session.scalars(select(ChatMessage).where(mine, ChatMessage.helpful.is_(False))
                                .order_by(ChatMessage.id.desc()).limit(50))
    risks = session.scalars(select(ChatMessage).where(mine, ChatMessage.kind == "risk", ChatMessage.handled.is_(False))
                            .order_by(ChatMessage.id.desc()).limit(50))
    leads = session.scalars(select(PartnerLead).where(PartnerLead.product == product)
                            .order_by(PartnerLead.id.desc()).limit(50))
    return {
        "users": count(ClientUser),
        "chats": count(ChatMessage, mine),
        "helpful": count(ChatMessage, mine, ChatMessage.helpful.is_(True)),
        "not_helpful": count(ChatMessage, mine, ChatMessage.helpful.is_(False)),
        "unanswered": [{"id": m.id, "question": m.question, "kind": m.kind, "created_at": m.created_at} for m in unanswered],
        "not_helpful_list": [{"id": m.id, "question": m.question, "qa_code": m.qa_code} for m in unhelpful],
        "risk_pending": [{"id": m.id, "user_id": m.user_id, "question": m.question, "created_at": m.created_at} for m in risks],
        "leads": [lead_out(x) for x in leads],
    }


@admin.post("/risk/{message_id}/handled")
def admin_risk_handled(message_id: int, _: User = Depends(auth.require("reviewer")),
                       session: Session = Depends(get_session)):
    msg = session.get(ChatMessage, message_id)
    if msg is None or msg.kind != "risk":
        raise HTTPException(404, "找不到这条记录")
    msg.handled = True
    session.commit()
    return {"ok": True}
