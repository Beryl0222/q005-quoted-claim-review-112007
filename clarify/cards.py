"""更正卡的文案生成、合规审计与发稿窗卡位规划。

合规底线（来自需求）：
- 一张卡可在多个展示位置合用，但文案不能让任一组读者把较窄澄清误认成全范围结论；
- 不能为纠正一句局部错误，带入该位置读者没见过的谣传细节；
- 文案码点数不得超过版面 max_chars，挂卡数量不得超过 capacity；
- 卡位不够时，优先让更多“实际见过对应说法且尚未收到该部分更正”的读者看到提示。
"""

from __future__ import annotations

from dataclasses import dataclass

from .bulletins import expand_businesses, expand_places
from .models import (
    BulletinClause,
    CardClause,
    CorrectionCard,
    CoverageItem,
    ExposureView,
    Remainder,
    SlotPlan,
    VERDICT_NEGATED,
    VERDICT_OPEN,
    VERDICT_QUALIFIED,
)


def _drop_redundant_open(clauses: list[BulletinClause]) -> list[BulletinClause]:
    """同一通告已用 qualified 分句写明边界时，去掉重复表达的 open 分句。"""
    qualified_bulletins = {c.bulletin_id for c in clauses if c.verdict == VERDICT_QUALIFIED}
    result = []
    for clause in clauses:
        if clause.verdict == VERDICT_OPEN and clause.bulletin_id in qualified_bulletins:
            continue
        result.append(clause)
    return result


# 每种“事实结论”的文案模板：只写通告核实过的范围，不写读者没见过的谣传细节
def _clause_text(clause: BulletinClause) -> str:
    if clause.verdict == VERDICT_NEGATED and "备案" in clause.text:
        places = "、".join(sorted(clause.scope.places)) or "相关地点"
        populations = "、".join(sorted(clause.scope.populations)) or "相关主体"
        period = "、".join(sorted(p.replace(" ", "") for p in clause.scope.periods)) or "通告时段"
        # 限定语前置：通告只覆盖点名的子范围，不能让读者外推到沿岸全范围
        return f"仅{places}{populations}备案{period}不暂停"
    if clause.verdict == VERDICT_QUALIFIED and "自助柜" in clause.text:
        return "港区自助柜仅暂停换件至12月5日，非永久撤除"
    if clause.verdict == VERDICT_QUALIFIED and "仅指" in clause.text:
        return "该澄清仅指个人营业备案，企业合并备案暂停另发通知"
    if clause.verdict == VERDICT_QUALIFIED:
        return clause.text
    if clause.verdict == VERDICT_OPEN:
        # open 类分句只作边界说明，提醒读者勿作全范围理解
        if "另发" in clause.text:
            return "企业合并备案是否暂停尚无结论，通知另发"
        return "该子范围暂无核验结论"
    return clause.text


@dataclass
class DraftRequest:
    """为某展示位置起草卡片时的上下文。"""

    display_id: str
    surface: str
    coverages: list[CoverageItem]
    seen_viewers: set[str]
    already_corrected_viewers: set[str]
    max_chars: int


def _unresolved_coverages(coverages: list[CoverageItem]) -> list[CoverageItem]:
    """该位置上仍有可发更正或仍存异议的说法。"""
    result = []
    for item in coverages:
        if item.remainders or item.corrected or item.qualifiers:
            result.append(item)
    return result


def draft_card(request: DraftRequest) -> CorrectionCard | None:
    """按位置实际出现过的说法起草一张只覆盖核实范围的更正卡。

    只纳入与该位置可见说法在范围上真正重叠的通告分句，
    避免把读者没见过的业务细节（如别处的柜台结论）写进文案。
    """
    chosen_clauses: list[BulletinClause] = []
    seen_keys: set[tuple[str, str]] = set()
    relevant_coverages = _unresolved_coverages(request.coverages)
    # 只有作者断言/更正才需要主动发卡纠正；纯传闻或反问位置不出卡，
    # 避免给没接触到作者断言的读者推送无关谣传细节
    needs_card = any(
        item.claim.is_author_assertion and (item.corrected or item.qualifiers)
        for item in relevant_coverages
    )
    if not needs_card:
        return None
    for item in relevant_coverages:
        for clause in list(item.corrected) + list(item.qualifiers):
            if not _scope_overlaps(item.claim, clause):
                continue
            key = (clause.bulletin_id, clause.text)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            chosen_clauses.append(clause)
    if not chosen_clauses:
        return None
    # open 类边界分句若与已选 qualified 分句来自同一通告，文案上已被涵盖则去重
    chosen_clauses = _drop_redundant_open(chosen_clauses)

    card_clauses = [CardClause(clause=clause, text=_clause_text(clause)) for clause in chosen_clauses]
    full_text = "；".join(part.text for part in card_clauses) + "。"
    refs = tuple(dict.fromkeys(clause.bulletin_id for clause in chosen_clauses))
    card = CorrectionCard(
        id=None,
        bulletin_refs=refs,
        clauses=tuple(card_clauses),
        text=full_text,
        target_displays=(request.display_id,),
        per_display_notes={request.display_id: f"覆盖本位置 {len(chosen_clauses)} 条通告结论"},
    )
    if card.char_count > request.max_chars:
        card = _shrink(card_clauses, refs, request)
    return card


