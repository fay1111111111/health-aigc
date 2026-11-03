"""模型调用层。

千问和 DeepSeek 都走 OpenAI 兼容接口，统一成同一个调用方式：
主模型失败时自动切到备用模型；向量化固定用百炼的 embedding 模型。
"""

import logging
from dataclasses import dataclass
from typing import Protocol

from openai import OpenAI

from .config import Settings, get_settings

log = logging.getLogger(__name__)

EMBED_BATCH = 10  # 百炼 embedding 接口单次最多 10 条


@dataclass
class ChatResult:
    text: str
    model: str


class LLM(Protocol):
    def chat(self, messages: list[dict], temperature: float = 0.7) -> ChatResult: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class LLMError(RuntimeError):
    pass


@dataclass
class _Provider:
    name: str
    client: OpenAI
    model: str

    def chat(self, messages: list[dict], temperature: float) -> ChatResult:
        resp = self.client.chat.completions.create(model=self.model, messages=messages, temperature=temperature)
        return ChatResult(text=(resp.choices[0].message.content or "").strip(), model=f"{self.name}:{self.model}")


class RoutedLLM:
    def __init__(self, settings: Settings | None = None):
        s = settings or get_settings()
        self.settings = s
        self.providers: dict[str, _Provider] = {}
        if s.dashscope_api_key:
            self.providers["qwen"] = _Provider(
                "qwen", OpenAI(api_key=s.dashscope_api_key, base_url=s.dashscope_base_url, timeout=s.llm_timeout_seconds),
                s.dashscope_chat_model)
        if s.deepseek_api_key:
            self.providers["deepseek"] = _Provider(
                "deepseek", OpenAI(api_key=s.deepseek_api_key, base_url=s.deepseek_base_url, timeout=s.llm_timeout_seconds),
                s.deepseek_chat_model)

    def chat(self, messages: list[dict], temperature: float = 0.7) -> ChatResult:
        order = [p for p in (self.settings.llm_primary, self.settings.llm_fallback) if p in self.providers]
        if not order:
            raise LLMError("没有可用的模型：请在 .env 中填写 DASHSCOPE_API_KEY 或 DEEPSEEK_API_KEY")
        errors = []
        for name in dict.fromkeys(order):
            try:
                return self.providers[name].chat(messages, temperature)
            except Exception as e:  # noqa: BLE001  任一家失败都尝试下一家
                log.warning("模型 %s 调用失败：%s", name, e)
                errors.append(f"{name}: {e}")
        raise LLMError("所有模型调用均失败：" + "；".join(errors))

    def embed(self, texts: list[str]) -> list[list[float]]:
        provider = self.providers.get("qwen")
        if provider is None:
            raise LLMError("向量化需要百炼：请在 .env 中填写 DASHSCOPE_API_KEY")
        vectors: list[list[float]] = []
        for i in range(0, len(texts), EMBED_BATCH):
            resp = provider.client.embeddings.create(
                model=self.settings.embedding_model,
                input=texts[i:i + EMBED_BATCH],
                dimensions=self.settings.embedding_dim,
            )
            vectors.extend(d.embedding for d in sorted(resp.data, key=lambda d: d.index))
        return vectors


_llm: LLM | None = None


def get_llm() -> LLM:
    global _llm
    if _llm is None:
        _llm = RoutedLLM()
    return _llm
