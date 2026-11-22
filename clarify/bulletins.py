"""核验通告的子范围解析与说法覆盖判断。

通告一句话可能同时：
- 否定一个子范围（“不会暂停”“仍开放”）；
- 给另一个子范围附条件（“仅暂停换件至 12 月 5 日”）；
- 对其余范围明示没有结论（“不在回复范围”“另发”）。

后续通告（如 N3）可以缩窄早前通告的适用范围；本模块按通告时间顺序生效，
缩窄后被排除的子范围作为 ``narrowed_remainder`` 保留，不会被旧结论吞掉。
"""

from __future__ import annotations

import re

from .analyzer import extract_scope
from .models import (
    BulletinClause,
    CoverageItem,
    Remainder,
    SAY_QUESTION,
    Scope,
    VERDICT_CONFIRMED,
    VERDICT_NEGATED,
    VERDICT_OPEN,
    VERDICT_QUALIFIED,
)

# 地点层级：叶子 -> 祖先链（由近到远）
PLACE_PARENT = {
    "城区": ("沿岸",),
    "东岸": ("沿岸",),
    "西岸旧街": ("西岸", "沿岸"),
    "港区": ("西岸", "沿岸"),
    "西岸": ("沿岸",),
    "境外": (),
    "外地": (),
}
LEAF_PLACES = ("城区", "东岸", "西岸旧街", "港区", "境外", "外地")

# 业务层级：细分业务 -> 上位业务
BUSINESS_PARENT = {
    "营业备案": ("备案",),
    "合并备案": ("备案",),
    "换件": ("自助柜",),
    "自助柜": (),
    "柜台": (),
    "名单": ("柜台",),
    "批量资料": (),
}
BROAD_BUSINESS_CHILDREN = {
    "备案": ("营业备案", "合并备案"),
}

_OPEN_MARKERS = ("不在回复范围", "不在范围", "另发", "没找到发文")
_QUALIFIED_MARKERS = ("仅暂停", "只暂停", "仅指", "至 12 月 5 日", "至12月5日", "改用")
_NEGATED_MARKERS = ("不会暂停", "仍开放", "不是暂停", "没有查到关闭", "照常")
_CONFIRMED_MARKERS = ("已永久", "暂停换件")

_SENTENCE_SPLIT = re.compile(r"[。；;，,]+")
_BULLETIN_REF_RE = re.compile(r"N\d+")


def expand_places(places: frozenset[str]) -> list[str]:
    """把宽范围地点展开成叶子；无地点限定视为全部叶子。"""
    if not places:
        return list(LEAF_PLACES)
    leaves: list[str] = []
    for place in places:
        if place == "沿岸":
            leaves.extend(["城区", "东岸", "西岸旧街", "港区"])
        elif place == "西岸":
            leaves.extend(["西岸旧街", "港区"])
        else:
            # 叶子地点（含境外、外地）原样保留
            leaves.append(place)
    return sorted(set(leaves))


def expand_businesses(businesses: frozenset[str]) -> list[str]:
    """把“备案”等宽业务展开成通告实际区分的细分业务。"""
    result: list[str] = []
    for business in businesses:
        if business in BROAD_BUSINESS_CHILDREN:
            result.extend(BROAD_BUSINESS_CHILDREN[business])
        else:
            result.append(business)
    return sorted(set(result)) or list(businesses)


def _verdict_of(sentence: str) -> str:
    # N3 式缩窄：先于 open 判定，“仅指”本身是对前序通告适用范围的限定
    if "仅指" in sentence:
        return VERDICT_QUALIFIED
    if any(marker in sentence for marker in _OPEN_MARKERS):
        return VERDICT_OPEN
    # 同一句里“暂停换件至某日”是附条件，不能压过“仍开放/不会暂停”的否定
    if any(marker in sentence for marker in _NEGATED_MARKERS):
        return VERDICT_NEGATED
    if any(marker in sentence for marker in _QUALIFIED_MARKERS):
        return VERDICT_QUALIFIED
    if any(marker in sentence for marker in _CONFIRMED_MARKERS):
        return VERDICT_CONFIRMED
    return VERDICT_OPEN


def parse_bulletin(row: dict) -> list[BulletinClause]:
    """把一条通告切成带范围和处置的子句。"""
    clauses: list[BulletinClause] = []
    for sentence in filter(None, (part.strip() for part in _SENTENCE_SPLIT.split(row["text"]))):
        verdict = _verdict_of(sentence)
        scope = extract_scope(sentence)
        refs = _BULLETIN_REF_RE.findall(sentence)
        note = ""
        if verdict == VERDICT_OPEN:
            note = "通告明示无结论：" + "、".join(_OPEN_MARKERS_HITS(sentence))
        if refs:
            note = ("缩窄通告 " + "、".join(refs) + " 的适用范围；" + note).rstrip("；")
        clauses.append(
            BulletinClause(
                bulletin_id=row["id"],
                bulletin_time=row["time"],
                verdict=verdict,
                scope=scope,
                text=sentence,
                note=note,
            )
        )
    return clauses


