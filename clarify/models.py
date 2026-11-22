"""合成传播复核系统的数据模型。

所有对象均为不可变数据的薄封装，原始资料（records.json）不会被改写；
编辑工作流产生的状态保存在独立的工作区文件中。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# 文本分析层
# ---------------------------------------------------------------------------

# 话语类型：
#   assertion  作者断言
#   hearsay    传闻（带消息来源、作者未背书）
#   question   反问 / 提问，不构成断言
#   refutation 明确反驳（作者明确否定某一说法）
#   correction 更正性陈述（作者给出的替代事实）
SAY_ASSERTION = "assertion"
SAY_HEARSAY = "hearsay"
SAY_QUESTION = "question"
SAY_REFUTATION = "refutation"
SAY_CORRECTION = "correction"


@dataclass(frozen=True)
class Scope:
    """说法或通告结论的限定范围。

    每个维度是一个标签集合；空集合表示该维度未限定（即“全范围”）。
    places 还承载层级：见 :mod:`clarify.bulletins` 的 PLACE_PARENT。
    """

    places: frozenset[str] = frozenset()
    populations: frozenset[str] = frozenset()
    businesses: frozenset[str] = frozenset()
    periods: frozenset[str] = frozenset()
    modalities: frozenset[str] = frozenset()

    def with_extra(self, **parts) -> "Scope":
        data = {
            "places": set(self.places),
            "populations": set(self.populations),
            "businesses": set(self.businesses),
            "periods": set(self.periods),
            "modalities": set(self.modalities),
        }
        for key, values in parts.items():
            data[key].update(values)
        return Scope(**{key: frozenset(values) for key, values in data.items()})

    def is_empty(self) -> bool:
        return not (
            self.places
            or self.populations
            or self.businesses
            or self.periods
            or self.modalities
        )


@dataclass(frozen=True)
class Claim:
    """从一个版本的可见文本中辨出的一个说法。"""

    id: str
    revision_id: str
    field: str  # headline / body
    clause_index: int
    text: str  # 原句（原样保留，含限定词）
    kind: str  # SAY_* 之一
    polarity: str  # positive / negative / neutral
    speaker: str  # 发言者（含“网传/消息称”等来源标记）
    scope: Scope
    quoted: bool = False  # 是否出现在引号、书名号等引述内

    @property
    def is_author_assertion(self) -> bool:
        """只有作者自己的断言与更正性陈述算作者断言；传闻、反问不算。"""
        return self.kind in (SAY_ASSERTION, SAY_CORRECTION)


# ---------------------------------------------------------------------------
# 传播层
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ResolvedDisplay:
    """一个浏览位置在某次展示中实际可见的内容。"""

    display_id: str
    surface: str
    visible_fields: tuple[str, ...]
    revision_id: str
    post_id: str
    claims: tuple[Claim, ...]
    # 同屏引用卡片实际解析到的版本（动态卡片按展示时间取最新版，冻结卡片固定）
    linked_revision_id: Optional[str]
    link_mode: Optional[str]


@dataclass(frozen=True)
class ExposureView:
    """一次匿名读者的实际展示（exposure 记录的解析结果）。"""

    exposure_id: str
    viewer: str
    display_id: str
    time: str
    revision_id: Optional[str]  # 空值表示截图无法定位版本
    linked_revision_id: Optional[str]
    located: bool
    visible_claims: tuple[Claim, ...]
    unverifiable: bool


# ---------------------------------------------------------------------------
# 通告与覆盖结论层
# ---------------------------------------------------------------------------

# 通告对范围内说法的处置
VERDICT_NEGATED = "negated"      # 明确否定
VERDICT_QUALIFIED = "qualified"  # 附条件成立 / 范围缩窄
VERDICT_CONFIRMED = "confirmed"  # 明确证实
VERDICT_OPEN = "open"            # 通告明示不在范围、没有结论


@dataclass(frozen=True)
class BulletinClause:
    """通告的一个适用子范围及处置。"""

    bulletin_id: str
    bulletin_time: str
    verdict: str
    scope: Scope
    text: str  # 通告原句
    note: str = ""  # 例如 open 类结论的“不在回复范围”说明


@dataclass(frozen=True)
class Remainder:
    """说法中未被通告覆盖或仅被缩窄的剩余部分。"""

    scope: Scope
    reason: str  # open / not_addressed / narrowed_remainder


@dataclass(frozen=True)
class CoverageItem:
    """一条说法与全部有效通告比对后的复核结论。"""

    claim: Claim
    corrected: tuple[BulletinClause, ...]      # 可据以更正的通告子句
    qualifiers: tuple[BulletinClause, ...]     # 附条件 / 缩窄的子句
    remainders: tuple[Remainder, ...]          # 仍有异议或无结论的部分

    @property
    def fully_corrected(self) -> bool:
        return bool(self.corrected) and not self.remainders


# ---------------------------------------------------------------------------
# 更正卡与工作流层
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CardClause:
    """更正卡文案中的一个事实分句，对应一条通告子句。"""

    clause: BulletinClause
    text: str


@dataclass(frozen=True)
class CorrectionCard:
    """编辑可批准的更正卡（草案或已发卡）。"""

    id: Optional[str]
    bulletin_refs: tuple[str, ...]
    clauses: tuple[CardClause, ...]
    text: str
    target_displays: tuple[str, ...]
    # 文案对每个目标位置分别覆盖了哪些说法、是否带来该位置读者没见过的谣传
    per_display_notes: dict[str, str] = field(default_factory=dict)
    published: Optional[str] = None
    copied_viewers: tuple[str, ...] = ()

    @property
    def char_count(self) -> int:
        return len(self.text)


@dataclass(frozen=True)
class SlotPlan:
    """一个版面发稿窗的卡位安排。"""

    slot_id: str
    surface: str
    capacity: int
    max_chars: int
    chosen: tuple[CorrectionCard, ...]
    deferred: tuple[tuple[CorrectionCard, str], ...]  # 卡位不足而顺延的卡与理由


@dataclass(frozen=True)
class PositionDetail:
    """编辑打开任一传播位置时看到的完整核对单。"""

    display_id: str
    surface: str
    visible_fields: tuple[str, ...]
    shown_revision_id: str
    link_mode: Optional[str]
    linked_revision_id: Optional[str]
    exposures: tuple[ExposureView, ...]
    visible_claims: tuple[Claim, ...]
    coverages: tuple[CoverageItem, ...]
    received_corrections: tuple[CorrectionCard, ...]
    received_audit: dict[str, list[str]]        # 已发卡按当前通告的实时审计意见
    missing: tuple[str, ...]                 # 当前仍缺的澄清（人话说明）
    cannot_share_reasons: dict[str, str]     # 不能与其它位置合用提示的理由
