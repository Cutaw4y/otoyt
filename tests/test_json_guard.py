"""Regression tests for actual November history conflict and fail-closed guard."""
import json
import tempfile
import unittest
from pathlib import Path

import json_guard


class JsonGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, relative, value):
        p = self.root / relative
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(value, encoding="utf-8")
        return p

    def test_conflict_markers_never_parse_as_history(self):
        broken = '''[
{"type":"reel", "video_id":"abc"},
<<<<<<< Updated upstream
{"type":"short", "video_id":"def"},
=======
{"type":"short", "video_id":"ghi"},
>>>>>>> Stashed changes
{"type":"reel", "video_id":"jkl"}
]'''
        self.write("history.json", broken)
        with self.assertRaisesRegex(ValueError, "unresolved Git merge conflict"):
            json_guard.validate_repo(self.root)

    def test_wrong_json_shape_rejected(self):
        self.write("history.json", "{}")
        with self.assertRaisesRegex(ValueError, "unexpected JSON root type"):
            json_guard.validate_repo(self.root)

    def test_invalid_json_kept_byte_for_byte(self):
        file = self.write("history.json", "[{")
        before = file.read_bytes()
        with self.assertRaisesRegex(ValueError, "invalid JSON"):
            json_guard.validate_repo(self.root)
        self.assertEqual(before, file.read_bytes())

    def test_missing_optional_files_dont_block(self):
        self.assertEqual([], json_guard.validate_repo(self.root))

    def test_two_channels_valid_preserves_every_item(self):
        en = [{"type": "long", "video_id": "abcdefghijk"},
              {"type": "short", "video_id": "lmnopqrstuv", "parent": "abcdefghijk"}]
        tr = [{"type": "long", "video_id": "ABCDEFGHIJK"}]
        self.write("history.json", json.dumps(en))
        self.write("data/golgedefter/history.json", json.dumps(tr))
        self.write("ig_queue.json", "[]")
        checked = json_guard.validate_repo(self.root)
        self.assertEqual(3, len(checked))
        self.assertEqual(en, json.loads((self.root / "history.json").read_text()))
        self.assertEqual(tr, json.loads((self.root / "data/golgedefter/history.json").read_text()))

    def test_real_history_repair_preserves_all_distinct_upload_ids(self):
        real = Path(__file__).resolve().parents[1] / "history.json"
        rows = json.loads(real.read_text(encoding="utf-8"))
        expected = {"XGbn1i8ALlA", "YSpB3wnb6nA",
                    "6ZAsvPuQuGE", "pJ4tUNbxKUw",
                    "18164505271501341", "18116351437858771"}
        ids = [row["video_id"] for row in rows]
        self.assertTrue(expected.issubset(set(ids)))
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(24, len(rows))


if __name__ == "__main__":
    unittest.main()