def _OPEN_MARKERS_HITS(sentence: str) -> list[str]:
    return [marker for marker in _OPEN_MARKERS if marker in sentence]


def parse_bulletins(records: dict) -> list[BulletinClause]:
    rows = sorted(records["bulletin"], key=lambda row: row["time"])
    clauses: list[BulletinClause] = []
    for row in rows:
        clauses.extend(parse_bulletin(row))
    return clauses


def expand_populations(populations: frozenset[str]) -> list[str]:
    """把宽范围人群展开成通告实际区分的人群。"""
    result: list[str] = []
    for population in populations:
        if population in ("小微商户",):
            result.extend(["个人营业", "企业合并"])
        else:
            result.append(population)
    return sorted(set(result)) or list(populations)


def _place_covered(clause_place: str, leaf: str) -> bool:
    if clause_place == leaf:
        return True
    return clause_place in PLACE_PARENT.get(leaf, ())


def _business_covered(clause_business: str, leaf_business: str) -> bool:
    # 层级方向：上位业务（备案、柜台、自助柜）可覆盖细分；
    # “自助柜 ↔ 换件”是同一设施的不同表述，允许双向命中
    if clause_business == leaf_business:
        return True
    if clause_business in BUSINESS_PARENT.get(leaf_business, ()):
        return True
    pair = {"自助柜", "换件"}
    return clause_business in pair and leaf_business in pair


def _population_covered(clause_pop: str, leaf_pop: str) -> bool:
    # 通告未限定人群时不排除任何说法；说法未限定人群时接受任何通告
    if not clause_pop or not leaf_pop:
        return True
    if clause_pop == leaf_pop:
        return True
    # 宽口径人群（小微商户）包含个人营业与企业合并两个细类
    broad = {"小微商户": ("个人营业", "企业合并")}
    return leaf_pop in broad.get(clause_pop, ())


def _matching_clauses(leaf_place: str, leaf_business: str, leaf_pop: str,
                      clauses) -> list[BulletinClause]:
    """命中要求：地点、业务、人群三个维度都至少有一个标签相容。

    子句可同时带多个业务标签（如“自助柜/换件”），任一相容即整体命中，
    不会因子句里还带另一个业务标签而漏掉它真正回应的说法。
    """
    matches = []
    for clause in clauses:
        clause_places = expand_places(clause.scope.places)
        clause_businesses = list(clause.scope.businesses)
        clause_pops = list(clause.scope.populations)
        if not any(_place_covered(place, leaf_place) for place in clause_places):
            continue
        if not any(_business_covered(biz, leaf_business) for biz in clause_businesses):
            continue
        # 通告未限定人群（空集合）时不排除任何说法；否则逐个人群标签相容判断
        if clause_pops and not any(_population_covered(pop, leaf_pop) for pop in clause_pops):
            continue
        matches.append(clause)
    return matches


