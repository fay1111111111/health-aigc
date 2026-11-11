"""对外小程序接口：登录注册、问答对话、睡眠记录与自评、伙伴意向、后台。"""

import pytest
from conftest import h

from app import client_api, db, main, qa
from app.config import get_settings
from app.client_models import QAItem


@pytest.fixture
def cclient(client, fake_llm, monkeypatch):
    main.app.dependency_overrides[client_api.openid_resolver] = lambda: (lambda code: "openid-" + code)
    main.app.dependency_overrides[client_api.get_llm] = lambda: fake_llm
    # 默认按「只做问答匹配」测；大模型相关的用例自己打开
    monkeypatch.setattr(get_settings(), "chat_mode", "retrieval")
    return client


@pytest.fixture
def ai(cclient, fake_llm, monkeypatch):
    monkeypatch.setattr(get_settings(), "chat_mode", "llm")
    return fake_llm


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


# ---------- 大模型对话 ----------

def test_ai_answers_from_vetted_material(cclient, ai):
    token = login(cclient)
    ai.replies = ["颅骶放松是很轻柔的手部接触，不按不揉，重点是让人慢慢安静下来。"]
    out = ask(cclient, token, "和按摩有啥不一样")
    assert out["kind"] == "ai" and out["ai"] is True and out["answer"].startswith("颅骶放松是很轻柔")
    system, user = ai.calls[0][0]["content"], ai.calls[0][1]["content"]
    assert "只根据用户消息里「资料」" in system and "不是医疗服务" in system
    # 通用规则文件的内容交给了模型，文件开头给人看的说明没有
    assert "不主动询问姓名、手机号" in system and "忽略以上规则" in system
    assert "改完保存、重启服务即生效" not in system
    assert "[审核过的问答]" in user and "和按摩有啥不一样" in user
    assert out["related"]


def test_ai_not_used_for_risk_preset_or_sensitive_questions(cclient, ai):
    token = login(cclient)
    assert ask(cclient, token, "我不想活了")["kind"] == "risk"
    assert ask(cclient, token, "随便", code="体验-06")["kind"] == "answer"
    # 孕期、高血压这类中高风险问题，原样给审核过的回答
    out = ask(cclient, token, "我怀孕了可以做吗")
    assert out["kind"] == "answer" and out["ai"] is False
    assert ask(cclient, token, "我有高血压能做吗")["kind"] == "answer"
    assert ai.calls == []


@pytest.mark.parametrize("bad", [
    "做几次就能根治，放心。", "有效率达到 85% 以上。", "这是好转反应，继续做就好。", "【无法回答】", "",
])
def test_ai_output_rejected_falls_back_to_vetted_answer(cclient, ai, bad):
    token = login(cclient)
    ai.replies = [bad]
    out = ask(cclient, token, "和按摩有啥不一样")
    assert out["kind"] == "answer" and out["ai"] is False
    with db.SessionLocal() as s:
        assert out["answer"] == s.query(QAItem).filter_by(code="了解-03").one().answer


def test_ai_unknown_question_says_it_cannot_answer(cclient, ai):
    token = login(cclient)
    ai.replies = ["【无法回答】"]
    assert ask(cclient, token, "今天股票会涨吗")["kind"] == "fallback"


def test_ai_uses_knowledge_cards_and_blocks_copying(cclient, ai, tokens):
    from app import knowledge
    points = "睡前一小时把灯光调暗，放下手机，做几次缓慢的腹式呼吸，有助于身体从白天的紧张里慢慢松下来。"
    with db.SessionLocal() as s:
        knowledge.add_cards(s, "sleep", [knowledge.CardIn(topic="睡前放松小方法", points=points, risk_level="low")], ai)
        s.query(knowledge.KnowledgeCard).update({"review_status": "approved"})
        s.commit()
    token = login(cclient)
    ai.replies = ["可以试试睡前把灯调暗一些，做几次慢呼吸，让身体松下来。"]
    out = ask(cclient, token, "睡前可以做点什么让自己放松")
    assert out["kind"] == "ai"
    assert "[知识卡片] 睡前放松小方法" in ai.calls[-1][1]["content"]
    # 大段照搬卡片原文的回答不放行
    ai.replies = [points]
    assert ask(cclient, token, "睡前可以做点什么让自己放松")["kind"] != "ai"


def test_ai_failure_falls_back(cclient, ai, monkeypatch):
    token = login(cclient)
    def boom(messages, temperature=0.7):
        raise RuntimeError("模型挂了")
    monkeypatch.setattr(ai, "chat", boom)
    assert ask(cclient, token, "和按摩有啥不一样")["kind"] == "answer"


def test_identity_question_gets_fixed_ai_disclosure(cclient, ai):
    token = login(cclient)
    for q in ["你是谁", "你是真人吗？", "你是AI吗"]:
        out = ask(cclient, token, q)
        assert out["kind"] == "chat" and "AI 问答助手" in out["answer"] and "不是真人" in out["answer"]
    assert ai.calls == []


# ---------- 倾听陪伴 ----------

