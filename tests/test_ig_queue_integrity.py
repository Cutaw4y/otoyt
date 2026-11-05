"""No-network Instagram queue integrity and secret-redaction regression tests."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ig_queue


class QueueIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.queue = self.root / "ig_queue.json"
        self.history = self.root / "history.json"

    def test_corrupted_existing_queue_does_not_look_empty(self):
        self.queue.write_text("{CORRUPTED", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "KORUNDU"):
            ig_queue.load(self.queue, [])
        self.assertEqual("{CORRUPTED", self.queue.read_text(encoding="utf-8"))

    def test_wrong_json_shape_is_rejected(self):
        self.queue.write_text('{"not":"a list"}', encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "liste değil"):
            ig_queue.load(self.queue, [])

    def test_missing_queue_is_harmless_empty(self):
        self.assertEqual([], ig_queue.load(self.queue, []))

    def test_save_is_atomic_on_replace_failure(self):
        self.queue.write_text('[{"asset": "old.mp4"}]', encoding="utf-8")
        before = self.queue.read_bytes()
        with mock.patch.object(ig_queue.os, "replace", side_effect=OSError("crash")):
            with self.assertRaises(OSError):
                ig_queue.save(self.queue, [{"asset": "new.mp4"}])
        self.assertEqual(before, self.queue.read_bytes())
        self.assertFalse((self.root / "ig_queue.json.tmp").exists())

    def test_successful_save_keeps_valid_json(self):
        data = [{"asset": "x.mp4", "caption": "test"}]
        ig_queue.save(self.queue, data)
        self.assertEqual(data, json.loads(self.queue.read_text(encoding="utf-8")))

    def test_error_message_redacts_multiple_secrets(self):
        with mock.patch.dict(os.environ, {"IG_ACCESS_TOKEN": "SecretLongAccessTokenXYZ",
                                          "TELEGRAM_BOT_TOKEN": "LongTelegramToken"}):
            result = ig_queue.safe_error(
                "SecretLongAccessTokenXYZ access_token=abcSecretDef "
                "Authorization Bearer abcde123456 LongTelegramToken"
            )
        for secret in ("SecretLongAccessTokenXYZ", "abcSecretDef",
                       "abcde123456", "LongTelegramToken"):
            self.assertNotIn(secret, result)
        self.assertIn("[REDACTED]", result)

    def test_already_published_queue_entry_never_posts_again(self):
        item = {"path": "clip.mp4", "title": "One", "caption": "test"}
        self.queue.write_text(json.dumps([item]), encoding="utf-8")
        self.history.write_text(json.dumps([{"type": "reel", "queue_key": "local:clip.mp4",
                                            "video_id": "already_uploaded"}]), encoding="utf-8")
        with mock.patch.object(ig_queue, "ROOT", self.root), \
             mock.patch.object(ig_queue, "DATA", self.root), \
             mock.patch.object(ig_queue, "QUEUE", self.queue), \
             mock.patch.object(ig_queue, "HISTORY", self.history), \
             mock.patch.object(ig_queue, "CFG", {"instagram_enabled": True}), \
             mock.patch.object(ig_queue, "notify"), \
             mock.patch.object(ig_queue.publish, "instagram_reel") as publish, \
             mock.patch.dict(os.environ, {"IG_ACCESS_TOKEN": "dummy", "IG_USER_ID": "123"}):
            ig_queue.main()
            publish.assert_not_called()
        self.assertEqual([], json.loads(self.queue.read_text(encoding="utf-8")))

    def test_failed_publish_log_never_exposes_access_token(self):
        secret = "VerySecretIGAccessToken012345"
        (self.root / "clip.mp4").write_bytes(b"video")
        item = {"path": "clip.mp4", "title": "One", "caption": "test"}
        self.queue.write_text(json.dumps([item]), encoding="utf-8")
        self.history.write_text("[]", encoding="utf-8")
        with mock.patch.object(ig_queue, "ROOT", self.root), \
             mock.patch.object(ig_queue, "DATA", self.root), \
             mock.patch.object(ig_queue, "QUEUE", self.queue), \
             mock.patch.object(ig_queue, "HISTORY", self.history), \
             mock.patch.object(ig_queue, "CFG", {"instagram_enabled": True}), \
             mock.patch.object(ig_queue, "notify"), \
             mock.patch.object(ig_queue.publish, "ig_link_cta", return_value="caption"), \
             mock.patch.object(ig_queue.publish, "instagram_reel",
                               side_effect=RuntimeError("failed token=" + secret)), \
             mock.patch.dict(os.environ, {"IG_ACCESS_TOKEN": secret, "IG_USER_ID": "123"}):
            with self.assertRaises(SystemExit):
                ig_queue.main()
        recorded = (self.root / "ig_last_error.txt").read_text(encoding="utf-8")
        self.assertNotIn(secret, recorded)
        self.assertIn("[REDACTED]", recorded)
        self.assertEqual([item], json.loads(self.queue.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