def _dedup_coords(rows: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """同一子范围坐标可能由业务/人群展开重复产生，按坐标去重。"""
    seen: set[str] = set()
    result: list[tuple[str, str]] = []
    for coord, reason in rows:
        if coord in seen:
            continue
        seen.add(coord)
        result.append((coord, reason))
    return result


def evaluate_coverage(claim, clauses: list[BulletinClause]) -> CoverageItem:
    """比对一条说法与全部有效通告，给出可更正部分与剩余异议。

    说法被切成 (地点叶子 × 业务叶子 × 人群叶子) 的子范围逐段判定；
    每段独立取命中通告中时间最新者，因此 N3 只能缩窄它点明的人群子范围，
    不会把 N1 在其它子范围上的否定整体吞掉。
    """
    if claim.kind == SAY_QUESTION:
        return CoverageItem(
            claim=claim,
            corrected=(),
            qualifiers=(),
            remainders=(
                Remainder(scope=claim.scope, reason="open：反问/提问不构成断言，且无通告结论"),
            ),
        )

    place_leaves = expand_places(claim.scope.places)
    business_leaves = expand_businesses(claim.scope.businesses) or list(claim.scope.businesses)
    # 人群与业务各自展开后按“同类叶子”配对，不做全笛卡尔积：
    # 企业合并人群不会被安到营业备案子范围上，反之亦然。
    pop_leaves = expand_populations(claim.scope.populations) or [""]
    segments = _segments(place_leaves, business_leaves, pop_leaves, claim)

    corrected: list[BulletinClause] = []
    qualifiers: list[BulletinClause] = []
    # 剩余异议只保留原说法自己的范围标签，不把叶子展开结果回写进去
    remainder_rows: list[tuple[str, str]] = []  # (子范围坐标, reason)

    for place, business, population in segments:
        matches = _matching_clauses(place, business, population, clauses)
        coord = f"{place}/{business}/{population or '不限人群'}"
        if not matches:
            remainder_rows.append((coord, "not_addressed：现有通告未覆盖该子范围"))
            continue
        latest = max(matches, key=lambda clause: clause.bulletin_time)
        verdicts = {clause.verdict for clause in matches}
        # 缩窄（qualified）不推翻同范围内更早的否定/证实结论本身：
        # N3 只把 N1 的适用面限定到个人营业，个人营业子范围仍可据 N1 更正；
        # 被 N3“另发/open”点名的企业子范围才作为剩余异议保留。
        if latest.verdict == VERDICT_QUALIFIED and VERDICT_NEGATED in verdicts:
            for clause in matches:
                if clause.verdict == VERDICT_NEGATED and clause not in corrected:
                    corrected.append(clause)
            if latest not in qualifiers:
                qualifiers.append(latest)
        elif latest.verdict == VERDICT_NEGATED:
            if latest not in corrected:
                corrected.append(latest)
        elif latest.verdict == VERDICT_QUALIFIED:
            if latest not in qualifiers:
                qualifiers.append(latest)
            # 仅当条件子句没有完全回应说法（如把“永久撤除”改成“暂停换件”）时记异议
            if _qualified_leaves_disagreement(claim, latest):
                remainder_rows.append(
                    (coord, "narrowed_remainder：仅附条件成立——" + latest.text)
                )
        elif latest.verdict == VERDICT_CONFIRMED:
            if latest not in qualifiers:
                qualifiers.append(latest)
        else:
            if VERDICT_NEGATED in verdicts or VERDICT_QUALIFIED in verdicts:
                if latest not in qualifiers:
                    qualifiers.append(latest)
            remainder_rows.append((coord, "open：" + (latest.note or latest.text)))

    remainders: list[Remainder] = []
    reasons: dict[str, list[str]] = {}
    for coord, reason in _dedup_coords(remainder_rows):
        reasons.setdefault(reason, []).append(coord)
    for reason, coords in reasons.items():
        remainders.append(Remainder(scope=claim.scope, reason=f"{reason}（子范围：{'、'.join(coords)}）"))

    return CoverageItem(
        claim=claim,
        corrected=tuple(corrected),
        qualifiers=tuple(dict.fromkeys(qualifiers)),
        remainders=tuple(remainders),
    )


def _qualified_leaves_disagreement(claim, clause: BulletinClause) -> bool:
    """条件结论是否与原说法的模态相冲突（暂停换件 ≠ 永久撤除）。"""
    claim_modal = set(claim.scope.modalities)
    claim_period = set(claim.scope.periods)
    if ("永久" in claim_period or "撤除" in claim_modal) and "暂停" in set(clause.scope.modalities):
        return True
    if "一律" in claim_modal:
        return True
    return False


# 业务叶子与人群细类的语义配对，避免无关组合产生伪子范围
_BUSINESS_POP_PAIRS = {
    "营业备案": ("个人营业",),
    "合并备案": ("企业合并",),
}


def _segments(place_leaves, business_leaves, pop_leaves, claim):
    """生成 (地点, 业务, 人群) 子范围；业务与人群按语义配对而非全组合。"""
    explicit_pops = list(claim.scope.populations)
    for place in place_leaves:
        for business in business_leaves:
            paired = _BUSINESS_POP_PAIRS.get(business)
            if paired and explicit_pops:
                for population in paired:
                    if population in pop_leaves:
                        yield place, business, population
            elif paired:
                yield place, business, paired[0]
            elif pop_leaves == [""]:
                yield place, business, ""
            else:
                for population in pop_leaves:
                    yield place, business, population


def evaluate_all(claims_by_revision: dict, clauses: list[BulletinClause]) -> dict[str, CoverageItem]:
    """返回 ``{claim_id: CoverageItem}``。"""
    result: dict[str, CoverageItem] = {}
    for claims in claims_by_revision.values():
        for claim in claims:
            result[claim.id] = evaluate_coverage(claim, clauses)
    return result
