"""文本辨识与限定条件保留的规则测试。"""

import unittest
from pathlib import Path

from inspect_records import load_records
from clarify.analyzer import analyze_packet, analyze_field, extract_scope
from clarify.models import (
    SAY_ASSERTION,
    SAY_CORRECTION,
    SAY_HEARSAY,
    SAY_QUESTION,
    SAY_REFUTATION,
)

RECORDS = load_records(Path(__file__).resolve().parents[1] / "data" / "records.json")


class AnalyzerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.claims = analyze_packet(RECORDS)

    def _find(self, revision_id, text_fragment):
        for claim in self.claims[revision_id]:
            if text_fragment in claim.text:
                return claim
        self.fail(f"{revision_id} 中找不到包含 {text_fragment} 的说法")

    def test_headline_author_assertion(self):
        claim = self._find("T1v1", "一律暂停备案")
        self.assertEqual(claim.kind, SAY_ASSERTION)
        self.assertTrue(claim.is_author_assertion)

    def test_hearsay_is_not_author_assertion(self):
        claim = self._find("T1v1", "消息称")
        self.assertEqual(claim.kind, SAY_HEARSAY)
        self.assertFalse(claim.is_author_assertion)
        self.assertEqual(claim.speaker, "消息称")
        # 转引同一标题：T2 里的引述句是传闻，不是 T2 作者的断言
        quoted = self._find("T2v1", "网传")
        self.assertEqual(quoted.kind, SAY_HEARSAY)
        self.assertTrue(quoted.quoted)

    def test_rhetorical_question_is_neutral(self):
        claim = self._find("T4v1", "停了吗")
        self.assertEqual(claim.kind, SAY_QUESTION)
        self.assertEqual(claim.polarity, "neutral")
        self.assertFalse(claim.is_author_assertion)

    def test_explicit_refutation_and_correction(self):
        refute = self._find("T1v2", "没有查到关闭通知")
        self.assertEqual(refute.kind, SAY_REFUTATION)
        self.assertEqual(refute.polarity, "negative")
        correction = self._find("T1v2", "改用新表")
        self.assertEqual(correction.kind, SAY_CORRECTION)

    def test_qualifiers_are_preserved(self):
        claim = self._find("T1v1", "一律暂停备案")
        self.assertEqual(set(claim.scope.places), {"沿岸"})
        self.assertEqual(set(claim.scope.populations), {"小微商户"})
        self.assertEqual(set(claim.scope.periods), {"下月"})
        self.assertIn("备案", set(claim.scope.businesses))
        self.assertIn("一律", set(claim.scope.modalities))
        self.assertIn("暂停", set(claim.scope.modalities))

    def test_scope_extraction_keeps_place_and_business(self):
        claims = analyze_field("Xv1", "body", "另说西岸柜台已到期关闭")
        scope = claims[0].scope
        self.assertEqual(set(scope.places), {"西岸"})
        self.assertIn("柜台", set(scope.businesses))
        self.assertIn("到期", set(scope.periods))

    def test_empty_scope_means_unrestricted(self):
        self.assertTrue(extract_scope("澄清了").is_empty())


if __name__ == "__main__":
    unittest.main()