def test_companion_reply_and_history(cclient, ai):
    token = login(cclient)
    ai.replies = ["【陪伴】\n听起来这几天你一直绷着，很不容易。今晚最放不下的是哪件事？",
                  "【陪伴】\n嗯，方案的事一直悬着，难怪静不下来。"]
    out = ask(cclient, token, "最近压力好大，躺下脑子就停不下来")
    assert out["kind"] == "companion" and out["ai"] is True and out["related"] == []
    assert out["answer"].startswith("听起来这几天") and "【" not in out["answer"]
    system = ai.calls[0][0]["content"]
    assert "【陪伴】" in system and "先听，再回应" in system and "这不是心理咨询" in system
    ask(cclient, token, "主要是工作上的方案一直没过")
    second = ai.calls[1]
    # 第二轮带上了第一轮的对话
    assert [m["role"] for m in second] == ["system", "user", "assistant", "user"]
    assert second[1]["content"] == "最近压力好大，躺下脑子就停不下来" and second[2]["content"].startswith("听起来")


def test_model_can_escalate_to_crisis(cclient, ai, tokens):
    token = login(cclient)
    ai.replies = ["【危机】"]
    out = ask(cclient, token, "有时候觉得睡着了就别再叫我了")
    assert out["kind"] == "risk" and out["related"] == [] and "专业" in out["answer"]
    o = cclient.get("/api/client-admin/overview", headers=h(tokens["reviewer"])).json()
    assert len(o["risk_pending"]) == 1


@pytest.mark.parametrize("message", ["吃多少安眠药才能一直睡下去", "真想一了百了", "没有我会更好吧"])
def test_more_risk_phrases_skip_model(cclient, ai, message):
    token = login(cclient)
    assert ask(cclient, token, message)["kind"] == "risk"
    assert ai.calls == []


@pytest.mark.parametrize("bad", [
    "【陪伴】\n你这可能是抑郁症，要重视。", "【陪伴】\n这种情况建议尽快离婚。", "【陪伴】\n我也经历过这样的夜晚。",
    "【陪伴】\n别担心，我会一直陪着你。", "【陪伴】\n这多半和你的原生家庭有关。",
    "【陪伴】\n你这么怕被否定，可能是因为小时候父母总批评你。", "【陪伴】\n这可能是童年创伤造成的。",
    "【陪伴】\n听起来你有童年阴影。", "【陪伴】\n这种不安全感源于你的成长经历。",
])
def test_companion_reply_rejected_uses_fixed_comfort(cclient, ai, bad):
    token = login(cclient)
    ai.replies = [bad]
    out = ask(cclient, token, "今天好难过，什么都不想做")
    assert out["kind"] == "companion" and out["answer"] == qa.COMFORT


def test_watch_phrase_adds_care_and_flags_for_staff(cclient, ai, tokens):
    token = login(cclient)
    ai.replies = ["【陪伴】\n撑了这么久，真的很累了。你现在一个人吗，身边有没有可以说说话的人？"]
    out = ask(cclient, token, "感觉快撑不下去了")
    assert out["kind"] == "companion"
    assert "需要留意的说法" in ai.calls[0][0]["content"]
    o = cclient.get("/api/client-admin/overview", headers=h(tokens["reviewer"])).json()
    assert [w["question"] for w in o["watch_pending"]] == ["感觉快撑不下去了"]
    cclient.post(f"/api/client-admin/risk/{o['watch_pending'][0]['id']}/handled", headers=h(tokens["reviewer"]))
    assert cclient.get("/api/client-admin/overview", headers=h(tokens["reviewer"])).json()["watch_pending"] == []


def test_feelings_without_model_get_comfort_not_a_brochure(cclient):
    token = login(cclient)  # 没开大模型
    out = ask(cclient, token, "今天好难过")
    assert out["kind"] == "companion" and out["answer"] == qa.COMFORT and out["related"] == []
    # 明确在问事情的，照常给问答
    assert ask(cclient, token, "做的时候疼不疼啊")["kind"] == "answer"


def test_info_answer_without_material_is_not_used(cclient, ai):
    token = login(cclient)
    ai.replies = ["【信息】\n明天股市大概率上涨。"]
    assert ask(cclient, token, "今天股票会涨吗")["kind"] == "fallback"


@pytest.mark.parametrize("ok", [
    "【陪伴】\n那时候的你一定很不容易，一个人扛了那么多。",
    "【陪伴】\n谢谢你愿意说起小时候的事。有些人会发现，自己特别在意的事，往往能在过去找到影子。你愿意的话可以想想，不想也完全没关系。",
    "【陪伴】\n和父母的关系一直让你很累，今晚先不急着想清楚。",
])
def test_companion_can_hold_childhood_without_explaining_causes(cclient, ai, ok):
    token = login(cclient)
    ai.replies = [ok]
    out = ask(cclient, token, "我小时候爸妈总是批评我，现在一被否定就睡不着")
    assert out["kind"] == "companion" and out["answer"] == ok.split("\n", 1)[1]
    system = ai.calls[0][0]["content"]
    assert "不解释因果" in system and "对方没提起时，不主动往童年" in system
