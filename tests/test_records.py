"""核对随档资料可以读取，以及原始记录引用完整。"""

from pathlib import Path
import tempfile
import unittest

from inspect_records import load_records


class RecordsTest(unittest.TestCase):
    def setUp(self):
        self.data = load_records(Path(__file__).resolve().parents[1] / "data" / "records.json")

    def test_record_references(self):
        for source, field, target in [('display', 'revision', 'revision'), ('decisions', 'display', 'display'), ('decisions', 'ref', 'bulletin'), ('appeal', 'decision', 'decisions')]:
            group, _, key = target.partition(".")
            keys = {row[key or "id"] for row in self.data[group]}
            for row in self.data[source]:
                refs = row.get(field)
                if refs is None:
                    continue
                refs = refs if isinstance(refs, list) else [refs]
                with self.subTest(kind=source, record=row["id"], field=field):
                    self.assertLessEqual(set(refs), keys)

    def test_duplicate_id_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.json"
            path.write_text('{"组":[{"id":"1"},{"id":"1"}]}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "编号重复"):
                load_records(path)

    def test_source_packet(self):
        post_ids = {item["post"] for item in self.data["revision"]}
        rev_ids = {item["id"] for item in self.data["revision"]}
        for link in self.data["links"]:
            self.assertIn(link["target"], rev_ids if link["mode"] == "冻结引用" else post_ids)
        self.assertGreater(len(self.data["decisions"]), 1)
        self.assertIn(["headline"], [item["visible"] for item in self.data["display"]])


if __name__ == "__main__":
    unittest.main()
