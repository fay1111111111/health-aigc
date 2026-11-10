"""对外小程序接口：登录注册、问答对话、睡眠记录与自评、伙伴意向、后台。"""

import pytest
from conftest import h

from app import client_api, db, main, qa
from app.client_models import QAItem


@pytest.fixture
def cclient(client):
    main.app.dependency_overrides[client_api.openid_resolver] = lambda: (lambda code: "openid-" + code)
    return client


def login(c, code="a", register=True):
    token = c.post("/api/c/login", json={"code": code}).json()["token"]
    if register:
        r = c.post("/api/c/register", json={"role": "self", "nickname": "小雨", "agree_privacy": True}, headers=h(token))
        assert r.status_code == 200
    return token


def ask(c, token, message, **extra):
    r = c.post("/api/c/chat", json={"message": message, **extra}, headers=h(token))
    assert r.status_code == 200, r.text
    return r.json()


def test_seed_loads_all_51(cclient):
    with db.SessionLocal() as s:
        assert s.query(QAItem).count() == 51
        assert qa.seed(s, "sleep") == 0  # 重复导入不新增


def test_login_and_register(cclient):
    assert cclient.get("/api/c/me").status_code == 401
    token = login(cclient, register=False)
    assert cclient.get("/api/c/me", headers=h(token)).json()["registered"] is False
    bad = cclient.post("/api/c/register", json={"role": "self", "agree_privacy": False}, headers=h(token))
    assert bad.status_code == 400
    ok = cclient.post("/api/c/register", json={"role": "partner", "nickname": "", "agree_privacy": True}, headers=h(token))
    assert ok.json() == {**ok.json(), "registered": True, "role": "partner", "nickname": "微信用户"}
    # 同一个微信号再次登录还是同一个账号
    again = cclient.post("/api/c/login", json={"code": "a"}).json()
    assert again["user"]["id"] == ok.json()["id"] and again["user"]["registered"]


def test_internal_token_cannot_use_client_api(cclient, tokens):
    assert cclient.get("/api/c/me", headers=h(tokens["admin"])).status_code == 401


@pytest.mark.parametrize("message,code", [
    ("颅骶疗法（CST）到底是什么？能简单说说吗？", "了解-02"),
    ("做的时候疼不疼啊", "了解-08"),
    ("多少钱一次", "体验-01"),
    ("我怀孕了可以做吗", "了解-15"),
    ("和按摩有啥不一样", "了解-03"),
    ("临时有事想改个时间", "售后-07"),
    ("能退款吗", "售后-08"),
    ("做完头有点晕", "售后-05"),
    ("我有高血压能做吗", "了解-09"),
    ("哪些人不能做", "了解-06"),
    ("做完没啥感觉", "售后-02"),
    ("你们在几楼", "体验-07"),
    ("学完能做什么", "B端-03"),
    ("零基础可以学吗", "B端-02"),
])
def test_chat_matches(cclient, message, code):
    token = login(cclient)
    out = ask(cclient, token, message)
    assert out["kind"] == "answer", out
    with db.SessionLocal() as s:
        item = s.query(QAItem).filter_by(code=code).one()
    assert out["answer"] == item.answer
    assert out["related"] and all(r["code"] != "售后-03" for r in out["related"])


def test_chat_by_code_and_unknown(cclient):
    token = login(cclient)
    assert ask(cclient, token, "随便", code="体验-06")["answer"].startswith("一般不需要宽衣")
    out = ask(cclient, token, "今天股票会涨吗")
    assert out["kind"] == "fallback" and out["related"]


@pytest.mark.parametrize("message", ["我不想活了", "有时候真想死", "感觉活 不 下 去了，想预约"])
def test_risk_signal_stops_everything(cclient, message):
    token = login(cclient)
    out = ask(cclient, token, message)
    assert out["kind"] == "risk" and out["related"] == []
    assert "专业" in out["answer"] and "预约" not in out["answer"].split("暂停")[0]


def test_feedback_and_history(cclient):
    token, other = login(cclient, "a"), login(cclient, "b")
    mid = ask(cclient, token, "疼不疼")["id"]
    assert cclient.post(f"/api/c/chat/{mid}/feedback", json={"helpful": False}, headers=h(other)).status_code == 404
    assert cclient.post(f"/api/c/chat/{mid}/feedback", json={"helpful": True}, headers=h(token)).status_code == 200
    hist = cclient.get("/api/c/chat/history", headers=h(token)).json()
    assert len(hist) == 1 and hist[0]["helpful"] is True
    assert cclient.get("/api/c/chat/history", headers=h(other)).json() == []


def test_home_hides_risk_and_splits_scenes(cclient):
    token = login(cclient)
    c = cclient.get("/api/c/home", headers=h(token)).json()["categories"]
    assert [x["id"] for x in c] == ["learn", "experience", "aftercare"]
    codes = [q["code"] for x in c for q in x["questions"]]
    assert "售后-03" not in codes and len(codes) == 42
    p = cclient.get("/api/c/home?scene=partner", headers=h(token)).json()["categories"]
    assert len(p) == 1 and len(p[0]["questions"]) == 8


