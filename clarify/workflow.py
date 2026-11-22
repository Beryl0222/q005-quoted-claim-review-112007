"""编辑工作流：批准发卡、送达留存、新通告缩窄后的旧卡重判、位置核对单。

状态与原始资料分离：records.json 只读，工作流状态写入独立 JSON。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .bulletins import evaluate_all, parse_bulletins
from .cards import audit_active_card, can_share, draft_card, plan_slot
from .models import CorrectionCard, PositionDetail, SlotPlan
from .propagation import resolve_displays, resolve_exposures, viewers_per_display


@dataclass
class IssuedCard:
    """已批准发出的卡（工作流状态）。"""

    id: str
    bulletin_refs: list[str]
    text: str
    target_displays: list[str]
    published: str
    copied_viewers: list[str] = field(default_factory=list)
    # 每张卡按通告时间戳记录当时依据的通告；后续缩窄时据此重判
    based_on_bulletins: list[str] = field(default_factory=list)
    status: str = "active"  # active / superseded
    superseded_reason: str = ""


class ClarifyWorkspace:
    """把资料包与编辑动作组装成可查询的工作台。"""

    def __init__(self, records: dict):
        self.records = records
        from .analyzer import analyze_packet

        self.claims_by_revision = analyze_packet(records)
        self.displays = resolve_displays(records, self.claims_by_revision)
        self.views = resolve_exposures(records, self.displays, self.claims_by_revision)
        self.bulletin_clauses = parse_bulletins(records)
        self.coverages = evaluate_all(self.claims_by_revision, self.bulletin_clauses)
        self.display_by_id = {item.display_id: item for item in self.displays}
        self.viewers_by_display = viewers_per_display(self.views)
        self.issued: list[IssuedCard] = []
        self._hydrate_active_cards()

    # -- 资料中已存在的送达记录：原样留存 --------------------------------
    def _hydrate_active_cards(self) -> None:
        for row in self.records.get("active_cards", []):
            self.issued.append(
                IssuedCard(
                    id=row["id"],
                    bulletin_refs=list(row["ref"]),
                    text=row["text"],
                    target_displays=list(row["display"]),
                    published=row["published"],
                    copied_viewers=list(row.get("copied_viewers", [])),
                    based_on_bulletins=list(row["ref"]),
                    status="active",
                )
            )

    # -- 基础查询 --------------------------------------------------------
    def claims_on_display(self, display_id: str) -> list:
        display = self.display_by_id[display_id]
        return [claim for claim in self.claims_by_revision[display.revision_id]
                if claim.field in display.visible_fields]

    def coverages_on_display(self, display_id: str) -> list:
        return [self.coverages[claim.id] for claim in self.claims_on_display(display_id)]

    def exposures_on_display(self, display_id: str) -> list:
        return [view for view in self.views if view.display_id == display_id]

    def delivered_viewers(self, display_id: str) -> set[str]:
        """该位置读者已经收到、且当前仍有效的纠正覆盖。

        已被新通告缩窄而失效（superseded）的旧卡不计入“已更正”，
        但其送达名单在 :class:`IssuedCard` 中照实保留，补发仍会覆盖这些读者。
        """
        result: set[str] = set()
        for card in self.issued:
            if display_id in card.target_displays and card.status == "active":
                result.update(card.copied_viewers)
        return result

    def historical_deliveries(self, display_id: str) -> list[IssuedCard]:
        """该位置所有送达记录（含已失效旧卡），用于编辑核对，不做删除。"""
        return [card for card in self.issued if display_id in card.target_displays]

    # -- 起草 / 发卡 -----------------------------------------------------
    def draft_for_display(self, display_id: str) -> CorrectionCard | None:
        slot = self._slot_of(display_id)
        request_max = slot["max_chars"] if slot else 200
        return draft_card(
            type(
                "DraftRequest",
                (),
                {
                    "display_id": display_id,
                    "surface": self.display_by_id[display_id].surface,
                    "coverages": self.coverages_on_display(display_id),
                    "seen_viewers": self.viewers_by_display.get(display_id, set()),
                    "already_corrected_viewers": self.delivered_viewers(display_id),
                    "max_chars": request_max,
                },
            )()
        )

    def _slot_of(self, display_id: str) -> dict | None:
        for slot in self.records["slots"]:
            if display_id in slot["displays"]:
                return slot
        return None

    def approve(self, card: CorrectionCard, published: str, copied_viewers: list[str],
                card_id: str) -> IssuedCard:
        """编辑批准并发出更正卡；送达名单照实记录。"""
        issued = IssuedCard(
            id=card_id,
            bulletin_refs=list(card.bulletin_refs),
            text=card.text,
            target_displays=list(card.target_displays),
            published=published,
            copied_viewers=list(copied_viewers),
            based_on_bulletins=list(card.bulletin_refs),
        )
        self.issued.append(issued)
        return issued

    # -- 新通告缩窄后的旧卡重判 ------------------------------------------
    def reevaluate_after_new_bulletin(self) -> list[dict]:
        """重新区分：哪些旧卡仍可用、哪些位置需要替换；旧送达记录照实保留。"""
        findings: list[dict] = []
        for card in self.issued:
            audit = audit_active_card(
                {
                    "id": card.id,
                    "text": card.text,
                    "display": card.target_displays,
                    "copied_viewers": card.copied_viewers,
                },
                self.coverages,
                self.views,
                {display_id: self.claims_on_display(display_id)
                 for display_id in card.target_displays},
            )
            if audit.overreach and card.status == "active":
                card.status = "superseded"
                card.superseded_reason = "；".join(audit.overreach)
            findings.append(
                {
                    "card_id": card.id,
                    "status": card.status,
                    "overreach": audit.overreach,
                    "reusable_facts": audit.reusable_facts,
                    "per_display": audit.per_display,
                    # 已看过旧卡的记录照实保留；补发不能把他们当已更正而跳过
                    "copied_viewers_retained": list(card.copied_viewers),
                    "still_needed_viewers": audit.still_needed_viewers,
                }
            )
        return findings

    # -- 发稿窗规划 ------------------------------------------------------
    def plan_all_slots(self) -> list[SlotPlan]:
        drafts = {}
        for display in self.displays:
            card = self.draft_for_display(display.display_id)
            if card is not None:
                drafts[display.display_id] = card
        coverages_by_display = {
            display.display_id: self.coverages_on_display(display.display_id)
            for display in self.displays
        }
        delivered = {
            display.display_id: self.delivered_viewers(display.display_id)
            for display in self.displays
        }
        return [
            plan_slot(slot, drafts, coverages_by_display, self.viewers_by_display, delivered)
            for slot in self.records["slots"]
        ]

    # -- 无法定位版本的截图：只列给编辑核对，不算已证传播 ----------------
    def unverifiable_screenshots(self) -> list[dict]:
        return [
            {
                "exposure_id": view.exposure_id,
                "viewer": view.viewer,
                "display_id": view.display_id,
                "time": view.time,
                "reason": "exposure.revision 为空，截图无法定位版本，不能当成已证传播",
            }
            for view in self.views
            if view.unverifiable
        ]

    def _live_card_audit(self, display_id: str) -> dict[str, list[str]]:
        """对挂在某位置的每张已发卡按当前通告做实时合规审计。"""
        findings: dict[str, list[str]] = {}
        for card in self.issued:
            if display_id not in card.target_displays:
                continue
            audit = audit_active_card(
                {
                    "id": card.id,
                    "text": card.text,
                    "display": [display_id],
                    "copied_viewers": card.copied_viewers,
                },
                self.coverages,
                self.views,
                {display_id: self.claims_on_display(display_id)},
            )
            notes = list(audit.overreach)
            if audit.per_display.get(display_id, "").startswith("该位置"):
                notes.append(audit.per_display[display_id])
            notes.extend(f"仍可沿用：{fact}" for fact in audit.reusable_facts)
            findings[card.id] = notes
        return findings

    # -- 任一传播位置的完整核对单 ----------------------------------------
    def position_detail(self, display_id: str) -> PositionDetail:
        display = self.display_by_id[display_id]
        coverages = self.coverages_on_display(display_id)
        missing: list[str] = []
        for item in coverages:
            for remainder in item.remainders:
                missing.append(f"原句“{item.claim.text}”：{remainder.reason}（范围 {_scope_text(remainder.scope)}）")
        cannot_share: dict[str, str] = {}
        own_card = self.draft_for_display(display_id)
        if own_card is not None:
            for other in self.displays:
                if other.display_id == display_id:
                    continue
                ok, reason = can_share(own_card, other.display_id,
                                       self.coverages_on_display(other.display_id))
                if not ok:
                    cannot_share[other.display_id] = reason
        received = tuple(
            CorrectionCard(
                id=card.id,
                bulletin_refs=tuple(card.bulletin_refs),
                clauses=(),
                text=card.text,
                target_displays=tuple(card.target_displays),
                published=card.published,
                copied_viewers=tuple(card.copied_viewers),
            )
            for card in self.issued
            if display_id in card.target_displays
        )
        # 已发卡按当前全部通告实时审计：即使编辑还没跑重判，详情也能标出越界分句
        live_card_findings = self._live_card_audit(display_id)
        return PositionDetail(
            display_id=display_id,
            surface=display.surface,
            visible_fields=display.visible_fields,
            shown_revision_id=display.revision_id,
            link_mode=display.link_mode,
            linked_revision_id=display.linked_revision_id,
            exposures=tuple(self.exposures_on_display(display_id)),
            visible_claims=tuple(self.claims_on_display(display_id)),
            coverages=tuple(coverages),
            received_corrections=received,
            received_audit=live_card_findings,
            missing=tuple(missing),
            cannot_share_reasons=cannot_share,
        )

    # -- 状态持久化 ------------------------------------------------------
    def save_state(self, path: Path) -> None:
        payload = {"issued": [asdict(card) for card in self.issued]}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_state(self, path: Path) -> None:
        if not path.exists():
            return
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.issued = [IssuedCard(**row) for row in payload.get("issued", [])]


def _scope_text(scope) -> str:
    parts = []
    for label, values in (
        ("地点", scope.places), ("业务", scope.businesses),
        ("人群", scope.populations), ("时段", scope.periods),
    ):
        if values:
            parts.append(f"{label}={'/'.join(sorted(values))}")
    return "，".join(parts) or "未限定"
