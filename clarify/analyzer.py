"""把帖子版本的可见文本辨成带限定范围的说法。

设计原则：
- 只依赖标准库、规则可审计；每条规则都是显式词表，编辑可逐条核对。
- 原句原样保留，限定条件（发言者、地点、人群、时段、业务种类、数量/模态）
  结构化进 :class:`Scope`，不做语义改写。
- 转引、传闻、反问不提升为作者断言；作者断言与更正性陈述才是作者背书。
"""

from __future__ import annotations

import re

from .models import (
    Claim,
    SAY_ASSERTION,
    SAY_CORRECTION,
    SAY_HEARSAY,
    SAY_QUESTION,
    SAY_REFUTATION,
    Scope,
)

# 传闻来源标记：出现即说明该句是转述，作者未背书
_HEARSAY_MARKERS = ("消息称", "另说", "网传", "网帖", "朋友转发", "转述", "有读者问")
# 明确反驳 / 更正标记
_REFUTATION_MARKERS = ("不是", "没有查到", "没找到", "原意有误", "请勿", "澄清了")
_CORRECTION_MARKERS = ("改用", "表单变化", "新表", "仍开放", "只暂停", "仅暂停")
# 否定词（用于极性判断，不单独决定话语类型）
_NEGATION_WORDS = ("不", "没", "未", "非", "勿", "误")

# 限定词词表：在原句中命中即收入对应维度
_PLACE_WORDS = (
    "境外", "沿岸", "城区", "东岸", "西岸旧街", "西岸", "港区", "外地",
)
_POPULATION_WORDS = ("小微商户", "个人营业", "企业合并", "企业", "代办", "读者")
_BUSINESS_WORDS = (
    "合并备案", "营业备案", "批量资料", "备案", "换件", "自助柜", "柜台", "名单",
)
_PERIOD_WORDS = ("12 月", "12月", "下月", "永久", "12 月 5 日", "12月5日", "到期")
_MODALITY_WORDS = ("一律", "暂停", "照常", "关闭", "撤除", "开放", "停了", "停")

_CLAUSE_SPLIT = re.compile(r"[，。！？；;,!?]+")
_QUOTE_RE = re.compile(r"[「『\"]([^」』\"]+)[」』\"]")


def _normalize(text: str) -> str:
    return text.strip()


def _hit(text: str, words) -> frozenset[str]:
    return frozenset(word for word in words if word in text)


def extract_scope(text: str) -> Scope:
    """从原句抽取限定词；抽不到的维度留空表示“未限定/全范围”。"""
    compact = text.replace(" ", "")
    return Scope(
        places=_hit(text, _PLACE_WORDS),
        populations=_hit(text, _POPULATION_WORDS),
        businesses=_hit(compact, [w.replace(" ", "") for w in _BUSINESS_WORDS]),
        periods=_hit(compact, [w.replace(" ", "") for w in _PERIOD_WORDS]),
        modalities=_hit(text, _MODALITY_WORDS),
    )


def _speaker(text: str, kind: str) -> str:
    if kind == SAY_HEARSAY:
        for marker in _HEARSAY_MARKERS:
            if marker in text:
                return marker
        return "转述来源"
    center = re.search(r"(.{2,6}?)说", text)
    if center:
        return center.group(1)
    return "作者"


def _classify(clause: str) -> tuple[str, str, bool]:
    """返回 (话语类型, 极性, 是否引述内)。"""
    quoted = bool(_QUOTE_RE.search(clause))
    if any(marker in clause for marker in _HEARSAY_MARKERS):
        kind = SAY_HEARSAY
    elif clause.endswith(("吗", "？", "?")) or "是否" in clause:
        kind = SAY_QUESTION
    elif any(marker in clause for marker in _REFUTATION_MARKERS):
        kind = SAY_REFUTATION
    elif any(marker in clause for marker in _CORRECTION_MARKERS):
        kind = SAY_CORRECTION
    else:
        kind = SAY_ASSERTION
    if kind == SAY_QUESTION:
        polarity = "neutral"
    elif any(word in clause for word in _NEGATION_WORDS):
        polarity = "negative"
    else:
        polarity = "positive"
    return kind, polarity, quoted


def _split_clauses(text: str) -> list[str]:
    parts = [part.strip() for part in _CLAUSE_SPLIT.split(text) if part.strip()]
    return parts or [text.strip()]


def analyze_field(revision_id: str, field_name: str, text: str) -> list[Claim]:
    """分析一个版本的一个可见字段（headline 或 body）。"""
    claims: list[Claim] = []
    for index, clause in enumerate(_split_clauses(_normalize(text))):
        kind, polarity, quoted = _classify(clause)
        claims.append(
            Claim(
                id=f"{revision_id}:{field_name}:{index}",
                revision_id=revision_id,
                field=field_name,
                clause_index=index,
                text=clause,
                kind=kind,
                polarity=polarity,
                speaker=_speaker(clause, kind),
                scope=extract_scope(clause),
                quoted=quoted,
            )
        )
    return claims


def analyze_revision(revision: dict) -> list[Claim]:
    """分析一个 revision 的标题与正文。"""
    claims: list[Claim] = []
    for field_name in ("headline", "body"):
        if revision.get(field_name):
            claims.extend(analyze_field(revision["id"], field_name, revision[field_name]))
    return claims


def analyze_packet(records: dict) -> dict[str, list[Claim]]:
    """返回 ``{revision_id: [Claim, ...]}``。"""
    return {row["id"]: analyze_revision(row) for row in records["revision"]}
