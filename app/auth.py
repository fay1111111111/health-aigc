"""内部账号：每人一个令牌，按角色控制权限。"""

import hashlib
import secrets

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_session
from .models import User

ROLES = ("operator", "reviewer", "admin")
_RANK = {r: i for i, r in enumerate(ROLES)}


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_user(session: Session, name: str, role: str) -> tuple[User, str]:
    if role not in ROLES:
        raise ValueError(f"角色只能是 {ROLES}")
    token = secrets.token_urlsafe(24)
    user = User(name=name, role=role, token_hash=hash_token(token))
    session.add(user)
    session.commit()
    return user, token


def current_user(authorization: str = Header(default=""), session: Session = Depends(get_session)) -> User:
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(401, "缺少登录令牌")
    user = session.scalar(select(User).where(User.token_hash == hash_token(token), User.active.is_(True)))
    if user is None:
        raise HTTPException(401, "令牌无效或账号已停用")
    return user


def require(role: str):
    def checker(user: User = Depends(current_user)) -> User:
        if _RANK[user.role] < _RANK[role]:
            raise HTTPException(403, "没有权限")
        return user

    return checker
