"""Safety regression tests. No YouTube/Instagram API calls and no real publishing."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("GITHUB_TOKEN", "unit-test-token")

import ai
import ig_queue
import pipeline
import publish
import watchdog


class InstagramGuards(unittest.TestCase):
    def test_wrong_ig_account_is_rejected(self):
        response = mock.Mock(ok=True)
        response.json.return_value = {"id": "321", "username": "somebody_else"}
        with mock.patch.dict(os.environ, {"IG_ACCESS_TOKEN": "IG_example", "IG_USER_ID": "321"}):
            with mock.patch.object(publish.requests, "get", return_value=response):
                with self.assertRaisesRegex(RuntimeError, "YANLIŞ INSTAGRAM HESABI"):
                    publish.instagram_check("golge.defter")

    def test_right_ig_account_passes(self):
        response = mock.Mock(ok=True)
        response.json.return_value = {"id": "321", "username": "golge.defter"}
        with mock.patch.dict(os.environ, {"IG_ACCESS_TOKEN": "IG_example", "IG_USER_ID": "321"}):
            with mock.patch.object(publish.requests, "get", return_value=response) as get:
                self.assertEqual("golge.defter", publish.instagram_check("@GOLGE.DEFTER"))
                self.assertIn("/me", get.call_args.args[0])

    def test_wrong_account_does_not_upload_any_video(self):
        with mock.patch.object(publish, "instagram_check", side_effect=RuntimeError("wrong")):
            with mock.patch.object(publish.requests, "post") as post:
                with self.assertRaises(RuntimeError):
                    publish.instagram_reel("anything.mp4", "caption", expected_username="beyondqaf")
                post.assert_not_called()

    def test_missing_release_asset_remains_in_queue(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            queue = root / "ig_queue.json"
            items = [{"asset": "missing.mp4", "title": "Keep me", "caption": "test"}]
            queue.write_text(json.dumps(items), encoding="utf-8")
            with mock.patch.object(ig_queue, "ROOT", root), \
                 mock.patch.object(ig_queue, "DATA", root), \
                 mock.patch.object(ig_queue, "QUEUE", queue), \
                 mock.patch.object(ig_queue, "CFG", {"instagram_enabled": True}), \
                 mock.patch.object(ig_queue, "gh", return_value=SimpleNamespace(returncode=1, stderr="404")), \
                 mock.patch.object(ig_queue, "notify"), \
                 mock.patch.dict(os.environ, {"IG_ACCESS_TOKEN": "fake", "IG_USER_ID": "123"}):
                with self.assertRaisesRegex(RuntimeError, "kuyruk KORUNDU"):
                    ig_queue.main()
            self.assertEqual(items, json.loads(queue.read_text(encoding="utf-8")))
            self.assertTrue((root / "ig_last_error.txt").exists())


class WatchdogGuards(unittest.TestCase):
    def _dispatch(self, status):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            hist = root / "history.json"
            with mock.patch.object(watchdog, "ROOT", root), \
                 mock.patch.object(watchdog, "CHANNELS", [("otoyt.yml", hist, {4}, 8, "Beyond Qaf")]), \
                 mock.patch.object(watchdog, "runs", return_value=[]), \
                 mock.patch.object(watchdog, "tg"), \
                 mock.patch.object(watchdog.requests, "post",
                                   return_value=SimpleNamespace(status_code=status, text="test")) as post, \
                 mock.patch.object(watchdog, "datetime") as dt:
                dt.now.return_value = datetime(2026, 11, 6, 12, 0,
                                               tzinfo=timezone(timedelta(hours=3)))
                watchdog.main()
                self.assertEqual(1, post.call_count)
                return json.loads((root / "watchdog_state.json").read_text(encoding="utf-8"))

    def test_refused_dispatch_does_not_consume_retry(self):
        state = self._dispatch(403)
        self.assertFalse(any(k.endswith(":tries") for k in state))

    def test_successful_dispatch_consumes_one_retry(self):
        state = self._dispatch(204)
        self.assertEqual(1, state.get("otoyt.yml:2026-11-06:tries"))


class ProductionGuards(unittest.TestCase):
    def test_manual_workflow_cannot_repeat_todays_video(self):
        with tempfile.TemporaryDirectory() as t:
            history = Path(t) / "history.json"
            today = datetime.now(timezone.utc).date().isoformat()
            history.write_text(json.dumps([{"date": today + "T10:00", "type": "long",
                                            "video_id": "already_done"}]), encoding="utf-8")
            with mock.patch.object(pipeline, "HISTORY", history), \
                 mock.patch.object(pipeline, "DRY_RUN", False), \
                 mock.patch.object(pipeline, "check_channel") as channel, \
                 mock.patch.dict(os.environ, {"GITHUB_EVENT_NAME": "workflow_dispatch",
                                              "FORCE_NEW_VIDEO": "", "CATCHUP": ""}):
                self.assertIs(True, pipeline.run_once())
                channel.assert_not_called()

    def test_vertex_image_pause_skips_quota_requests(self):
        with mock.patch.object(ai, "vertex_on", return_value=True):
            with mock.patch.object(ai, "_vx_image_pause_until",
                                   ai.time.monotonic() + 60):
                self.assertFalse(ai.vertex_image_on())
            with mock.patch.object(ai, "_vx_image_pause_until", 0):
                self.assertTrue(ai.vertex_image_on())


if __name__ == "__main__":
    unittest.main()
