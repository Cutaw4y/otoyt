"""No-network, no-publish regression tests for the production data auditor."""
import json
import tempfile
import unittest
from pathlib import Path

import audit_state


class AuditStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "data/golgedefter").mkdir(parents=True)

    def write(self, relative, value):
        dest = self.root / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(value), encoding="utf-8")
        return dest

    def prepare(self, history=None, queue=None, *, tr=False):
        base = "data/golgedefter/" if tr else ""
        self.write(base + "history.json", [] if history is None else history)
        self.write(base + "ig_queue.json", [] if queue is None else queue)

    def test_healthy_two_channel_history_does_not_report_errors(self):
        history = [{"type": "long", "date": "2026-11-05T12:00", "video_id": "abcdefghijk"}]
        history += [{"type": "short", "video_id": f"short{i:06d}", "parent": "abcdefghijk"}
                    for i in range(3)]
        self.prepare(history, [{"asset": "test.mp4", "title": "Reel"}])
        self.prepare(tr=True)
        report = audit_state.audit(self.root)
        self.assertEqual(0, report["errors"])
        self.assertEqual(3, report["channels"][0]["counts"]["short"])
        self.assertEqual(1, report["channels"][0]["counts"]["queued_reel"])
        self.assertEqual([], report["channels"][0]["missing_short_groups"])

    def test_missing_two_short_videos_produce_warning_not_error(self):
        self.prepare([
            {"type": "long", "video_id": "abcdefghijk"},
            {"type": "short", "video_id": "12345678901", "parent": "abcdefghijk"},
        ])
        report = audit_state.audit_channel(self.root, "EN", ".", "ig-queue", 3)
        self.assertEqual(1, len(report["missing_short_groups"]))
        self.assertEqual(0, len(report["errors"]))
        self.assertEqual(1, report["missing_short_groups"][0]["count"])

    def test_manual_unlinked_short_not_misreported_as_certain_failure(self):
        self.prepare([
            {"type": "long", "video_id": "abcdefghijk"},
            {"type": "short", "video_id": "12345678901", "manual": True},
        ])
        report = audit_state.audit_channel(self.root, "EN", ".", "ig-queue")
        self.assertEqual(1, report["counts"]["unlinked_short"])
        self.assertTrue(any("manuel" in w for w in report["warnings"]))

    def test_duplicate_video_id_is_error(self):
        self.prepare([
            {"type": "short", "video_id": "12345678901"},
            {"type": "short", "video_id": "12345678901"},
        ])
        report = audit_state.audit_channel(self.root, "EN", ".", "ig-queue")
        self.assertTrue(any("tekrar" in e for e in report["errors"]))

    def test_unsafe_file_path_is_rejected(self):
        self.prepare([], [{"path": "../../etc/passwd", "title": "escape"}])
        report = audit_state.audit_channel(self.root, "EN", ".", "ig-queue")
        self.assertTrue(any("repo dışına" in e for e in report["errors"]))

    def test_absent_local_reel_file_is_reported(self):
        self.prepare([], [{"path": "missing.mp4", "title": "Lost"}])
        report = audit_state.audit_channel(self.root, "EN", ".", "ig-queue")
        self.assertTrue(any("bulunamadı" in e for e in report["errors"]))

    def test_valid_local_file_is_accepted(self):
        (self.root / "local.mp4").write_bytes(b"video")
        self.prepare([], [{"path": "local.mp4", "title": "Present"}])
        report = audit_state.audit_channel(self.root, "EN", ".", "ig-queue")
        self.assertEqual([], report["errors"])

    def test_queue_item_requires_exactly_one_source(self):
        self.prepare([], [{"title": "wrong", "asset": "a.mp4", "path": "b.mp4"}])
        report = audit_state.audit_channel(self.root, "EN", ".", "ig-queue")
        self.assertTrue(any("tam biri" in e for e in report["errors"]))

    def test_broken_json_is_reported_without_exception(self):
        self.prepare()
        (self.root / "history.json").write_text("{oops", encoding="utf-8")
        report = audit_state.audit_channel(self.root, "EN", ".", "ig-queue")
        self.assertTrue(any("JSON okunamadı" in e for e in report["errors"]))

    def test_audit_does_not_mutate_files(self):
        p = self.write("history.json", [{"type": "long", "video_id": "abcdefghijk"}])
        q = self.write("ig_queue.json", [])
        self.prepare(tr=True)
        before = (p.read_bytes(), q.read_bytes())
        audit_state.audit(self.root)
        self.assertEqual(before, (p.read_bytes(), q.read_bytes()))


if __name__ == "__main__":
    unittest.main()
