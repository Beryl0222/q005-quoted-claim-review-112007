"""传播解析：冻结/动态引用、渠道可见性、无法定位截图。"""

import unittest
from pathlib import Path

from inspect_records import load_records
from clarify.analyzer import analyze_packet
from clarify.propagation import (
    DYNAMIC,
    FROZEN,
    resolve_displays,
    resolve_exposures,
    resolve_link_target,
    viewers_per_display,
    viewers_who_saw_claim,
)

RECORDS = load_records(Path(__file__).resolve().parents[1] / "data" / "records.json")


class PropagationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.claims = analyze_packet(RECORDS)
        cls.displays = resolve_displays(RECORDS, cls.claims)
        cls.views = resolve_exposures(RECORDS, cls.displays, cls.claims)
        cls.display_by_id = {d.display_id: d for d in cls.displays}

    def test_frozen_link_keeps_old_revision(self):
        link = next(link for link in RECORDS["links"] if link["id"] == "L1")
        self.assertEqual(link["mode"], FROZEN)
        # 冻结引用无论何时都解析到被冻结的 T1v1
        target = resolve_link_target(link, "2030-01-01T00:00:00+08:00", RECORDS)
        self.assertEqual(target, "T1v1")
        self.assertEqual(self.display_by_id["S4"].linked_revision_id, "T1v1")

    def test_dynamic_link_follows_latest_revision(self):
        link = next(link for link in RECORDS["links"] if link["id"] == "L2")
        self.assertEqual(link["mode"], DYNAMIC)
        target = resolve_link_target(link, "2026-11-28T12:15:00+08:00", RECORDS)
        self.assertEqual(target, "T1v2")

    def test_headline_only_surface_hides_body_clarification(self):
        # S1 只展示 headline：T1v1 正文里的内容不可见
        s1 = self.display_by_id["S1"]
        self.assertEqual(s1.visible_fields, ("headline",))
        self.assertTrue(all(claim.field == "headline" for claim in s1.claims))

    def test_body_page_sees_both_fields(self):
        s3 = self.display_by_id["S3"]
        fields = {claim.field for claim in s3.claims}
        self.assertEqual(fields, {"headline", "body"})

    def test_quoted_headline_does_not_create_second_assertion(self):
        # S4 正文里“网传「...暂停备案」”是传闻；T2 作者没有背书“暂停备案”这一断言
        s4_view = next(view for view in self.views if view.exposure_id == "E5")
        hearsay = [c for c in s4_view.visible_claims if c.kind == "hearsay"]
        self.assertTrue(any("网传" in c.text for c in hearsay))
        # T2v1 自己的作者断言只有“暂停备案？澄清了”这个标题片段，
        # 完整的“一律暂停备案”说法在 S4 上只能以传闻身份出现
        full_claim = [c for c in s4_view.visible_claims
                      if c.is_author_assertion and "一律" in c.text]
        self.assertEqual(full_claim, [])

    def test_old_display_not_reattributed_to_new_revision(self):
        # E1 在 T1v1 时期展示；后来 T1 出了 v2，E1 仍归 T1v1
        e1 = next(view for view in self.views if view.exposure_id == "E1")
        self.assertEqual(e1.revision_id, "T1v1")
        self.assertTrue(e1.located)

    def test_dynamic_card_exposure_records_resolved_version(self):
        e3 = next(view for view in self.views if view.exposure_id == "E3")
        self.assertEqual(e3.linked_revision_id, "T1v2")

    def test_unlocatable_screenshot_is_flagged_not_counted(self):
        e7 = next(view for view in self.views if view.exposure_id == "E7")
        self.assertTrue(e7.unverifiable)
        self.assertFalse(e7.located)
        self.assertEqual(e7.visible_claims, ())
        per_display = viewers_per_display(self.views)
        # 读者戊不能算作任何说法的已证传播对象
        self.assertNotIn("读者戊", set().union(*per_display.values()))

    def test_viewers_who_saw_claim(self):
        headline_claim = next(c for c in self.claims["T3v1"] if c.field == "headline")
        viewers = viewers_who_saw_claim(self.views, headline_claim.id)
        self.assertEqual(viewers, {"读者乙", "读者丙", "读者甲"})


if __name__ == "__main__":
    unittest.main()
