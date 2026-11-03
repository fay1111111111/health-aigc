from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 本套部署服务的产品线，对应 products/<product>/
    product: str = "sleep"

    database_url: str = "postgresql+psycopg://aigc:aigc@db:5432/aigc"

    # 阿里云百炼（OpenAI 兼容接口）
    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    dashscope_chat_model: str = "qwen-plus"
    embedding_model: str = "text-embedding-v3"
    embedding_dim: int = 1024

    # DeepSeek（OpenAI 兼容接口）
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_chat_model: str = "deepseek-chat"

    llm_primary: str = "qwen"
    llm_fallback: str = "deepseek"
    llm_timeout_seconds: float = 60

    # 检索与合规
    retrieval_top_k: int = 5
    copy_ngram: int = 12  # 与知识库原文连续重合这么多个字，判定为疑似照搬
    max_rewrite_attempts: int = 1  # 命中禁用内容时自动改写的次数

    products_dir: Path = ROOT / "products"
    channels_dir: Path = ROOT / "channels"
    compliance_dir: Path = ROOT / "compliance"


@lru_cache
def get_settings() -> Settings:
    return Settings()
