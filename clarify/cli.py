"""值班编辑命令行：只处理本地合成资料。

示例：
    python3 -I -m clarify.cli data/records.json claims
    python3 -I -m clarify.cli data/records.json positions
    python3 -I -m clarify.cli data/records.json position S3
    python3 -I -m clarify.cli data/records.json plan
    python3 -I -m clarify.cli data/records.json reevaluate
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from inspect_records import load_records

from .models import (
    SAY_ASSERTION,
    SAY_CORRECTION,
    SAY_HEARSAY,
    SAY_QUESTION,
    SAY_REFUTATION,
)
from .workflow import ClarifyWorkspace, _scope_text

KIND_LABEL = {
    SAY_ASSERTION: "作者断言",
    SAY_HEARSAY: "传闻转述",
    SAY_QUESTION: "反问提问",
    SAY_REFUTATION: "明确反驳",
    SAY_CORRECTION: "更正陈述",
}


def _enum(value, mapping):
    return mapping.get(value, value)


def _print_claims(ws: ClarifyWorkspace) -> None:
    for revision_id, claims in ws.claims_by_revision.items():
        print(f"\n== 版本 {revision_id} ==")
        for claim in claims:
            print(
                f"  [{claim.field}#{claim.clause_index}] {_enum(claim.kind, KIND_LABEL)}"
                f"（{claim.polarity}，发言者：{claim.speaker}）"
            )
            print(f"      原句：{claim.text}")
            print(f"      限定：{_scope_text(claim.scope)}")


def _print_positions(ws: ClarifyWorkspace) -> None:
    for display in ws.displays:
        viewers = sorted(ws.viewers_by_display.get(display.display_id, set()))
        print(f"\n== {display.display_id}｜{display.surface}｜可见字段 {'+'.join(display.visible_fields)} ==")
        print(f"   展示版本：{display.revision_id}"
              + (f"｜引用卡片：{display.link_mode}→{display.linked_revision_id or '曝光时解析'}"
                 if display.link_mode else ""))
        print(f"   实际见到的读者：{'、'.join(viewers) if viewers else '无定位记录'}")
        for item in ws.coverages_on_display(display.display_id):
            claim = item.claim
            tag = _enum(claim.kind, KIND_LABEL)
            corrected = "、".join(dict.fromkeys(c.bulletin_id for c in item.corrected)) or "—"
            qualified = "、".join(dict.fromkeys(c.bulletin_id for c in item.qualifiers)) or "—"
            print(f"   · [{tag}] {claim.text}")
            print(f"       可更正依据：{corrected}｜附条件依据：{qualified}")
            for remainder in item.remainders:
                print(f"       剩余异议：{remainder.reason}｜{_scope_text(remainder.scope)}")
    print("\n== 无法定位版本的截图（仅列给编辑核对，不算已证传播） ==")
    for row in ws.unverifiable_screenshots():
        print(f"   {row['exposure_id']}｜{row['viewer']}｜{row['display_id']}｜{row['time']}：{row['reason']}")


def _print_position_detail(ws: ClarifyWorkspace, display_id: str) -> None:
    detail = ws.position_detail(display_id)
    print(f"\n== 位置 {detail.display_id}｜{detail.surface} ==")
    print(f"   当时显示版本：{detail.shown_revision_id}｜可见字段：{'+'.join(detail.visible_fields)}")
    if detail.link_mode:
        print(f"   引用模式：{detail.link_mode}｜同屏引用版本：{detail.linked_revision_id or '曝光时解析'}")
    print("   实际展示记录：")
    for view in detail.exposures:
        if view.unverifiable:
            print(f"     {view.exposure_id} {view.viewer} {view.time}：版本无法定位，待编辑核对")
        else:
            print(f"     {view.exposure_id} {view.viewer} {view.time}：版本 {view.revision_id}"
                  + (f"，引用 {view.linked_revision_id}" if view.linked_revision_id else ""))
    print("   读者已收到的纠正范围：")
    if not detail.received_corrections:
        print("     （无）")
    for card in detail.received_corrections:
        superseded = any(issued.id == card.id and issued.status == "superseded" for issued in ws.issued)
        status = "（已被新通告缩窄，需替换；送达记录留存）" if superseded else ""
        print(f"     {card.id}（{card.published}）{status}：{card.text}"
              f"｜送达：{'、'.join(card.copied_viewers) or '无送达记录'}")
        for note in detail.received_audit.get(card.id, []):
            print(f"       审计：{note}")
    print("   当前仍缺的澄清：")
    if not detail.missing:
        print("     （无）")
    for line in detail.missing:
        print(f"     - {line}")
    print("   不能与其它位置合用提示的理由：")
    if not detail.cannot_share_reasons:
        print("     （无冲突）")
    for other_id, reason in detail.cannot_share_reasons.items():
        print(f"     - 与 {other_id} 的卡：{reason}")


def _print_plan(ws: ClarifyWorkspace) -> None:
    for plan in ws.plan_all_slots():
        print(f"\n== 版面 {plan.slot_id}｜{plan.surface}｜卡位 {plan.capacity}｜上限 {plan.max_chars} 字 ==")
        for card in plan.chosen:
            print(f"   发：{card.text}（{card.char_count} 字）")
            print(f"       通告依据：{'、'.join(card.bulletin_refs)}｜挂位：{'、'.join(card.target_displays)}")
        for card, reason in plan.deferred:
            print(f"   顺延：{card.text} —— {reason}")


def _print_reevaluate(ws: ClarifyWorkspace) -> None:
    for finding in ws.reevaluate_after_new_bulletin():
        print(f"\n== 旧卡 {finding['card_id']}｜状态 {finding['status']} ==")
        for line in finding["overreach"]:
            print(f"   需替换：{line}")
        for line in finding["reusable_facts"]:
            print(f"   可继续使用：{line}")
        for display_id, note in finding["per_display"].items():
            print(f"   位置 {display_id}：{note}")
        print(f"   已送达记录照实保留：{'、'.join(finding['copied_viewers_retained']) or '无'}")
        print(f"   仍需补发覆盖的读者：{'、'.join(finding['still_needed_viewers']) or '无'}")


def _print_bulletins(ws: ClarifyWorkspace) -> None:
    for clause in ws.bulletin_clauses:
        print(f"\n[{clause.bulletin_id}｜{clause.verdict}] {clause.text}")
        print(f"   范围：{_scope_text(clause.scope)}")
        if clause.note:
            print(f"   说明：{clause.note}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="澄清稿与二次传播的主张复核（仅本地合成资料）")
    parser.add_argument("path", type=Path, help="records.json 路径")
    parser.add_argument("--state", type=Path, default=Path("workspace_state.json"),
                        help="编辑工作流状态文件（与原始资料分离）")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("claims", help="列出各版本辨出的说法与限定条件")
    sub.add_parser("bulletins", help="列出通告子范围与处置")
    sub.add_parser("positions", help="按传播位置汇总说法、可更正部分与剩余异议")
    detail = sub.add_parser("position", help="打开一个传播位置的完整核对单")
    detail.add_argument("display_id")
    sub.add_parser("plan", help="按卡位与字数规划本窗更正卡")
    sub.add_parser("reevaluate", help="新通告缩窄后重判旧卡")
    sub.add_parser("screenshots", help="列出无法定位版本、待编辑核对的截图")
    approve = sub.add_parser("approve", help="编辑批准并发出为某位置起草的更正卡")
    approve.add_argument("display_id")
    approve.add_argument("--card-id", required=True)
    approve.add_argument("--published", required=True, help="发卡时间（ISO 8601）")
    approve.add_argument("--viewers", nargs="*", default=[], help="实际送达读者名单")
    sub.add_parser("json", help="输出工作台结构化 JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    records = load_records(args.path)
    ws = ClarifyWorkspace(records)
    ws.load_state(args.state)

    if args.command == "claims":
        _print_claims(ws)
    elif args.command == "bulletins":
        _print_bulletins(ws)
    elif args.command == "positions":
        _print_positions(ws)
    elif args.command == "position":
        if args.display_id not in ws.display_by_id:
            print(f"未知位置 {args.display_id}", file=sys.stderr)
            return 2
        _print_position_detail(ws, args.display_id)
    elif args.command == "plan":
        _print_plan(ws)
    elif args.command == "reevaluate":
        _print_reevaluate(ws)
        ws.save_state(args.state)
        print(f"\n工作流状态已写入 {args.state}（原始资料未改动）")
    elif args.command == "screenshots":
        for row in ws.unverifiable_screenshots():
            print(json.dumps(row, ensure_ascii=False))
    elif args.command == "approve":
        if args.display_id not in ws.display_by_id:
            print(f"未知位置 {args.display_id}", file=sys.stderr)
            return 2
        card = ws.draft_for_display(args.display_id)
        if card is None:
            print(f"位置 {args.display_id} 没有需要发出的更正卡", file=sys.stderr)
            return 1
        slot = ws._slot_of(args.display_id)
        if slot and card.char_count > slot["max_chars"]:
            print(f"文案 {card.char_count} 字超过版面上限 {slot['max_chars']} 字，拒绝发卡", file=sys.stderr)
            return 1
        issued = ws.approve(card, args.published, args.viewers, args.card_id)
        ws.save_state(args.state)
        print(f"已发卡 {issued.id}：{issued.text}（{len(issued.text)} 字）")
        print(f"挂位：{'、'.join(issued.target_displays)}｜送达：{'、'.join(issued.copied_viewers) or '无'}")
    elif args.command == "json":
        print(json.dumps({
            "claims": [asdict(claim) for claims in ws.claims_by_revision.values() for claim in claims],
            "bulletin_clauses": [asdict(clause) for clause in ws.bulletin_clauses],
            "unverifiable": ws.unverifiable_screenshots(),
        }, ensure_ascii=False, indent=2, default=tuple))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
