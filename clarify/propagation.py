"""把 display / links / exposure 解析成“谁在何时实际看到了哪一版的哪些说法”。

关键规则：
- 冻结引用的卡片永远显示 target 指定的不可变版本；
- 动态卡片跟随帖子，按曝光时刻解析该帖子的最新版本，旧版展示不会因后来更新而归到新版；
- display.visible 决定渠道：只展示 headline 的位置看不到正文里的澄清；
- 转引同一标题不产生两处作者断言：引述句由 :mod:`clarify.analyzer` 标为传闻；
- exposure.revision 为空表示截图无法定位版本，只能进编辑核对清单，不算已证传播。
"""

from __future__ import annotations

from collections import defaultdict

from .models import ExposureView, ResolvedDisplay

FROZEN = "冻结引用"
DYNAMIC = "动态卡片"


def _revision_indexes(records: dict):
    by_id = {row["id"]: row for row in records["revision"]}
    by_post: dict[str, list[dict]] = defaultdict(list)
    for row in records["revision"]:
        by_post[row["post"]].append(row)
    # 合成资料没有版本时间字段，版本号 v1/v2 即时间顺序
    for rows in by_post.values():
        rows.sort(key=lambda row: row["id"])
    return by_id, by_post


def resolve_link_target(link: dict, exposure_time: str, records: dict) -> str | None:
    """按引用模式解析同屏卡片在某时刻实际显示的版本。"""
    by_id, by_post = _revision_indexes(records)
    if link["mode"] == FROZEN:
        return link["target"]  # target 即 revision id
    candidates = by_post.get(link["target"], [])  # target 即 post id
    return candidates[-1]["id"] if candidates else None


def _links_by_display(records: dict) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = defaultdict(list)
    for link in records["links"]:
        result[link["from"]].append(link)
    return result


def resolve_displays(records: dict, claims_by_revision: dict) -> list[ResolvedDisplay]:
    """解析每个展示位置的静态可见内容（动态卡片的版本留到曝光时解析）。"""
    by_id, _ = _revision_indexes(records)
    links = _links_by_display(records)
    resolved: list[ResolvedDisplay] = []
    for display in records["display"]:
        revision = by_id[display["revision"]]
        fields = tuple(display["visible"])
        visible_claims = tuple(
            claim
            for claim in claims_by_revision[display["revision"]]
            if claim.field in fields
        )
        local_links = links.get(display["id"], [])
        link = local_links[0] if local_links else None
        linked_rev = None
        mode = None
        if link is not None:
            mode = link["mode"]
            if mode == FROZEN:
                linked_rev = link["target"]
        resolved.append(
            ResolvedDisplay(
                display_id=display["id"],
                surface=display["surface"],
                visible_fields=fields,
                revision_id=display["revision"],
                post_id=revision["post"],
                claims=visible_claims,
                linked_revision_id=linked_rev,
                link_mode=mode,
            )
        )
    return resolved


def resolve_exposures(
    records: dict,
    displays: list[ResolvedDisplay],
    claims_by_revision: dict,
) -> list[ExposureView]:
    """把每条 exposure 解析为读者当时实际接触到的说法集合。"""
    display_by_id = {item.display_id: item for item in displays}
    by_id, _ = _revision_indexes(records)
    links = _links_by_display(records)
    views: list[ExposureView] = []
    for exposure in records["exposure"]:
        display = display_by_id[exposure["display"]]
        revision_id = exposure["revision"]
        unverifiable = revision_id is None
        located = not unverifiable
        visible: tuple = ()
        linked_rev = exposure.get("linked_revision")
        if located:
            # 曝光记录里的 revision 是当时的确切版本；以它为准过滤可见字段
            visible = tuple(
                claim
                for claim in claims_by_revision[revision_id]
                if claim.field in display.visible_fields
            )
            for link in links.get(display.display_id, []):
                if link["mode"] == DYNAMIC:
                    linked_rev = resolve_link_target(link, exposure["time"], records)
        views.append(
            ExposureView(
                exposure_id=exposure["id"],
                viewer=exposure["viewer"],
                display_id=display.display_id,
                time=exposure["time"],
                revision_id=revision_id,
                linked_revision_id=linked_rev,
                located=located,
                visible_claims=visible,
                unverifiable=unverifiable,
            )
        )
    return views


def viewers_who_saw_claim(views: list[ExposureView], claim_id: str) -> set[str]:
    """实际见到某条说法的读者集合（无法定位版本的截图不计入）。"""
    return {
        view.viewer
        for view in views
        if view.located and any(claim.id == claim_id for claim in view.visible_claims)
    }


def viewers_per_display(views: list[ExposureView]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = defaultdict(set)
    for view in views:
        if view.located:
            result[view.display_id].add(view.viewer)
    return dict(result)
