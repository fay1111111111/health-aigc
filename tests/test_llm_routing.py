from app.config import Settings
from app.llm import RoutedLLM


def test_deepseek_goes_through_bailian_with_same_key():
    llm = RoutedLLM(Settings(dashscope_api_key="sk-bailian", deepseek_api_key=""))
    ds, qw = llm.providers["deepseek"], llm.providers["qwen"]
    assert ds.model == "deepseek-v4-flash" and ds.client is qw.client
    assert str(ds.client.base_url).startswith("https://dashscope.aliyuncs.com")
    assert ds.extra_body == {"enable_thinking": False} and qw.extra_body is None


def test_official_deepseek_key_takes_precedence():
    llm = RoutedLLM(Settings(dashscope_api_key="sk-bailian", deepseek_api_key="sk-ds"))
    ds = llm.providers["deepseek"]
    assert str(ds.client.base_url).startswith("https://api.deepseek.com") and ds.extra_body is None


def test_fallback_used_when_primary_fails():
    llm = RoutedLLM(Settings(dashscope_api_key="sk-bailian", deepseek_api_key=""))

    class Boom:
        name, model = "qwen", "qwen-plus"

        def chat(self, *a):
            raise RuntimeError("down")

    class Ok:
        name, model = "deepseek", "deepseek-v4-flash"

        def chat(self, *a):
            from app.llm import ChatResult
            return ChatResult(text="好", model="deepseek:deepseek-v4-flash")

    llm.providers = {"qwen": Boom(), "deepseek": Ok()}
    assert llm.chat([{"role": "user", "content": "hi"}]).model == "deepseek:deepseek-v4-flash"
