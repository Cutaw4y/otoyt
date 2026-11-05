"""Offline owner-approval regression tests; no real generation, messages or uploads."""
import copy
import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import telegram_control as tc

OWNER = 123


def state(status="topic"):
    return {"job": {"id": "abc", "status": status, "target_date": "2026-11-07",
                    "ideas": [{"title": "A", "angle": "One", "pillar": "History"},
                              {"title": "B", "angle": "Two", "pillar": "History"}]}}


def click(action, owner=OWNER, chat=OWNER, job="abc"):
    return {"update_id": 7, "callback_query": {"id": "cb", "data": f"gd:{job}:{action}",
            "from": {"id": owner}, "message": {"chat": {"id": chat, "type": "private"}}}}


class OwnerDecisions(unittest.TestCase):
    def test_two_distinct_approvals_required(self):
        s = state()
        self.assertIsNone(tc.transition(s, click("produce-0"), OWNER))
        self.assertEqual("confirm", tc.transition(s, click("choose-0"), OWNER))
        self.assertEqual("confirm", s["job"]["status"])
        self.assertEqual("approved", tc.transition(s, click("produce-0"), OWNER))
        self.assertIsNone(tc.transition(s, click("publish"), OWNER))
        s["job"]["status"] = "review"
        self.assertEqual("publish_approved", tc.transition(s, click("publish"), OWNER))

    def test_wrong_user_chat_and_old_job_do_nothing(self):
        for update in [click("choose-0", owner=456), click("choose-0", chat=456),
                       click("choose-0", job="old"), click("choose-99"), click("choose-bad")]:
            s = state()
            before = copy.deepcopy(s)
            self.assertIsNone(tc.transition(s, update, OWNER))
            self.assertEqual(before, s)

    def test_confirmation_bound_to_current_choice(self):
        s = state()
        tc.transition(s, click("choose-0"), OWNER)
        tc.transition(s, click("choose-1"), OWNER)
        self.assertIsNone(tc.transition(s, click("produce-0"), OWNER))
        self.assertEqual("approved", tc.transition(s, click("produce-1"), OWNER))
        self.assertEqual("B", s["job"]["idea"]["title"])

    def test_double_click_is_idempotent(self):
        s = state("review")
        self.assertEqual("publish_approved", tc.transition(s, click("publish"), OWNER))
        self.assertIsNone(tc.transition(s, click("publish"), OWNER))

    def test_revision_requires_owner_note_and_invalidates_old_buttons(self):
        s = state("review")
        s["job"]["idea"] = dict(s["job"]["ideas"][0])
        self.assertEqual("need_note", tc.transition(s, click("revise"), OWNER))
        update = {"message": {"chat": {"id": OWNER, "type": "private"},
                              "from": {"id": OWNER}, "text": "Girişi kısalt"}}
        tc.transition(s, update, OWNER)
        self.assertEqual("approved", tc.transition(s, click("revise"), OWNER))
        self.assertIn("Girişi kısalt", s["job"]["idea"]["angle"])
        self.assertIsNone(tc.transition(s, click("publish"), OWNER))

    def test_cancel_is_not_a_delete_operation(self):
        s = state("review")
        self.assertEqual("cancelled", tc.transition(s, click("cancel"), OWNER))
        self.assertEqual("cancelled", s["job"]["status"])


