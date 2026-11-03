"""合规检测：禁用词、医疗用语、疑似照搬知识库原文。"""

import re
from dataclasses import asdict, dataclass
from functools import lru_cache

import yaml

from .config import get_settings
from .products import Product

_NON_WORD = re.compile(r"[\s\W_]+", re.UNICODE)


@dataclass
class Issue:
    level: str  # block / warn
    kind: str  # word / copy
    term: str
    message: str


@lru_cache
def base_rules() -> dict:
    with (get_settings().compliance_dir / "rules.yaml").open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return {k: list(data.get(k) or []) for k in ("block", "warn", "allow")}


def _mask_allowed(text: str, allow: list[str]) -> str:
    for phrase in sorted(allow, key=len, reverse=True):
        text = text.replace(phrase, "□" * len(phrase))
    return text


def _normalize(text: str) -> str:
    return _NON_WORD.sub("", text)


def _copied_spans(text: str, reference: str, n: int) -> list[str]:
    """找出 text 中与 reference 连续重合 n 个字以上的片段。"""
    t, r = _normalize(text), _normalize(reference)
    if len(t) < n or len(r) < n:
        return []
    ref_grams = {r[i:i + n] for i in range(len(r) - n + 1)}
    spans, i = [], 0
    while i <= len(t) - n:
        if t[i:i + n] in ref_grams:
            j = i + n
            while j < len(t) and t[i:j + 1] in r:
                j += 1
            spans.append(t[i:j])
            i = j
        else:
            i += 1
    return spans


def check(text: str, product: Product, references: list[str] | None = None) -> dict:
    rules = base_rules()
    block = rules["block"] + product.compliance.extra_block
    warn = rules["warn"] + product.compliance.extra_warn
    masked = _mask_allowed(text, rules["allow"])

    issues: list[Issue] = []
    for term in dict.fromkeys(block):
        if term in masked:
            issues.append(Issue("block", "word", term, f"含禁用表述「{term}」"))
    for term in dict.fromkeys(warn):
        if term in masked and term not in block:
            issues.append(Issue("warn", "word", term, f"含需留意的表述「{term}」，请确认语境"))

    n = get_settings().copy_ngram
    for ref in references or []:
        for span in _copied_spans(text, ref, n):
            issues.append(Issue("block", "copy", span, f"与参考资料原文连续重合：「{span}」，需改写"))

    return {
        "passed": not any(i.level == "block" for i in issues),
        "issues": [asdict(i) for i in issues],
    }


def blocking_terms(result: dict) -> list[str]:
    return [i["term"] for i in result["issues"] if i["level"] == "block"]
