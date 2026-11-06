"""06.11.2026'da yaşanan hataların bir daha olmaması için kalıcı testler (ağ/yayın yok)."""
import os
import re
import unittest
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("DRY_RUN", "1")
os.environ.setdefault("GITHUB_TOKEN", "test")  # watchdog içe aktarımı için (ağ kullanılmaz)
os.environ.setdefault("OTOYT_CONFIG", "channels/golgedefter.yaml")
import pipeline  # noqa: E402
import watchdog  # noqa: E402


def wf(name):
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))


class DuplicateVideo(unittest.TestCase):
    """Hata 1: yüklenmiş videonun senaryosu ertesi gün tekrar üretildi (kopya video)."""

    def test_uploaded_pending_is_detected_even_after_title_change(self):
        pend = {"idea": {"title": "Mount Qaf"}, "script": {"title": "Mount Qaf: The Emerald Wall"}}
        hist = [{"type": "long", "title": "Yeni Başlık", "orig_title": "Mount Qaf: The Emerald Wall"}]
        self.assertTrue(pipeline.pending_already_uploaded(pend, hist))
        hist = [{"type": "long", "title": "Başka", "idea_title": "Mount Qaf"}]
        self.assertTrue(pipeline.pending_already_uploaded(pend, hist))
        self.assertFalse(pipeline.pending_already_uploaded(pend, [{"type": "long", "title": "Alakasız"}]))
        self.assertFalse(pipeline.pending_already_uploaded(None, hist))

    def test_deleted_pending_script_is_committed(self):
        """Yüklemeden sonra silinen senaryo kaydının silinmesi git'e işlenmeli."""
        bq = (ROOT / ".github/workflows/otoyt.yml").read_text(encoding="utf-8")
        self.assertIn("git add -A pending_script.json", bq)
        gd = (ROOT / ".github/workflows/otoyt_tr.yml").read_text(encoding="utf-8")
        self.assertIn("git add -A data/golgedefter", gd)


class NoVideoBecauseOfStrictFloor(unittest.TestCase):
    """Hata 2: iyi puanlı (ort 7.7) senaryo, onaylı-konu kuralına takılıp video hiç üretilmedi."""

    def run_produce(self, strict):
        script = {"title": "T", "scenes": [{"narration": "a b c"}]}
        review = {"hook": 6, "flow": 8, "simplicity": 8, "problems": []}
        with mock.patch.multiple(pipeline, write_script=lambda i: dict(script), ensure_length=lambda s: s,
                                 plain_pass=lambda s, *a: s, verify_loop=lambda s: (s, True),
                                 title_pass=lambda s: s, seo_fix=lambda s: s, revise_script=lambda s, n: s,
                                 quality_gate=lambda s: (False, review), _score=lambda r: (6.0, 7.7),
                                 notify=lambda *a, **k: None, log=lambda *a, **k: None):
            return pipeline.produce_script([], {}, {"title": "Konu", "pillar": "p"}, strict=strict)

    def test_unattended_same_topic_rewrite_still_makes_a_video(self):
        idea, script = self.run_produce(strict=False)
        self.assertIsNotNone(script)

    def test_owner_approved_topic_keeps_high_floor(self):
        idea, script = self.run_produce(strict=True)
        self.assertIsNone(script)


class Schedule(unittest.TestCase):
    """Hata 3: günler/kanallar birbirinden habersiz değişti; aynı gün iki üretim ve yanlış bekçi günleri."""

    @staticmethod
    def cron_days(name):
        on = wf(name).get(True) or wf(name).get("on")
        days = set()
        for c in (on or {}).get("schedule", []):
            field = c["cron"].split()[4]
            days |= {(int(d) - 1) % 7 for d in field.split(",")}  # cron 0=Pazar → python 6
        return days

    def test_channels_never_share_a_day(self):
        self.assertFalse(self.cron_days("otoyt.yml") & self.cron_days("otoyt_tr.yml"))

    def test_watchdog_days_match_crons(self):
        days = {wf_name: d for wf_name, _, d, _, _ in watchdog.CHANNELS}
        self.assertEqual(days["otoyt.yml"], self.cron_days("otoyt.yml"))
        cfg = yaml.safe_load((ROOT / "channels/golgedefter.yaml").read_text(encoding="utf-8"))
        if not cfg.get("telegram_editorial", {}).get("enabled"):
            self.assertEqual(days["otoyt_tr.yml"], self.cron_days("otoyt_tr.yml"))

    def test_production_workflows_share_one_queue(self):
        for name in ("otoyt.yml", "otoyt_tr.yml"):
            c = wf(name)["concurrency"]
            self.assertEqual("otoyt-uretim", c["group"] if isinstance(c, dict) else c)
            if isinstance(c, dict):
                self.assertFalse(c.get("cancel-in-progress", False))

    def test_automatic_mode_has_schedule(self):
        cfg = yaml.safe_load((ROOT / "channels/golgedefter.yaml").read_text(encoding="utf-8"))
        if not cfg.get("telegram_editorial", {}).get("enabled"):
            self.assertTrue(self.cron_days("otoyt_tr.yml"), "Gölgedefter otomatik ama zamanlayıcısı yok")

    def test_queued_runs_check_out_latest_main(self):
        for name in ("otoyt.yml", "otoyt_tr.yml"):
            text = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
            self.assertRegex(text, r"actions/checkout@v4\s*\n\s*with: \{ref: main\}")


class MixedDateFormats(unittest.TestCase):
    """Hata 5: '+00:00' ile yazılmış kayıt tarihi, dilimsiz tarihlerle karşılaştırılınca üretim çöküyordu."""

    def test_history_dates_are_comparable(self):
        import publish
        from datetime import datetime
        for s in ("2026-11-06T10:58", "2026-11-06T10:58+00:00", "2026-11-06T13:58+03:00"):
            self.assertEqual(publish.hist_dt(s), datetime(2026, 11, 6, 10, 58))
        hist = [{"type": "long", "video_id": "a", "date": "2026-11-01T10:00+00:00"},
                {"type": "long", "video_id": "b", "date": "2026-11-01T10:00"}]
        with mock.patch.object(publish, "video_stats", return_value={}):
            publish.performance_report(hist)  # hata vermemeli


class LongDocumentaryFitsTimeLimit(unittest.TestCase):
    """Hata 4: uzun belgesel 5,5 saatlik iş sınırını aşmasın (kaba bütçe kontrolü)."""

    def test_friday_budget(self):
        cfg = yaml.safe_load((ROOT / "channels/golgedefter.yaml").read_text(encoding="utf-8"))
        fri = (cfg.get("weekday_overrides") or {}).get(4, {})
        if fri:
            self.assertLessEqual(fri.get("visual_time_budget_min", 45), 75)
            self.assertLessEqual(fri.get("target_words", 0), 5500)
        tr = (ROOT / ".github/workflows/otoyt_tr.yml").read_text(encoding="utf-8")
        self.assertTrue(re.search(r"timeout-minutes: 3[0-5]\d", tr))


if __name__ == "__main__":
    unittest.main()
