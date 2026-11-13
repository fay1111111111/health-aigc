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

    # DeepSeek：默认通过百炼调用（用同一个 DASHSCOPE_API_KEY，不用另开账号）
    bailian_deepseek_model: str = "deepseek-v4.1-flash"
    # 只有填了 DEEPSEEK_API_KEY 才改走 DeepSeek 官网接口；模型名以官网文档为准
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_chat_model: str = "deepseek-v4-flash"

    llm_primary: str = "qwen"
    llm_fallback: str = "deepseek"
    llm_timeout_seconds: float = 60

    # 检索与合规
    retrieval_top_k: int = 5
    copy_ngram: int = 12  # 与知识库原文连续重合这么多个字，判定为疑似照搬
    max_rewrite_attempts: int = 1  # 命中禁用内容时自动改写的次数

    # 自动入库
    ingest_max_chunks: int = 40  # 每条资料最多处理多少段（控制成本）
    # 和已有卡片的向量相似度超过这个值视为重复。可在 .env 用 DEDUP_SIMILARITY 调整：
    # 调低去重更狠（可能误伤相近但不同的知识点），调高更宽松（可能留下换了说法的重复）
    # 实测（text-embedding-v3）：0.94 以上是同一知识点；0.85～0.88 是换了说法的重复；0.80 左右只是部分重叠
    dedup_similarity: float = 0.85

    # 对外小程序（药王山沁养）：AppID 和密钥在微信公众平台「开发管理」里查看，只写在服务器 .env
    wx_appid: str = ""
    wx_secret: str = ""
    wx_dev_login: bool = False  # 只在本地联调时打开：没配置 AppID 也能登录测试账号
    client_session_days: int = 30
    # 对话方式：llm = 大模型根据审核过的问答和知识库组织回答；retrieval = 只做问答匹配，不调用大模型
    chat_mode: str = "llm"
    chat_knowledge_top_k: int = 4
    upload_dir: Path = ROOT / "uploads"  # 用户头像

    products_dir: Path = ROOT / "products"
    channels_dir: Path = ROOT / "channels"
    compliance_dir: Path = ROOT / "compliance"


@lru_cache
def get_settings() -> Settings:
    return Settings()
