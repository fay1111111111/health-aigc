"""内容生成引擎：产品线 × 渠道 × 受众 × 内容类型。

流程：检索知识卡片 → 拼提示词 → 调模型 → 合规检测 → 命中禁用内容时自动改写 → 存档待审。
"""

from sqlalchemy.orm import Session

from . import compliance, knowledge
from .config import get_settings
from .llm import LLM
from .models import Generation, KnowledgeCard
from .products import Channel, Example, Product, load_channels, load_examples


class GenerationError(ValueError):
    pass


def build_messages(product: Product, channel: Channel, audience_id: str, content_type_id: str,
                   brief: str, cards: list[KnowledgeCard], examples: list[Example]) -> list[dict]:
    audience = product.audience(audience_id)
    ctype = product.content_type(content_type_id)
    rules = compliance.base_rules()
    banned = "、".join(dict.fromkeys(rules["block"] + product.compliance.extra_block))

    system = "\n".join([
        f"你是「{product.name}」的资深内容策划，为线上线下各渠道撰写营销文字。",
        f"业务说明：{product.description}",
        f"品牌语气：{product.brand_voice}",
        "",
        "必须遵守：",
        "1. 这是养生保健服务，不是医疗服务。不得宣称治疗、治愈任何疾病，不得使用医疗诊疗用语，不得承诺效果。",
        "2. 不得使用广告法禁止的绝对化用语。",
        f"3. 下列表述一律不能出现：{banned}。",
        "4. 参考资料只作为事实依据，必须用自己的话重新表达，不能照抄其中的句子。",
        "5. 价格、地址、电话、门店数量、顾客数量等具体信息，只能使用用户在补充要点里给出的，不得编造。",
        "6. 具体的服务项目、服务步骤、手法名称、穴位名称，只能使用补充要点或参考资料里出现的；没有给出的就用概括的说法（如“从肩颈开始慢慢放松”），不得自行编造。",
        "7. 直接输出成稿，不要解释你的写作思路。",
    ])

    parts = [
        f"渠道：{channel.name}（篇幅：{channel.length}）",
        "格式要求：\n" + "\n".join(f"- {r}" for r in channel.format_rules),
        f"目标受众：{audience.name}。{audience.description}",
        f"内容类型：{ctype.name}",
    ]
    if brief.strip():
        parts.append(f"补充要点：{brief.strip()}")
    if cards:
        parts.append("参考资料（事实依据，用自己的话表达）：\n" +
                     "\n".join(f"- {c.topic}：{c.points}" for c in cards))
    picked = [e for e in examples if e.channel == channel.id][:2]
    if picked:
        parts.append("风格参考（学习结构和语气，不要复用句子）：\n" +
                     "\n---\n".join(e.text for e in picked))

    return [{"role": "system", "content": system}, {"role": "user", "content": "\n\n".join(parts)}]


def rewrite_messages(messages: list[dict], draft: str, result: dict) -> list[dict]:
    problems = "\n".join(f"- {i['message']}" for i in result["issues"] if i["level"] == "block")
    return messages + [
        {"role": "assistant", "content": draft},
        {"role": "user", "content": f"这篇稿子有以下问题，请修改后重新输出完整成稿，保持原有结构和篇幅：\n{problems}"},
    ]


def with_disclaimer(text: str, product: Product, audience_id: str) -> str:
    c = product.compliance
    if c.disclaimer and audience_id in c.disclaimer_audiences and c.disclaimer not in text:
        return f"{text}\n\n{c.disclaimer}"
    return text


def generate(session: Session, llm: LLM, product: Product, user_id: int, channel_id: str,
             audience_id: str, content_type_id: str, brief: str = "") -> Generation:
    settings = get_settings()
    if channel_id not in product.channels:
        raise GenerationError(f"产品线 {product.id} 未启用渠道：{channel_id}")
    try:
        product.audience(audience_id)
        product.content_type(content_type_id)
    except KeyError as e:
        raise GenerationError(str(e)) from e
    channel = load_channels()[channel_id]

    query = " ".join([product.content_type(content_type_id).name, product.audience(audience_id).name, brief])
    cards = knowledge.search(session, product.id, query, llm, k=settings.retrieval_top_k,
                             max_risk=product.generation_max_risk)
    references = [c.points for c in cards]

    messages = build_messages(product, channel, audience_id, content_type_id, brief, cards, load_examples(product.id))
    result = llm.chat(messages)
    text, model = result.text, result.model
    check = compliance.check(text, product, references)

    rewrites = 0
    while not check["passed"] and rewrites < settings.max_rewrite_attempts:
        rewrites += 1
        result = llm.chat(rewrite_messages(messages, text, check), temperature=0.5)
        text, model = result.text, result.model
        check = compliance.check(text, product, references)

    text = with_disclaimer(text, product, audience_id)
    row = Generation(
        product=product.id, channel=channel_id, audience=audience_id, content_type=content_type_id,
        brief=brief, output=text, compliance=check, card_ids=[c.id for c in cards],
        model_used=model, rewrites=rewrites, created_by=user_id,
    )
    session.add(row)
    session.commit()
    return row


def recheck(session: Session, product: Product, row: Generation) -> dict:
    """人工修改后重新检测。"""
    refs = []
    if row.card_ids:
        refs = [c.points for c in session.query(KnowledgeCard).filter(KnowledgeCard.id.in_(row.card_ids))]
    return compliance.check(row.final_text, product, refs)
