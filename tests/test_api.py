import json
from pathlib import Path

from conftest import h

STARTER = Path(__file__).resolve().parent.parent / "products/sleep/knowledge/starter_cards.jsonl"


def load_starter(client, tokens):
    cards = [json.loads(line) for line in STARTER.read_text(encoding="utf-8").splitlines() if line.strip()]
    r = client.post("/api/knowledge", json=cards, headers=h(tokens["admin"]))
    assert r.status_code == 200
    return r.json()


def test_requires_login(client):
    assert client.get("/api/meta").status_code == 401
    assert client.get("/api/meta", headers=h("wrong")).status_code == 401


def test_meta(client, tokens):
    r = client.get("/api/meta", headers=h(tokens["operator"]))
    assert r.status_code == 200
    data = r.json()
    assert data["product"]["id"] == "sleep"
    assert {c["id"] for c in data["channels"]} >= {"xiaohongshu", "dianping", "douyin_script"}


def test_knowledge_import_is_idempotent(client, tokens):
    first = load_starter(client, tokens)
    assert first["added"] > 0 and first["skipped"] == 0
    second = load_starter(client, tokens)
    assert second["added"] == 0


def test_only_admin_adds_knowledge(client, tokens):
    r = client.post("/api/knowledge", json=[{"topic": "t", "points": "p"}], headers=h(tokens["reviewer"]))
    assert r.status_code == 403


def test_generate_uses_knowledge_and_adds_disclaimer(client, tokens, fake_llm):
    load_starter(client, tokens)
    fake_llm.replies = ["下班后来店里放松一下，灯光柔和。"]
    r = client.post("/api/generate", headers=h(tokens["operator"]), json={
        "channel": "xiaohongshu", "audience": "c_local", "content_type": "seeding", "brief": "肩颈放松"})
    assert r.status_code == 200, r.text
    g = r.json()
    assert g["status"] == "pending" and g["compliance"]["passed"] and g["rewrites"] == 0
    assert "不能替代医疗诊治" in g["output"]
    prompt = fake_llm.calls[0][1]["content"]
    assert "参考资料" in prompt and "肩颈放松" in prompt


def test_b2b_content_has_no_disclaimer(client, tokens, fake_llm):
    fake_llm.replies = ["欢迎了解加盟。"]
    r = client.post("/api/generate", headers=h(tokens["operator"]), json={
        "channel": "wechat_moments", "audience": "b_franchise", "content_type": "franchise"})
    assert "不能替代医疗诊治" not in r.json()["output"]


def test_blocked_draft_is_rewritten(client, tokens, fake_llm):
    fake_llm.replies = ["我们的手法疗效显著，根治失眠。", "下班来放松一下，睡个好觉。"]
    r = client.post("/api/generate", headers=h(tokens["operator"]), json={
        "channel": "xiaohongshu", "audience": "c_local", "content_type": "seeding"})
    g = r.json()
    assert g["rewrites"] == 1 and g["compliance"]["passed"]
    assert "疗效" in fake_llm.calls[1][-1]["content"]


def test_still_blocked_after_rewrite_cannot_be_approved(client, tokens, fake_llm):
    fake_llm.replies = ["疗效显著", "依然疗效显著"]
    g = client.post("/api/generate", headers=h(tokens["operator"]), json={
        "channel": "xiaohongshu", "audience": "c_local", "content_type": "seeding"}).json()
    assert not g["compliance"]["passed"]
    r = client.post(f"/api/generations/{g['id']}/review", json={"action": "approve"}, headers=h(tokens["reviewer"]))
    assert r.status_code == 409

    # 人工修改后重新检测，再通过
    r = client.patch(f"/api/generations/{g['id']}", json={"edited_output": "来店里放松一下"},
                     headers=h(tokens["operator"]))
    assert r.json()["compliance"]["passed"]
    r = client.post(f"/api/generations/{g['id']}/review", json={"action": "approve"}, headers=h(tokens["reviewer"]))
    assert r.status_code == 200 and r.json()["status"] == "approved"


def test_review_and_export_flow(client, tokens, fake_llm):
    fake_llm.replies = ["来店里放松一下。"]
    g = client.post("/api/generate", headers=h(tokens["operator"]), json={
        "channel": "dianping", "audience": "c_local", "content_type": "group_buy"}).json()

    assert client.get(f"/api/generations/{g['id']}/export", headers=h(tokens["operator"])).status_code == 409
    assert client.post(f"/api/generations/{g['id']}/review", json={"action": "approve"},
                       headers=h(tokens["operator"])).status_code == 403

    client.post(f"/api/generations/{g['id']}/review", json={"action": "approve"}, headers=h(tokens["reviewer"]))
    text = client.get(f"/api/generations/{g['id']}/export", headers=h(tokens["operator"])).json()["text"]
    assert "AI 辅助生成" in text

    r = client.patch(f"/api/generations/{g['id']}", json={"edited_output": "改一下"}, headers=h(tokens["operator"]))
    assert r.status_code == 409


def test_operator_sees_only_own(client, tokens, fake_llm):
    client.post("/api/generate", headers=h(tokens["reviewer"]), json={
        "channel": "dianping", "audience": "c_local", "content_type": "group_buy"})
    assert client.get("/api/generations", headers=h(tokens["operator"])).json() == []
    assert len(client.get("/api/generations", headers=h(tokens["reviewer"])).json()) == 1


def test_invalid_channel(client, tokens):
    r = client.post("/api/generate", headers=h(tokens["operator"]), json={
        "channel": "weibo", "audience": "c_local", "content_type": "seeding"})
    assert r.status_code == 400


def test_deactivate_user_revokes_token(client, tokens):
    users = client.get("/api/users", headers=h(tokens["admin"])).json()
    op = next(u for u in users if u["role"] == "operator")
    r = client.post(f"/api/users/{op['id']}/deactivate", headers=h(tokens["admin"]))
    assert r.status_code == 200
    assert client.get("/api/me", headers=h(tokens["operator"])).status_code == 401


def test_cannot_deactivate_last_admin(client, tokens):
    users = client.get("/api/users", headers=h(tokens["admin"])).json()
    admin = next(u for u in users if u["role"] == "admin")
    r = client.post(f"/api/users/{admin['id']}/deactivate", headers=h(tokens["admin"]))
    assert r.status_code == 400

    # 先建新管理员，再停用旧的
    new_token = client.post("/api/users", json={"name": "新管理员", "role": "admin"},
                            headers=h(tokens["admin"])).json()["token"]
    assert client.post(f"/api/users/{admin['id']}/deactivate", headers=h(new_token)).status_code == 200
    assert client.get("/api/me", headers=h(tokens["admin"])).status_code == 401


def test_admin_creates_user(client, tokens):
    r = client.post("/api/users", json={"name": "小王", "role": "operator"}, headers=h(tokens["admin"]))
    token = r.json()["token"]
    assert client.get("/api/me", headers=h(token)).json()["name"] == "小王"