def test_sleep_and_assessment_need_separate_consent(cclient):
    token = login(cclient)
    rec = {"day": "2026-11-09", "bedtime": "23:30", "wake_time": "07:00", "quality": 4}
    r = cclient.post("/api/c/sleep-records", json=rec, headers=h(token))
    assert r.status_code == 403 and r.json()["detail"] == "need_health_consent"
    cclient.post("/api/c/me/health-consent", json={"agree": True}, headers=h(token))
    r = cclient.post("/api/c/sleep-records", json=rec, headers=h(token))
    assert r.status_code == 200 and r.json()["minutes"] == 450
    # 同一天再记一次是覆盖
    cclient.post("/api/c/sleep-records", json={**rec, "quality": 2}, headers=h(token))
    rows = cclient.get("/api/c/sleep-records", headers=h(token)).json()
    assert len(rows) == 1 and rows[0]["quality"] == 2
    assert cclient.post("/api/c/sleep-records", json={**rec, "bedtime": "25:00"}, headers=h(token)).status_code == 422
    assert cclient.post("/api/c/sleep-records", json={**rec, "day": "2999-01-01"}, headers=h(token)).status_code == 400
    a = cclient.post("/api/c/assessments", json={"kind": "after", "relax": 5, "sleep": 4}, headers=h(token))
    assert a.status_code == 200
    assert cclient.post("/api/c/assessments", json={"kind": "after", "relax": 6, "sleep": 4}, headers=h(token)).status_code == 422
    # 撤回同意：记录一并删除
    cclient.post("/api/c/me/health-consent", json={"agree": False}, headers=h(token))
    assert cclient.get("/api/c/sleep-records", headers=h(token)).status_code == 403
    cclient.post("/api/c/me/health-consent", json={"agree": True}, headers=h(token))
    assert cclient.get("/api/c/sleep-records", headers=h(token)).json() == []
    assert cclient.get("/api/c/assessments", headers=h(token)).json() == []


def test_lead_and_admin_overview(cclient, tokens):
    token = login(cclient)
    lead = {"name": "陈老师", "org_type": "养生馆", "city": "广州", "contact": "wx: chen123", "interest": "learn"}
    assert cclient.post("/api/c/leads", json={**lead, "agree_contact": False}, headers=h(token)).status_code == 400
    assert cclient.post("/api/c/leads", json={**lead, "agree_contact": True}, headers=h(token)).status_code == 200
    ask(cclient, token, "今天股票会涨吗")
    rid = ask(cclient, token, "我想自杀")["id"]
    assert cclient.get("/api/client-admin/overview", headers=h(token)).status_code == 401
    assert cclient.get("/api/client-admin/overview", headers=h(tokens["operator"])).status_code == 403
    o = cclient.get("/api/client-admin/overview", headers=h(tokens["reviewer"])).json()
    assert o["users"] == 1 and len(o["leads"]) == 1 and len(o["unanswered"]) == 1
    assert [r["id"] for r in o["risk_pending"]] == [rid]
    cclient.post(f"/api/client-admin/risk/{rid}/handled", headers=h(tokens["reviewer"]))
    assert cclient.get("/api/client-admin/overview", headers=h(tokens["reviewer"])).json()["risk_pending"] == []


def test_admin_edits_qa_and_draft_is_hidden(cclient, tokens):
    token = login(cclient)
    rows = cclient.get("/api/client-admin/qa", headers=h(tokens["reviewer"])).json()
    item = next(r for r in rows if r["code"] == "了解-12")
    r = cclient.patch(f"/api/client-admin/qa/{item['id']}", json={"status": "draft"}, headers=h(tokens["reviewer"]))
    assert r.json()["status"] == "draft"
    assert ask(cclient, token, "那跟针灸比呢")["kind"] != "answer"
    risk = next(r for r in rows if r["code"] == "售后-03")
    r = cclient.patch(f"/api/client-admin/qa/{risk['id']}", json={"status": "draft"}, headers=h(tokens["reviewer"]))
    assert r.status_code == 400


def test_avatar_upload_and_delete_account(cclient, tmp_path, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "upload_dir", tmp_path)
    token = login(cclient)
    bad = cclient.post("/api/c/me/avatar", files={"file": ("a.txt", b"hi", "text/plain")}, headers=h(token))
    assert bad.status_code == 400
    ok = cclient.post("/api/c/me/avatar", files={"file": ("a.png", b"\x89PNG....", "image/png")}, headers=h(token))
    assert ok.json()["avatar_url"].startswith("/uploads/avatars/")
    assert len(list((tmp_path / "avatars").iterdir())) == 1
    ask(cclient, token, "疼不疼")
    assert cclient.delete("/api/c/me", headers=h(token)).status_code == 200
    assert list((tmp_path / "avatars").iterdir()) == []
    assert cclient.get("/api/c/me", headers=h(token)).status_code == 401