class DurableGuards(unittest.TestCase):
    def test_only_offers_on_eve_of_four_publish_days(self):
        cfg = {"telegram_editorial": {"publish_days": [1, 3, 5, 6]}}
        # 2 November 2026 is Monday: offers Tue/Thu/Sat/Sun on Mon/Wed/Fri/Sat.
        with mock.patch.object(tc, "generate_ideas", return_value=[]), \
             mock.patch.object(tc, "persist"), mock.patch.dict(os.environ, {"OFFER_NOW": "1"}):
            offered = []
            for day in range(7):
                s = {}
                tc.offer(s, cfg, datetime(2026, 11, 2 + day, 17, tzinfo=timezone.utc))
                if s.get("job"):
                    offered.append(day)
            self.assertEqual([0, 2, 4, 5], offered)

    def test_owner_topic_feedback_excludes_kaf_and_enforces_focus(self):
        import ai
        cfg = {"channel_name": "Gölgedefter", "channel_brief": "Mystery", "text_models": ["fake"],
               "content_pillars": ["History", "Supernatural"],
               "telegram_editorial": {"focus_pillars": [1], "min_focus_topics": 3,
                                      "excluded_topics": ["Kaf Dağı"]}}
        ideas = [{"title": f"Topic {i}", "angle": "Mystery encounter", "why": "Unknown",
                  "search_query": "mystery", "pillar": "Supernatural"} for i in range(4)]
        with mock.patch.object(ai, "gemini_json", return_value={"ideas": ideas}):
            self.assertEqual(4, len(tc.generate_ideas(cfg, [])))
            ideas[0]["title"] = "Kaf Dağı"
            with self.assertRaisesRegex(ValueError, "excluded"):
                tc.generate_ideas(cfg, [])
            ideas[0]["title"] = "Other topic"
            ideas[0]["pillar"] = ideas[1]["pillar"] = "History"
            with self.assertRaisesRegex(ValueError, "focus"):
                tc.generate_ideas(cfg, [])

    def test_telegram_expired_popup_does_not_leak_token(self):
        with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "secret-token"}), \
             mock.patch.object(tc.requests, "post", side_effect=RuntimeError("secret-token")):
            with self.assertRaises(RuntimeError) as error:
                tc.tg("answerCallbackQuery")
            self.assertNotIn("secret-token", str(error.exception))

    def test_reconcile_never_retries_claimed_upload(self):
        s = state("publishing")
        response = mock.Mock()
        response.json.return_value = {"workflow_runs": [{"display_title": "gd-abc-publish",
                                                        "status": "completed", "conclusion": "failure"}]}
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "Cutaw4y/otoyt"}), \
             mock.patch.object(tc, "github", return_value=response), mock.patch.object(tc, "persist"):
            tc.reconcile(s)
            self.assertEqual("failed", s["job"]["status"])

    def test_canceled_unclaimed_job_can_be_requeued(self):
        s = state("produce_queued")
        s["job"]["queued_at"] = datetime.now(timezone.utc).isoformat()
        response = mock.Mock()
        response.json.return_value = {"workflow_runs": [{"display_title": "gd-abc-produce",
                                                        "status": "completed", "conclusion": "cancelled"}]}
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "Cutaw4y/otoyt"}), \
             mock.patch.object(tc, "github", return_value=response), mock.patch.object(tc, "persist"):
            tc.reconcile(s)
            self.assertEqual("approved", s["job"]["status"])

    def test_unapproved_dispatch_never_calls_network(self):
        for status in ["topic", "confirm", "review", "producing", "publishing", "failed"]:
            with mock.patch.object(tc.requests, "post") as post:
                tc.dispatch(state(status))
                post.assert_not_called()

    def test_cannot_dispatch_until_state_is_durable(self):
        with mock.patch.object(tc, "persist", side_effect=RuntimeError("push failed")), \
             mock.patch.object(tc.requests, "post") as post:
            with self.assertRaises(RuntimeError):
                tc.dispatch(state("approved"))
            post.assert_not_called()

    def test_claim_requires_exact_channel_job_and_phase(self):
        cfg = {"data_dir": "data/golgedefter", "channel_id": tc.CHANNEL_ID,
               "telegram_editorial": {"enabled": True}}
        s = state("produce_queued")
        with mock.patch.object(tc, "load", return_value=s), mock.patch.object(tc, "persist"):
            self.assertIsNone(tc.claim(cfg, "old", "produce"))
            self.assertIsNone(tc.claim(cfg, "abc", "publish"))
            self.assertEqual("producing", tc.claim(cfg, "abc", "produce")["status"])
            self.assertIsNone(tc.claim(cfg, "abc", "produce"))
        with self.assertRaises(RuntimeError):
            tc.claim({**cfg, "channel_id": "other"}, "abc", "produce")

    def test_no_replacement_topics_while_review_is_pending(self):
        with mock.patch.object(tc, "generate_ideas") as generate:
            tc.offer(state("review"), {"telegram_editorial": {}}, datetime(2026, 11, 8, 20, tzinfo=timezone.utc))
            generate.assert_not_called()

    def test_manifest_and_media_must_match_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            media = folder / "long.mp4"
            media.write_bytes(b"approved video")
            manifest = folder / "bundle.json"
            manifest.write_text(json.dumps({"job_id": "abc", "channel_id": tc.CHANNEL_ID,
                                            "files": {"long.mp4": tc.digest(media)}}))
            job = {"id": "abc", "bundle": {"manifest_sha256": tc.digest(manifest)}}
            self.assertEqual("abc", tc.read_bundle(folder, job)["job_id"])
            media.write_bytes(b"different video")
            with self.assertRaisesRegex(RuntimeError, "integrity"):
                tc.read_bundle(folder, job)

    def test_preview_is_private_draft_and_never_uploaded_to_youtube(self):
        import release
        import publish
        s = state("producing")
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "video.mp4"
            video.write_bytes(b"rendered preview")
            with mock.patch.object(release, "enabled", return_value=True), \
                 mock.patch.object(release, "create", return_value=(1, "https://github.com/Cutaw4y/otoyt/releases")) as create, \
                 mock.patch.object(tc, "load", return_value=s), mock.patch.object(tc, "persist"), \
                 mock.patch.object(tc, "send"), mock.patch.object(publish, "upload") as upload:
                tc.stage(s["job"], {"title": "Test"}, {"title": "Test"}, [], None,
                         None, None, None, None, None, video, [])
                self.assertTrue(create.call_args.kwargs["draft"])
                self.assertEqual("review", s["job"]["status"])
                upload.assert_not_called()


