"""Shorts recovery regression suite: mocked Drive/YouTube only, never publishes."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import short_recovery as sr


def record(marker="otoyt_rescue_abcdefghijk_1", state="pending", video_id=None):
    meta = {"title": "Test #Shorts", "description": "Full: https://youtu.be/abcdefghijk",
            "tags": [marker], "category_id": "22", "lang": "en",
            "privacy": "private", "made_for_kids": False, "synthetic": True,
            "publish_at": (datetime.now(timezone.utc) + timedelta(hours=5)
                           ).strftime("%Y-%m-%dT%H:%M:%SZ")}
    entry = {"version": 1, "marker": marker, "parent": "abcdefghijk", "title": "Test",
             "index": 1, "pillar": "test", "meta": meta}
    props = {"otoyt_marker": marker, "otoyt_state": state}
    if video_id:
        props["youtube_id"] = video_id
    return {"id": "privateDriveFile1", "name": "private.mp4",
            "description": json.dumps(entry), "appProperties": props}


class ShortMetadataTests(unittest.TestCase):
    def test_marker_is_stable_and_tags_stay_within_quota(self):
        marker = sr.marker_for("abcdefghijk", 1)
        self.assertEqual("otoyt_rescue_abcdefghijk_1", marker)
        tagged = sr.with_marker({"tags": ["a" * 110] * 5}, marker)
        self.assertLessEqual(len(",".join(tagged["tags"]).encode("utf-8")), 440)
        self.assertEqual(marker, tagged["tags"][-1])

    def test_id_rejects_invalid_parent(self):
        with self.assertRaises(ValueError):
            sr.marker_for("", 1)

    def test_upload_marks_drive_state_in_order(self):
        with mock.patch.object(sr.drive, "short_backup_status") as status, \
             mock.patch.object(sr.publish, "upload", return_value="12345678901") as upload:
            result = sr.upload("test.mp4", {"title": "x"}, "youtube", "privateDriveFile1")
        self.assertEqual("12345678901", result)
        self.assertEqual(2, status.call_count)
        status.assert_any_call("privateDriveFile1", "uploading")
        status.assert_any_call("privateDriveFile1", "uploaded", "12345678901")
        self.assertEqual(1, upload.call_count)

    def test_ambiguous_upload_is_not_retriable(self):
        with mock.patch.object(sr.drive, "short_backup_status") as status, \
             mock.patch.object(sr.publish, "upload", side_effect=TimeoutError("lost final reply")):
            with self.assertRaises(TimeoutError):
                sr.upload("test.mp4", {}, "youtube", "privateDriveFile1")
        status.assert_any_call("privateDriveFile1", "uncertain")

    def test_explicit_quota_failure_is_deferred(self):
        with mock.patch.object(sr.drive, "short_backup_status") as status, \
             mock.patch.object(sr.publish, "upload", side_effect=RuntimeError("quotaExceeded")):
            with self.assertRaises(RuntimeError):
                sr.upload("test.mp4", {}, "youtube", "privateDriveFile1")
        status.assert_any_call("privateDriveFile1", "deferred")

    def test_old_schedule_is_moved_future(self):
        d = sr._future_meta({"publish_at": "2024-01-01T00:00:00Z", "title": "x"})
        self.assertGreater(datetime.fromisoformat(d["publish_at"].replace("Z", "+00:00")),
                           datetime.now(timezone.utc) + timedelta(hours=2))

    def test_future_schedule_preserved(self):
        d = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual(d, sr._future_meta({"publish_at": d})["publish_at"])


class ShortRecoveryTests(unittest.TestCase):
    def run_recovery(self, backups, recent=None, upload=None, downloaded=False, history=None):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.json"
            if history is not None:
                path.write_text(json.dumps(history), encoding="utf-8")
            notes = []
            with mock.patch.object(sr.drive, "enabled", return_value=True), \
                 mock.patch.object(sr.drive, "list_short_backups", return_value=backups), \
                 mock.patch.object(sr, "_identity_check", return_value="channel-id"), \
                 mock.patch.object(sr.publish, "recent_uploads", return_value=recent or []), \
                 mock.patch.object(sr.drive, "short_backup_status") as status, \
                 mock.patch.object(sr.drive, "download_short_backup", side_effect=lambda fid, p: Path(p).write_bytes(b"video")) as download, \
                 mock.patch.object(sr, "upload", return_value=upload or "newshort123") as u:
                result = sr.recover({"channel_name": "Beyond Qaf"}, path, notify=notes.append)
                saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
            return result, saved, notes, u.call_count, status.call_args_list, download.call_count

    def test_already_published_is_reconciled_without_upload(self):
        b = record(state="uploading")
        marker = json.loads(b["description"])["marker"]
        recent = [{"id": "foundID1234", "snippet": {"tags": ["other", marker]}}]
        result, history, _, uploads, calls, downloads = self.run_recovery([b], recent=recent)
        self.assertEqual(0, uploads)
        self.assertEqual(0, downloads)
        self.assertEqual("foundID1234", history[0]["video_id"])
        self.assertEqual(1, result["reconciled"])
        self.assertTrue(any(x.args[1] == "uploaded" for x in calls))

    def test_uncertain_without_match_never_reuploads(self):
        result, history, notes, uploads, _, downloads = self.run_recovery([record(state="uncertain")])
        self.assertEqual(0, uploads)
        self.assertEqual(0, downloads)
        self.assertEqual(1, result["uncertain"])
        self.assertTrue(any("BELİRSİZ" in x for x in notes))

    def test_staged_pending_can_be_uploaded_once(self):
        result, history, _, uploads, _, downloads = self.run_recovery([record(state="pending")])
        self.assertEqual(1, uploads)
        self.assertEqual(1, downloads)
        self.assertEqual(1, len(history))
        self.assertEqual("newshort123", history[0]["video_id"])
        self.assertEqual(1, result["uploaded"])

    def test_previous_uploaded_metadata_repairs_history(self):
        result, history, _, uploads, _, _ = self.run_recovery(
            [record(state="uploaded", video_id="existing1234")])
        self.assertEqual(0, uploads)
        self.assertEqual(1, result["reconciled"])
        self.assertEqual("existing1234", history[0]["video_id"])

    def test_duplicate_history_is_not_appended_twice(self):
        b = record(state="uploaded", video_id="already12345")
        prior = [{"type": "short", "video_id": "already12345",
                  "parent": "abcdefghijk", "recovery_marker": json.loads(b["description"])["marker"]}]
        result, history, _, uploads, _, _ = self.run_recovery([b], history=prior)
        self.assertEqual(1, len(history))
        self.assertEqual(0, uploads)

    def test_invalid_drive_record_does_not_publish(self):
        b = record()
        b["appProperties"]["otoyt_marker"] = "tampered"
        result, _, notes, uploads, _, _ = self.run_recovery([b])
        self.assertEqual(1, result["errors"])
        self.assertEqual(0, uploads)
        self.assertTrue(any("uyuşmazlığı" in x for x in notes))

    def test_wrong_channel_rejects_before_storage_access(self):
        with mock.patch.object(sr.drive, "enabled", return_value=True), \
             mock.patch.object(sr, "_identity_check", side_effect=RuntimeError("wrong account")), \
             mock.patch.object(sr.drive, "list_short_backups") as backups:
            with self.assertRaisesRegex(RuntimeError, "wrong account"):
                sr.recover({"channel_name": "Beyond Qaf"}, "history.json")
            backups.assert_not_called()


if __name__ == "__main__":
    unittest.main()
