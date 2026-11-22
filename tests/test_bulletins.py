"""通告子范围解析与覆盖判断：否定、附条件、无结论、后续缩窄。"""

import unittest
from pathlib import Path

from inspect_records import load_records
from clarify.analyzer import analyze_packet
from clarify.bulletins import evaluate_all, parse_bulletins
from clarify.models import VERDICT_NEGATED, VERDICT_OPEN, VERDICT_QUALIFIED

RECORDS = load_records(Path(__file__).resolve().parents[1] / "data" / "records.json")


class BulletinTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.clauses = parse_bulletins(RECORDS)
        cls.claims = analyze_packet(RECORDS)
        cls.coverage = evaluate_all(cls.claims, cls.clauses)

    def _clause(self, bulletin_id, verdict):
        return next(c for c in self.clauses
                    if c.bulletin_id == bulletin_id and c.verdict == verdict)

    def test_bulletin_split_into_scoped_clauses(self):
        n1_negated = self._clause("N1", VERDICT_NEGATED)
        self.assertEqual(set(n1_negated.scope.places), {"城区", "东岸"})
        n1_open = self._clause("N1", VERDICT_OPEN)
        self.assertIn("境外", set(n1_open.scope.places))
        n2_negated = self._clause("N2", VERDICT_NEGATED)
        self.assertIn("西岸旧街", set(n2_negated.scope.places))
        n2_qualified = self._clause("N2", VERDICT_QUALIFIED)
        self.assertIn("自助柜", set(n2_qualified.scope.businesses))

    def test_n3_is_qualifying_narrowing_not_flat_open(self):
        n3 = self._clause("N3", VERDICT_QUALIFIED)
        self.assertIn("个人营业", set(n3.scope.populations))
        self.assertTrue(n3.note.startswith("缩窄通告 N1"))

    def test_n1_negation_survives_n3_for_individual_scope(self):
        # 全范围“一律暂停”说法：个人营业子范围仍可据 N1 更正
        claim = next(c for c in self.claims["T1v1"] if "一律暂停备案" in c.text)
        item = self.coverage[claim.id]
        self.assertIn(self._clause("N1", VERDICT_NEGATED), item.corrected)
        self.assertIn(self._clause("N3", VERDICT_QUALIFIED), item.qualifiers)

    def test_enterprise_merger_remains_open_after_narrowing(self):
        claim = next(c for c in self.claims["T1v1"] if "一律暂停备案" in c.text)
        item = self.coverage[claim.id]
        open_remainders = [r for r in item.remainders if r.reason.startswith("open")]
        self.assertTrue(open_remainders)
        self.assertTrue(any("企业合并" in r.reason for r in open_remainders))

    def test_overseas_window_has_no_conclusion(self):
        open_clause = self._clause("N1", VERDICT_OPEN)
        self.assertIn("境外", set(open_clause.scope.places))
        self.assertIn("代办", set(open_clause.scope.populations))

    def test_west_bank_counter_closure_negated(self):
        claim = next(c for c in self.claims["T1v1"] if "西岸柜台" in c.text)
        item = self.coverage[claim.id]
        bulletin_ids = {c.bulletin_id for c in item.corrected}
        self.assertIn("N2", bulletin_ids)

    def test_permanent_removal_only_qualified_as_temporary_suspension(self):
        claim = next(c for c in self.claims["T3v1"] if "永久撤除" in c.text)
        item = self.coverage[claim.id]
        self.assertTrue(any(c.bulletin_id == "N2" for c in item.qualifiers))
        self.assertTrue(any("narrowed_remainder" in r.reason for r in item.remainders))

    def test_rhetorical_question_about_other_regions_stays_open(self):
        claim = next(c for c in self.claims["T4v1"] if "停了吗" in c.text)
        item = self.coverage[claim.id]
        self.assertEqual(item.corrected, ())
        self.assertTrue(any("open" in r.reason for r in item.remainders))

    def test_no_full_scope_correction_for_broad_claim(self):
        # 任何说法都不应出现“完全纠正且无剩余异议”的全范围结论
        for claim_id, item in self.coverage.items():
            if "暂停备案" in item.claim.text or "备案暂停" in item.claim.text:
                self.assertFalse(
                    item.fully_corrected,
                    f"{claim_id} 不应被判定为全范围已澄清",
                )


if __name__ == "__main__":
    unittest.main()
