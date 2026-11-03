import hashlib
import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["EMBEDDING_DIM"] = "16"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import auth, db, main  # noqa: E402
from app.llm import ChatResult  # noqa: E402


class FakeLLM:
    """测试用模型：按队列返回预设回复；向量用文本哈希生成，同样文本得到同样向量。"""

    def __init__(self):
        self.replies: list[str] = []
        self.calls: list[list[dict]] = []

    def chat(self, messages, temperature=0.7):
        self.calls.append(messages)
        text = self.replies.pop(0) if self.replies else "一篇合规的放松分享文案。"
        return ChatResult(text=text, model="fake:test")

    def embed(self, texts):
        out = []
        for t in texts:
            h = hashlib.sha256(t.encode()).digest()
            out.append([b / 255 for b in h[:16]])
        return out


@pytest.fixture
def fake_llm():
    return FakeLLM()


@pytest.fixture
def client(fake_llm):
    db.Base.metadata.drop_all(db.engine)
    db.init_db()
    main.app.dependency_overrides[main.llm_dep] = lambda: fake_llm
    with TestClient(main.app) as c:
        yield c
    main.app.dependency_overrides.clear()


@pytest.fixture
def tokens():
    out = {}
    with db.SessionLocal() as s:
        for role in auth.ROLES:
            _, out[role] = auth.create_user(s, f"{role}-user", role)
    return out


def h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}
