"""对外小程序（C 端 / B 端伙伴）用到的表。和内部账号、稿件分开，互不影响。"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .models import now


class ClientUser(Base):
    """小程序用户：用微信 openid 识别，不存手机号。"""

    __tablename__ = "client_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    openid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    nickname: Mapped[str] = mapped_column(String(32), default="")
    avatar_url: Mapped[str] = mapped_column(String(300), default="")
    role: Mapped[str] = mapped_column(String(16), default="")  # self 自己了解 / family 家庭 / partner 从业伙伴
    privacy_consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 睡眠、自评属于敏感个人信息，需要单独同意
    health_consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_login_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ClientSession(Base):
    __tablename__ = "client_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("client_users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class QAItem(Base):
    """问答清单：对话只从已发布（published）的条目里作答。"""

    __tablename__ = "qa_items"
    __table_args__ = (UniqueConstraint("product", "code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product: Mapped[str] = mapped_column(String(32), index=True)
    code: Mapped[str] = mapped_column(String(16))  # 了解-01 / 体验-01 / 售后-01 / B端-01
    category: Mapped[str] = mapped_column(String(16))  # learn / experience / aftercare / partner
    question: Mapped[str] = mapped_column(String(300))
    answer: Mapped[str] = mapped_column(Text)
    keywords: Mapped[list] = mapped_column(JSON, default=list)  # 用户常见的其他问法
    audience: Mapped[list] = mapped_column(JSON, default=list)
    weight: Mapped[int] = mapped_column(Integer, default=3)  # 1-5
    risk: Mapped[str] = mapped_column(String(8), default="low")  # low / mid / high
    course_level: Mapped[str] = mapped_column(String(32), default="")
    pending: Mapped[str] = mapped_column(Text, default="")  # 待确认事项，只在后台显示
    status: Mapped[str] = mapped_column(String(16), default="published")  # published / draft
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class ChatMessage(Base):
    """一问一答的记录，带「有帮助 / 没帮助」评价。"""

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product: Mapped[str] = mapped_column(String(32), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("client_users.id"), index=True)
    scene: Mapped[str] = mapped_column(String(16), default="c")  # c / partner
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16))  # ai 大模型生成 / answer 问答原文 / suggest 给候选 / fallback 没答上 / risk 风险信号 / chat 问候
    qa_code: Mapped[str] = mapped_column(String(16), default="")
    helpful: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    handled: Mapped[bool] = mapped_column(Boolean, default=False)  # 风险信号是否已有人跟进
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SleepRecord(Base):
    __tablename__ = "sleep_records"
    __table_args__ = (UniqueConstraint("user_id", "day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("client_users.id"), index=True)
    day: Mapped[str] = mapped_column(String(10))  # 醒来那天，YYYY-MM-DD
    bedtime: Mapped[str] = mapped_column(String(5))  # HH:MM
    wake_time: Mapped[str] = mapped_column(String(5))
    quality: Mapped[int] = mapped_column(Integer)  # 1-5
    note: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SelfAssessment(Base):
    """服务前后自评、次日回访，都是 1-5 分。"""

    __tablename__ = "self_assessments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("client_users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # before / after / next_day
    relax: Mapped[int] = mapped_column(Integer)  # 放松程度 1-5
    sleep: Mapped[int] = mapped_column(Integer)  # 睡眠感受 1-5
    note: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PartnerLead(Base):
    """B 端伙伴的学习 / 合作意向登记。"""

    __tablename__ = "partner_leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product: Mapped[str] = mapped_column(String(32), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("client_users.id"), index=True)
    name: Mapped[str] = mapped_column(String(32))
    org_type: Mapped[str] = mapped_column(String(32))
    city: Mapped[str] = mapped_column(String(32), default="")
    contact: Mapped[str] = mapped_column(String(64))
    interest: Mapped[str] = mapped_column(String(16), default="learn")  # learn 想学习 / cooperate 想谈合作
    note: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(16), default="new")  # new / contacted / closed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