class PipelineIntegration(unittest.TestCase):
    def test_approved_bundle_uses_existing_long_short_and_reel_path(self):
        import pipeline
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            w = Path(tmp)
            final, short = w / "long.mp4", w / "clip.mp4"
            final.write_bytes(b"reviewed long")
            short.write_bytes(b"reviewed short")
            cfg = {**pipeline.CFG, "publish_mode": "scheduled", "localize_metadata": False,
                   "subtitle_translation": {}, "short_captions": False, "telegram_editorial": {}}
            for name, value in {"CFG": cfg, "DATA": w, "HISTORY": w / "history.json",
                                "OUT": w / "out", "DRY_RUN": False, "UPLOAD_STARTED": False}.items():
                stack.enter_context(mock.patch.object(pipeline, name, value))
            for name in ["queue_comment", "record_vote", "mark_poll_used", "push_now",
                         "add_to_pillar_playlist", "notify", "log"]:
                stack.enter_context(mock.patch.object(pipeline, name))
            stack.enter_context(mock.patch.object(pipeline, "watch_next_line", return_value=""))
            stack.enter_context(mock.patch.object(pipeline, "_footer", return_value=""))
            stack.enter_context(mock.patch.object(pipeline, "short_hashtags", return_value="#test"))
            stack.enter_context(mock.patch.object(pipeline.related_video, "register"))
            upload = stack.enter_context(mock.patch.object(pipeline.publish, "upload",
                                                           side_effect=["abcdefghijk", "lmnopqrstuv"]))
            reels = stack.enter_context(mock.patch.object(pipeline, "post_reels"))
            script = {"title": "Reviewed title", "description": "Reviewed description", "tags": ["test"]}
            history = []
            pipeline.publish_rendered(script, {"pillar": "History"},
                                      [{"file": short, "title": "Reviewed clip"}], None, None,
                                      None, None, None, None, final, w, history, mock.Mock(),
                                      {"id": "abc", "target_date": "2027-01-01"})
            self.assertEqual(2, upload.call_count)
            self.assertEqual("Reviewed title", upload.call_args_list[0].args[1]["title"])
            self.assertIn("2027-01-01", upload.call_args_list[0].args[1]["publish_at"])
            self.assertEqual(b"reviewed long", upload.call_args_list[0].args[0].read_bytes())
            self.assertEqual(["long", "short"], [v["type"] for v in history])
            reels.assert_called_once()

    def test_no_approval_stops_before_preflight_or_network(self):
        import pipeline
        cfg = {**pipeline.CFG, "telegram_editorial": {"enabled": True}}
        with mock.patch.object(pipeline, "CFG", cfg), mock.patch.object(pipeline, "DRY_RUN", False), \
             mock.patch.object(tc, "claim", return_value=None), \
             mock.patch.object(pipeline, "check_channel") as check, \
             mock.patch.object(pipeline, "preflight") as preflight, \
             mock.patch.object(pipeline.publish, "upload") as upload:
            self.assertTrue(pipeline.run_once())
            check.assert_not_called()
            preflight.assert_not_called()
            upload.assert_not_called()

    def test_failed_selected_topic_never_switches_to_other_idea(self):
        import pipeline
        idea = {"title": "Selected", "angle": "Angle", "pillar": "History"}
        cfg = {**pipeline.CFG, "max_topic_attempts": 3}
        with mock.patch.object(pipeline, "CFG", cfg), \
             mock.patch.object(pipeline, "pick_topic") as pick, \
             mock.patch.object(pipeline, "write_script", return_value={"scenes": []}) as write, \
             mock.patch.object(pipeline, "ensure_length", side_effect=lambda x: x), \
             mock.patch.object(pipeline, "plain_pass", side_effect=lambda x: x), \
             mock.patch.object(pipeline, "verify_loop", return_value=({}, False)), \
             mock.patch.object(pipeline, "notify"), mock.patch.object(pipeline, "log"):
            self.assertEqual((None, None), pipeline.produce_script([], {}, idea))
            pick.assert_not_called()
            write.assert_called_once_with(idea)


if __name__ == "__main__":
    unittest.main()
