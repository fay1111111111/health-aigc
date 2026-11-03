"""产品线与渠道配置。

底座代码不写死任何产品：每条产品线是 products/<id>/ 下的一套配置、知识和样例，
渠道规范放在 channels/ 下，所有产品线共用。新增产品线只需加一个文件夹。
"""

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from .config import get_settings


class Option(BaseModel):
    id: str
    name: str
    description: str = ""


class ProductCompliance(BaseModel):
    extra_block: list[str] = Field(default_factory=list)
    extra_warn: list[str] = Field(default_factory=list)
    disclaimer: str = ""
    disclaimer_audiences: list[str] = Field(default_factory=list)  # 这些受众的内容自动附免责声明


class Product(BaseModel):
    id: str
    name: str
    description: str
    brand_voice: str = ""
    audiences: list[Option]
    content_types: list[Option]
    channels: list[str]
    compliance: ProductCompliance = ProductCompliance()
    ai_label: str = "（本内容由 AI 辅助生成，已经人工审核）"
    # 营销内容生成时最多参考到哪个风险等级的知识卡片（low / mid / high）。
    # 高风险卡片（疾病、药物、孕婴等）仍保留在知识库里，供查询和以后的对话产品使用。
    generation_max_risk: str = "mid"

    def audience(self, audience_id: str) -> Option:
        return _pick(self.audiences, audience_id, "受众")

    def content_type(self, type_id: str) -> Option:
        return _pick(self.content_types, type_id, "内容类型")


class Channel(BaseModel):
    id: str
    name: str
    length: str
    format_rules: list[str]


class Example(BaseModel):
    channel: str
    content_type: str
    text: str


def _pick(options: list[Option], option_id: str, label: str) -> Option:
    for option in options:
        if option.id == option_id:
            return option
    raise KeyError(f"未知{label}：{option_id}")


def _load_yaml(path: Path):
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


@lru_cache
def load_product(product_id: str | None = None) -> Product:
    settings = get_settings()
    product_id = product_id or settings.product
    return Product(**_load_yaml(settings.products_dir / product_id / "product.yaml"))


@lru_cache
def load_channels() -> dict[str, Channel]:
    settings = get_settings()
    channels = [Channel(**_load_yaml(p)) for p in sorted(settings.channels_dir.glob("*.yaml"))]
    return {c.id: c for c in channels}


def product_channels(product: Product) -> list[Channel]:
    all_channels = load_channels()
    missing = [c for c in product.channels if c not in all_channels]
    if missing:
        raise KeyError(f"产品线 {product.id} 引用了不存在的渠道：{missing}")
    return [all_channels[c] for c in product.channels]


@lru_cache
def load_examples(product_id: str) -> list[Example]:
    path = get_settings().products_dir / product_id / "examples.yaml"
    if not path.exists():
        return []
    return [Example(**e) for e in (_load_yaml(path) or [])]
