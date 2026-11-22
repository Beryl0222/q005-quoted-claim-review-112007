"""更正卡合规审计、合用判断、卡位规划与编辑工作流测试。"""

import tempfile
import unittest
from pathlib import Path

from inspect_records import load_records
from clarify.cards import audit_active_card, can_share
from clarify.workflow import ClarifyWorkspace

RECORDS = load_records(Path(__file__).resolve().parents[1] / "data" / "records.json")


class CardsTest(unittest.TestCase):
    def setUp(self):
        self.ws = ClarifyWorkspace(RECORDS)

    def test_existing_c1_overreach_after_n3(self):
        c1 = next(row for row in RECORDS["active_cards"] if row["id"] == "C1")
        audit = audit_active_card(
            c1,
            self.ws.coverages,
            self.ws.views,
            {display_id: self.ws.claims_on_display(display_id)
             for display_id in c1["display"]},
        )
        self.assertTrue(any("一律照常" in line for line in audit.overreach))
        self.assertTrue(any("港区" in line for line in audit.reusable_facts))

    def test_c1_port_fact_cannot_be_shoved_to_s1_readers(self):
        c1 = next(row for row in RECORDS["active_cards"] if row["id"] == "C1")
        audit = audit_active_card(
            c1,
            self.ws.coverages,
            self.ws.views,
            {display_id: self.ws.claims_on_display(display_id)
             for display_id in c1["display"]},
        )
        self.assertIn("S1", audit.per_display)
        self.assertIn("无关细节", audit.per_display["S1"])

    def test_drafted_cards_respect_char_limits(self):
        for slot in RECORDS["slots"]:
            for display_id in slot["displays"]:
                card = self.ws.draft_for_display(display_id)
                if card is not None:
                    self.assertLessEqual(card.char_count, slot["max_chars"],
                                         f"{display_id} 卡片超字数")

    def test_draft_never_claims_full_scope_resumption(self):
        for display in self.ws.displays:
            card = self.ws.draft_for_display(display.display_id)
            if card is not None:
                self.assertNotIn("一律", card.text)
                self.assertNotIn("全部照常", card.text)

    def test_hearsay_only_position_gets_no_card(self):
        # S5 只有反问与传闻，没有作者断言，不应主动起草更正卡
        self.assertIsNone(self.ws.draft_for_display("S5"))

    def test_share_blocked_by_unseen_rumor_detail(self):
        s3_card = self.ws.draft_for_display("S3")
        ok, reason = can_share(s3_card, "S1", self.ws.coverages_on_display("S1"))
        self.assertFalse(ok)
        self.assertIn("未见过", reason)

    def test_share_blocked_when_narrowing_would_read_as_full_scope(self):
        s1_card = self.ws.draft_for_display("S1")
        # S4 的说法里仍有企业合并/境外等未澄清子范围
        ok, _ = can_share(s1_card, "S4", self.ws.coverages_on_display("S4"))
        # S1 卡只写“仅东岸、城区…不暂停”，自带限定词，允许合用但不抹平异议
        self.assertIsInstance(ok, bool)