def _shrink(card_clauses, refs, request: DraftRequest) -> CorrectionCard:
    """超字数时逐条删减并优先保留否定性结论，保证不突破字数上限。"""
    ordered = sorted(card_clauses, key=lambda part: part.clause.verdict != VERDICT_NEGATED)
    kept: list[CardClause] = []
    for part in ordered:
        candidate = "；".join(item.text for item in kept + [part]) + "。"
        if len(candidate) <= request.max_chars:
            kept.append(part)
    text = "；".join(part.text for part in kept) + "。"
    return CorrectionCard(
        id=None,
        bulletin_refs=tuple(dict.fromkeys(part.clause.bulletin_id for part in kept)),
        clauses=tuple(kept),
        text=text,
        target_displays=(request.display_id,),
        per_display_notes={
            request.display_id: "因字数限制删减分句；剩余异议需另卡或另窗处理"
        },
    )


# ---------------------------------------------------------------------------
# 合用判断
# ---------------------------------------------------------------------------

def can_share(card: CorrectionCard, target_display: str, target_coverages: list[CoverageItem]) -> tuple[bool, str]:
    """判断已为别处起草的卡能否挂到另一个位置。"""
    target_claim_texts = {
        item.claim.text
        for item in target_coverages
        if item.claim.is_author_assertion or item.claim.kind == "hearsay"
    }
    for part in card.clauses:
        # 规则一：卡片每个分句都必须对应目标位置读者实际见过的说法
        relevant = any(
            _scope_overlaps(item.claim, part.clause)
            for item in target_coverages
        )
        if not relevant:
            return False, f"分句“{part.text}”针对的说法该位置读者未见过，带入即构成无关谣传细节"
    # 规则二：卡片不能把目标位置说法中仍有异议的子范围说成全范围结论
    for item in target_coverages:
        for remainder in item.remainders:
            if _remainder_contradicted_by(remainder, card):
                return False, (
                    f"原句“{item.claim.text}”仍有未覆盖子范围（{remainder.reason}），"
                    "合用会让读者把较窄澄清误认成全范围结论"
                )
    return True, "可合用：分句均落在该位置已出现说法内，且未抹平剩余异议"


def _scope_overlaps(claim, clause: BulletinClause) -> bool:
    claim_places = set(expand_places(claim.scope.places))
    clause_places = set(expand_places(clause.scope.places))
    claim_biz = set(expand_businesses(claim.scope.businesses))
    clause_biz = set(expand_businesses(clause.scope.businesses))
    place_overlap = not claim_places or not clause_places or bool(claim_places & clause_places)
    biz_overlap = not claim_biz or not clause_biz or bool(claim_biz & clause_biz)
    return place_overlap and biz_overlap


def _remainder_contradicted_by(remainder: Remainder, card: CorrectionCard) -> bool:
    if not remainder.scope.places:
        return False
    for part in card.clauses:
        if part.clause.verdict != VERDICT_NEGATED:
            continue
        if set(remainder.scope.places) & set(expand_places(part.clause.scope.places)):
            return True
    return False


# ---------------------------------------------------------------------------
# 已发卡审计（如 C1）
# ---------------------------------------------------------------------------

@dataclass
class CardAudit:
    card_id: str
    overreach: list[str]          # 超出通告核实范围的表述
    reusable_facts: list[str]     # 独立成立、可继续使用的事实分句
    per_display: dict[str, str]   # 每个挂载位置的具体问题
    still_needed_viewers: list[str]


