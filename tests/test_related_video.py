"""No-network tests: Related video Studio links are never claimed as applied."""
import json
import tempfile
import unittest
from pathlib import Path

import related_video


class RelatedVideoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "data/golgedefter").mkdir(parents=True, exist_ok=True)

    def history(self, data, channel="en"):
        base = self.root if channel == "en" else self.root / "data/golgedefter"
        (base / "history.json").write_text(json.dumps(data), encoding="utf-8")
        return base

    def example(self):
        return [
            {"type": "long", "video_id": "abcdefghijk", "title": "Long"},
            {"type": "short", "video_id": "sh0rts12345", "parent": "abcdefghijk", "title": "Clip"},
            {"type": "short", "video_id": "oLdshort001", "manual": True, "title": "old manual"},
        ]

    def test_exact_studio_link_to_original_short(self):
        row = related_video.task("sh0rts12345", "abcdefghijk", "Short", "Gölgedefter")
        self.assertEqual("https://studio.youtube.com/video/sh0rts12345/edit", row["studio_url"])
        self.assertEqual("https://youtu.be/abcdefghijk", row["parent_url"])
        self.assertEqual("studio_unverified", row["state"])

    def test_invalid_video_ids_are_rejected(self):
        for sid in ("a", "b" * 12, "https://youtu.be/abcdefghijk", "../evil1234"):
            with self.assertRaises(ValueError):
                related_video.validate(sid)

    def test_no_self_links(self):
        with self.assertRaises(ValueError):
            related_video.task("abcdefghijk", "abcdefghijk", "Self", "Channel")

    def test_register_once_without_duplicate_or_mutation(self):
        path = self.root / "related_video_tasks.json"
        self.assertTrue(related_video.register(path, "sh0rts12345", "abcdefghijk", "Short", "EN"))
        original = path.read_bytes()
        self.assertFalse(related_video.register(path, "sh0rts12345", "abcdefghijk", "Changed", "EN"))
        self.assertEqual(original, path.read_bytes())
        self.assertEqual(1, len(json.loads(original)))

    def test_same_short_cannot_be_retargeted_silently(self):
        path = self.root / "related_video_tasks.json"
        related_video.register(path, "sh0rts12345", "abcdefghijk", "Short", "EN")
        with self.assertRaisesRegex(ValueError, "iki farklı"):
            related_video.register(path, "sh0rts12345", "different01", "Wrong", "EN")

    def test_old_unlinked_manual_shorts_not_guessed(self):
        base = self.history(self.example())
        rows = related_video.history_links(base / "history.json", "EN")
        self.assertEqual(1, len(rows))
        self.assertEqual("sh0rts12345", rows[0]["short_id"])

    def test_unknown_parent_is_not_assumed(self):
        base = self.history([{"type": "short", "video_id": "sh0rts12345", "parent": "abcdefghijk"}])
        self.assertEqual([], related_video.history_links(base / "history.json", "EN"))

    def test_default_collection_never_writes(self):
        self.history(self.example())
        self.history([], "tr")
        rows = related_video.collect(self.root)
        self.assertEqual(1, len(rows))
        self.assertFalse((self.root / "related_video_tasks.json").exists())

    def test_sync_creates_two_separate_channel_queues(self):
        self.history(self.example())
        self.history([
            {"type": "long", "video_id": "1234567890A"},
            {"type": "short", "video_id": "0123456789A", "parent": "1234567890A", "title": "TR"},
        ], "tr")
        rows = related_video.collect(self.root, sync=True)
        self.assertEqual(2, len(rows))
        self.assertEqual(1, len(related_video.read_list(self.root / "related_video_tasks.json")))
        self.assertEqual(1, len(related_video.read_list(self.root / "data/golgedefter/related_video_tasks.json")))
        related_video.collect(self.root, sync=True)
        self.assertEqual(1, len(related_video.read_list(self.root / "related_video_tasks.json")))

    def test_report_does_not_claim_youtube_setting_completed(self):
        text = related_video.markdown([related_video.task("sh0rts12345", "abcdefghijk", "Test", "EN")])
        self.assertIn("Studio'da doğrulanmadı", text)
        self.assertIn("EKLEMEZ", text)
        self.assertIn("studio.youtube.com/video/sh0rts12345/edit", text)


if __name__ == "__main__":
    unittest.main()
