from app import compliance
from app.products import load_product


def test_clean_text_passes():
    r = compliance.check("下班后来店里放松一下，灯光柔和，音乐很轻。", load_product())
    assert r["passed"] and r["issues"] == []


def test_base_block_word():
    r = compliance.check("我们的手法疗效显著", load_product())
    assert not r["passed"]
    assert "疗效" in compliance.blocking_terms(r)


def test_product_block_word():
    r = compliance.check("坚持一个月，帮你根治失眠", load_product())
    assert "根治失眠" in compliance.blocking_terms(r)


def test_allowed_phrase_not_flagged():
    r = compliance.check("第一次来店里，技师先聊了聊作息", load_product())
    assert r["passed"]


def test_warn_does_not_block():
    r = compliance.check("睡前最好放下手机", load_product())
    assert r["passed"]
    assert [i["term"] for i in r["issues"]] == ["最好"]


def test_copy_detection():
    ref = "每天尽量在相近的时间上床和起床，周末也不要差太多。"
    text = "小建议：每天尽量在相近的时间上床和起床，这样身体会更舒服。"
    r = compliance.check(text, load_product(), [ref])
    assert not r["passed"]
    assert any(i["kind"] == "copy" for i in r["issues"])


def test_paraphrase_not_flagged_as_copy():
    ref = "每天尽量在相近的时间上床和起床，周末也不要差太多。"
    text = "作息尽量固定下来，周末也别一下子睡到中午。"
    assert compliance.check(text, load_product(), [ref])["passed"]