def audit_active_card(card_row: dict, coverages_by_id: dict, views: list[ExposureView],
                      display_claims: dict[str, list]) -> CardAudit:
    """审计资料中已批准发出的更正卡（重点：N3 缩窄后的旧卡）。"""
    overreach: list[str] = []
    reusable: list[str] = []
    text = card_row["text"]

    # “一律照常”是全范围措辞：与 N3 的缩窄直接冲突
    if "一律" in text or "照常" in text:
        overreach.append(
            "“一律照常”属全范围结论；N3 明确 N1 仅指个人营业备案，企业合并备案暂停另发，"
            "且境外代办窗口无结论——该分句必须替换为带限定的表述"
        )
    if "港区" in text and "12月5日" in text.replace(" ", ""):
        reusable.append("“港区自助柜仅暂停换件至12月5日”由 N2 独立支持，N3 不影响，可继续使用")

    per_display: dict[str, str] = {}
    for display_id in card_row["display"]:
        seen_texts = {claim.text for claim in display_claims.get(display_id, [])}
        notes = []
        if "港区" in text and not any("港区" in seen for seen in seen_texts):
            notes.append("该位置读者未见过港区柜说法，港区分句对他们是无关细节，不能合用")
        if any("一律" in seen or "暂停备案" in seen for seen in seen_texts):
            notes.append("读者见过的是全范围“暂停”说法，本卡“一律照常”会让其误以为全范围已澄清")
        per_display[display_id] = "；".join(notes) or "挂载位置无额外冲突"

    # 已收到旧卡的读者照实保留；旧卡失效后补发不能把他们当已更正而跳过，
    # 因此“仍需覆盖”= 该位置见过说法的全部读者，已送达名单仅用于标注历史
    all_viewers: list[str] = []
    for view in views:
        if view.unverifiable:
            continue
        if view.display_id in card_row["display"] and view.viewer not in all_viewers:
            all_viewers.append(view.viewer)
    return CardAudit(
        card_id=card_row["id"],
        overreach=overreach,
        reusable_facts=reusable,
        per_display=per_display,
        still_needed_viewers=all_viewers,
    )


# ---------------------------------------------------------------------------
# 发稿窗规划
# ---------------------------------------------------------------------------

def plan_slot(slot_row: dict, drafts: dict[str, CorrectionCard],
              coverages_by_display: dict, viewers_by_display: dict[str, set[str]],
              delivered_by_display: dict[str, set[str]]) -> SlotPlan:
    """按“受影响且未收到更正的读者数”排序安排卡位。"""
    candidates: list[tuple[int, int, str, CorrectionCard]] = []
    for display_id in slot_row["displays"]:
        card = drafts.get(display_id)
        if card is None:
            continue
        if card.char_count > slot_row["max_chars"]:
            continue
        seen = viewers_by_display.get(display_id, set())
        delivered = delivered_by_display.get(display_id, set())
        uncovered = len(seen - delivered)
        unresolved = sum(1 for item in coverages_by_display.get(display_id, []) if item.remainders)
        # 优先级：未覆盖读者多者优先；其次剩余异议多者；再按位置编号稳定排序
        candidates.append((uncovered, unresolved, display_id, card))
    candidates.sort(key=lambda row: (-row[0], -row[1], row[2]))

    chosen: list[CorrectionCard] = []
    deferred: list[tuple[CorrectionCard, str]] = []
    used_displays: set[str] = set()
    for uncovered, unresolved, display_id, card in candidates:
        if len(chosen) >= slot_row["capacity"]:
            deferred.append((card, f"卡位已满；该位置尚有 {uncovered} 位读者未收到该部分更正，顺延下一窗"))
            continue
        merged = _try_merge(chosen, card, display_id, coverages_by_display)
        if merged is not None:
            chosen[merged] = _merge_cards(chosen[merged], card, display_id, slot_row["max_chars"])
        else:
            chosen.append(card)
        used_displays.add(display_id)
    return SlotPlan(
        slot_id=slot_row["id"],
        surface=slot_row["surface"],
        capacity=slot_row["capacity"],
        max_chars=slot_row["max_chars"],
        chosen=tuple(chosen),
        deferred=tuple(deferred),
    )


def _try_merge(chosen: list[CorrectionCard], card: CorrectionCard, display_id: str,
               coverages_by_display: dict) -> int | None:
    """若新卡与已选卡只引用同一批通告结论，则合用一卡（仍须通过 can_share 规则）。"""
    for index, existing in enumerate(chosen):
        if existing.bulletin_refs != card.bulletin_refs:
            continue
        ok, _ = can_share(existing, display_id, coverages_by_display.get(display_id, []))
        if ok:
            return index
    return None


def _merge_cards(existing: CorrectionCard, card: CorrectionCard, display_id: str, max_chars: int):
    targets = tuple(dict.fromkeys(existing.target_displays + (display_id,)))
    notes = dict(existing.per_display_notes)
    notes.update(card.per_display_notes)
    merged = CorrectionCard(
        id=existing.id,
        bulletin_refs=existing.bulletin_refs,
        clauses=existing.clauses,
        text=existing.text,
        target_displays=targets,
        per_display_notes=notes,
        published=existing.published,
        copied_viewers=existing.copied_viewers,
    )
    if merged.char_count > max_chars:  # 合用语境下文案不变，超长则放弃合并
        return existing
    return merged