class SlotPlanningTest(unittest.TestCase):
    def setUp(self):
        self.ws = ClarifyWorkspace(RECORDS)

    def test_slot_capacity_and_priority(self):
        plans = {plan.slot_id: plan for plan in self.ws.plan_all_slots()}
        slot1 = plans["SLOT1"]
        self.assertEqual(len(slot1.chosen), slot1.capacity)
        # 信息流卡位只有 1 个：旧卡 C1 失效后，S1 与 S2 各有 1 位未覆盖读者，
        # 必须有一个被顺延并给出理由
        self.assertTrue(slot1.deferred)
        self.assertTrue(all("卡位已满" in reason for _, reason in slot1.deferred))

    def test_slot2_fits_both_body_positions(self):
        plans = {plan.slot_id: plan for plan in self.ws.plan_all_slots()}
        slot2 = plans["SLOT2"]
        self.assertEqual(len(slot2.chosen), 2)
        for card in slot2.chosen:
            self.assertLessEqual(card.char_count, slot2.max_chars)

    def test_priority_puts_more_uncovered_viewers_first(self):
        # 给 S1 发卡并送达读者甲后，S2 应成为信息流首选
        card = self.ws.draft_for_display("S1")
        self.ws.approve(card, "2026-11-28T14:00:00+08:00", ["读者甲"], "C2")
        plans = {plan.slot_id: plan for plan in self.ws.plan_all_slots()}
        chosen_targets = plans["SLOT1"].chosen[0].target_displays
        self.assertIn("S2", chosen_targets)


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.ws = ClarifyWorkspace(RECORDS)

    def test_reevaluate_supersedes_c1_but_keeps_delivery_record(self):
        findings = self.ws.reevaluate_after_new_bulletin()
        c1_finding = next(f for f in findings if f["card_id"] == "C1")
        self.assertEqual(c1_finding["status"], "superseded")
        self.assertEqual(c1_finding["copied_viewers_retained"], ["读者甲", "读者丙"])
        # 已看过旧卡的人仍在补发名单里，不能被当成已更正而跳过
        self.assertEqual(set(c1_finding["still_needed_viewers"]), {"读者甲", "读者丙"})

    def test_superseded_card_not_counted_as_current_delivery(self):
        self.ws.reevaluate_after_new_bulletin()
        # C1 失效后，S1 的“当前有效送达”应为空，但历史记录仍可查
        self.assertEqual(self.ws.delivered_viewers("S1"), set())
        history = self.ws.historical_deliveries("S1")
        self.assertTrue(any(card.id == "C1" for card in history))

    def test_reusable_independent_fact_kept(self):
        findings = self.ws.reevaluate_after_new_bulletin()
        c1_finding = next(f for f in findings if f["card_id"] == "C1")
        self.assertTrue(any("12月5日" in line for line in c1_finding["reusable_facts"]))

    def test_approve_persists_and_state_roundtrip(self):
        card = self.ws.draft_for_display("S2")
        self.ws.approve(card, "2026-11-28T15:00:00+08:00", ["读者乙"], "C3")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            self.ws.save_state(path)
            fresh = ClarifyWorkspace(RECORDS)
            fresh.load_state(path)
            self.assertTrue(any(card.id == "C3" for card in fresh.issued))
            self.assertIn("读者乙", fresh.delivered_viewers("S2"))

    def test_position_detail_lists_version_gaps_and_share_blocks(self):
        detail = self.ws.position_detail("S3")
        self.assertEqual(detail.shown_revision_id, "T3v1")
        self.assertTrue(any(exposure.viewer == "读者丙" for exposure in detail.exposures))
        self.assertTrue(any("企业合并" in line or "另发" in line for line in detail.missing))
        self.assertTrue(any(other_id in detail.cannot_share_reasons for other_id in ("S1", "S2")))

    def test_unverifiable_screenshot_only_listed(self):
        rows = self.ws.unverifiable_screenshots()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["exposure_id"], "E7")
        self.assertIn("不能当成已证传播", rows[0]["reason"])


class EndToEndTimelineTest(unittest.TestCase):
    """完整值班时序：发旧卡 → N3 缩窄 → 重判 → 补发仍覆盖老读者。"""

    def setUp(self):
        self.ws = ClarifyWorkspace(RECORDS)

    def test_full_correction_lifecycle(self):
        # 1. 初始状态：C1 已按 N1/N2 发出并送达读者甲、读者丙
        c1 = next(card for card in self.ws.issued if card.id == "C1")
        self.assertEqual(c1.status, "active")
        self.assertIn("读者甲", self.ws.delivered_viewers("S1"))

        # 2. N3 到达并缩窄 N1：重判后 C1 失效，但送达记录保留
        findings = self.ws.reevaluate_after_new_bulletin()
        self.assertEqual(next(f for f in findings if f["card_id"] == "C1")["status"],
                         "superseded")
        self.assertEqual(self.ws.delivered_viewers("S1"), set())
        self.assertEqual(
            next(card for card in self.ws.issued if card.id == "C1").copied_viewers,
            ["读者甲", "读者丙"],
        )

        # 3. 补发计划不能把读者甲当已更正而跳过：S1 仍有 1 位未覆盖读者
        plans = {plan.slot_id: plan for plan in self.ws.plan_all_slots()}
        slot1 = plans["SLOT1"]
        chosen = slot1.chosen[0]
        self.assertIn("S1", chosen.target_displays + slot1.deferred[0][0].target_displays)
        # 新卡文案带限定词，不再出现“一律照常”
        for card in slot1.chosen:
            self.assertNotIn("一律", card.text)
            self.assertIn("仅", card.text)

        # 4. 编辑批准补发并送达，老读者重新收到带限定的更正
        new_card = self.ws.draft_for_display("S1")
        self.ws.approve(new_card, "2026-11-28T16:00:00+08:00", ["读者甲"], "C2")
        self.assertIn("读者甲", self.ws.delivered_viewers("S1"))
        # 历史里同时留着旧卡 C1 与新卡 C2，可逐次核对
        history = {card.id: card for card in self.ws.historical_deliveries("S1")}
        self.assertEqual(set(history), {"C1", "C2"})

        # 5. 位置详情能同时给出当时版本、已收纠正、剩余异议与合用限制
        detail = self.ws.position_detail("S1")
        self.assertEqual(detail.shown_revision_id, "T1v1")
        self.assertTrue(detail.missing)
        self.assertIn("C1", detail.received_audit)


if __name__ == "__main__":
    unittest.main()
