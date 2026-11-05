"""Gölgedefter owner approvals. No upload or production without a persisted decision.

All invocations share the production workflow's concurrency group. Telegram is
polled by Actions 24/7 every five minutes; GitHub schedule latency may vary.
"""
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yaml

ROOT = Path(__file__).parent
CONFIG = ROOT / "channels/golgedefter.yaml"
STATE = ROOT / "data/golgedefter/editorial.json"
CHANNEL_ID = "UC17T58dqlAqBoQZjactX5rg"
TR = ZoneInfo("Europe/Istanbul")
TERMINAL = {"done", "cancelled"}


def enabled(cfg):
    return bool(cfg.get("telegram_editorial", {}).get("enabled"))


def guard(cfg):
    if (cfg.get("data_dir") != "data/golgedefter" or
            cfg.get("channel_id") != CHANNEL_ID):
        raise RuntimeError("Telegram editor only supports Gölgedefter")


def github(method, path, **kwargs):
    try:
        token = os.environ["GITHUB_TOKEN"]
        r = requests.request(method, path, headers={"Authorization": f"Bearer {token}",
                             "Accept": "application/vnd.github+json", **kwargs.pop("headers", {})},
                             timeout=60, **kwargs)
        r.raise_for_status()
        return r
    except Exception:
        raise RuntimeError("Private editorial storage request failed; credentials omitted") from None


def state_release(create=False):
    repo = os.environ["GITHUB_REPOSITORY"]
    base = f"https://api.github.com/repos/{repo}"
    # Draft releases are visible only to repository collaborators, even in a public repo.
    for page in range(1, 21):
        rows = github("GET", f"{base}/releases", params={"per_page": 100, "page": page}).json()
        for row in rows:
            if row.get("tag_name") == "gd-editorial-state":
                if not row.get("draft"):
                    raise RuntimeError("Editorial state release must stay private/draft")
                return row
        if len(rows) < 100:
            break
    if not create:
        return None
    return github("POST", f"{base}/releases", json={"tag_name": "gd-editorial-state",
                  "name": "Gölgedefter private editorial state", "draft": True,
                  "body": "Private owner decisions; keep draft. Do not publish."}).json()


def load():
    if os.getenv("GITHUB_ACTIONS") == "true":
        rel = state_release()
        if not rel:
            return {}
        latest = None
        for page in range(1, 101):
            rows = github("GET", rel["assets_url"], params={"per_page": 100, "page": page}).json()
            candidates = [a for a in rows if a["name"].startswith("state-") and a.get("state") == "uploaded"]
            if candidates:
                candidate = max(candidates, key=lambda a: a["id"])
                if latest is None or candidate["id"] > latest["id"]:
                    latest = candidate
            if len(rows) < 100:
                break
        else:
            raise RuntimeError("Editorial state archive is full; maintenance required")
        if latest is None:
            return {}
        return github("GET", latest["url"], headers={"Accept": "application/octet-stream"}).json()
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}


def save(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE)


def persist(state):
    """Fail closed before side effects. Decisions/feedback never enter the public git tree."""
    if os.getenv("GITHUB_ACTIONS") != "true":
        save(state)
        return
    rel = state_release(create=True)
    url = rel["upload_url"].split("{")[0]
    payload = json.dumps(state, ensure_ascii=False).encode("utf-8")
    github("POST", url, params={"name": f"state-{uuid.uuid4().hex}.json"}, data=payload,
           headers={"Content-Type": "application/json"})
    # Immutable append: a failed upload leaves the previous state intact.


def tg(method, **body):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    # Do not propagate requests exceptions containing the credential-bearing URL.
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/{method}",
                          json=body, timeout=30)
        data = r.json()
        if not r.ok or not data.get("ok"):
            raise ValueError("Telegram rejected request")
        return data["result"]
    except Exception:
        raise RuntimeError(f"Telegram {method} failed; credentials omitted") from None


def owner_id():
    chat = int(os.environ["TELEGRAM_CHAT_ID"])
    if chat <= 0:
        raise RuntimeError("Owner approvals require a private Telegram chat")
    return chat


def send(text, buttons=()):
    return tg("sendMessage", chat_id=owner_id(), text=text[:4000],
              reply_markup={"inline_keyboard": [[{"text": label, "callback_data": data}]
                                                for label, data in buttons]})


def key(job, action):
    return f"gd:{job['id']}:{action}"


def review_message(job):
    return (f"📕 Gölgedefter — hazır video\n{job['title']}\n"
            f"Tam ön izleme ve dosyalar: {job['preview_url']}\n"
            "Henüz YouTube veya Instagram'a yüklenmedi. Yayınla seçeneği uzun videoyu, "
            "Shorts'u ve bağlı Reels paylaşımını onaylar. Geç kalan onay bir sonraki uygun yayın saatine alınır.\n"
            "Düzelt: Telegram'a istediğin değişikliği yaz, ardından Düzelt düğmesine dokun.\n"
            "Düğmeler 24 saat kontrol edilir; genelde birkaç dakika içinde işlenir (GitHub gecikebilir).")


def review_buttons(job):
    return [("✅ Yayınla", key(job, "publish")), ("✏️ Düzelt", key(job, "revise")),
            ("❌ İptal", key(job, "cancel"))]


def transition(state, update, owner):
    """Pure validation/state transition. No API, production or publishing here."""
    job = state.get("job")
    msg = update.get("message", {})
    if (job and msg.get("chat", {}).get("id") == owner and
            msg.get("chat", {}).get("type") == "private" and
            msg.get("from", {}).get("id") == owner and job["status"] == "review"):
        text = msg.get("text", "").strip()
        if text and not text.startswith("/"):
            job["revision_note"] = text[:2000]
        return None
    cb = update.get("callback_query", {})
    chat = cb.get("message", {}).get("chat", {})
    if (not job or cb.get("from", {}).get("id") != owner or
            chat.get("id") != owner or chat.get("type") != "private"):
        return None
    data = cb.get("data", "").split(":")
    if len(data) != 3 or data[:2] != ["gd", job["id"]]:
        return None
    action, status = data[2], job["status"]
    if action.startswith("choose-") and status in {"topic", "confirm"}:
        try:
            i = int(action[7:])
        except ValueError:
            return None
        if not 0 <= i < len(job["ideas"]):
            return None
        job.update(status="confirm", selected=i)
        return "confirm"
    if action.startswith("produce-") and status == "confirm":
        # Bind confirmation to its selected option, so an old confirm button is harmless.
        if action != f"produce-{job['selected']}":
            return None
        job.update(status="approved", idea=job["ideas"][job["selected"]])
        return "approved"
    if action == "publish" and status == "review":
        job["status"] = "publish_approved"
        return "publish_approved"
    if action == "revise" and status == "review":
        if not job.get("revision_note"):
            return "need_note"
        idea = dict(job["idea"])
        idea["angle"] += "\nOwner revision: " + job.pop("revision_note")
        job.update(id=uuid.uuid4().hex[:12], status="approved", idea=idea)
        job.pop("bundle", None)
        return "approved"
    if action == "cancel" and status in {"topic", "confirm", "review", "failed", "dispatch_uncertain"}:
        job["status"] = "cancelled"
        return "cancelled"
    return None


def generate_ideas(cfg, history):
    import ai
    for target, source in cfg.get("env_map", {}).items():
        if os.getenv(source):
            os.environ[target] = os.environ[source]
    past = [h.get("title", "") for h in history if h.get("type", "long") == "long"][-100:]
    result = ai.gemini_json(
        f"You plan Turkish documentaries for {cfg['channel_name']}. {cfg['channel_brief']}\n"
        f"Rules: {cfg.get('idea_rules', '')}",
        "Propose exactly 4 distinct topics. Do not repeat or closely paraphrase these published titles:\n"
        + json.dumps(past, ensure_ascii=False) + "\nChoose pillars only from: "
        + json.dumps(cfg["content_pillars"], ensure_ascii=False)
        + '\nJSON: {"ideas":[{"title":"Turkish title","angle":"Turkish short summary",'
          '"why":"Turkish reason to watch","pillar":"exact pillar","search_query":"query"}]}',
        models=cfg.get("paid_text_models") or cfg["text_models"], required=("ideas",))
    ideas = result["ideas"]
    if len(ideas) != 4:
        raise ValueError("Expected four topic ideas")
    normal = lambda t: re.sub(r"\W+", "", t.casefold())
    seen = {normal(t) for t in past}
    for idea in ideas:
        if not all(isinstance(idea.get(k), str) and idea[k].strip()
                   for k in ("title", "angle", "why", "pillar", "search_query")):
            raise ValueError("Invalid topic suggestion")
        if idea["pillar"] not in cfg["content_pillars"] or normal(idea["title"]) in seen:
            raise ValueError("Duplicate topic or unknown pillar")
        seen.add(normal(idea["title"]))
    editorial = cfg.get("telegram_editorial", {})
    excluded = editorial.get("excluded_topics", [])
    if any(normal(term) in normal(idea["title"] + " " + idea["angle"])
           for term in excluded for idea in ideas):
        raise ValueError("Topic excluded by channel owner's feedback")
    focus = {cfg["content_pillars"][i] for i in editorial.get("focus_pillars", [])}
    if sum(idea["pillar"] in focus for idea in ideas) < editorial.get("min_focus_topics", 0):
        raise ValueError("Topic suggestions do not match channel owner's focus")
    return ideas


def offer(state, cfg, now):
    job = state.get("job", {})
    target_day = now.astimezone(TR).date() + timedelta(days=1)
    target = target_day.isoformat()
    editorial = cfg["telegram_editorial"]
    if target_day.weekday() not in editorial.get("publish_days", range(7)):
        return
    if (not os.getenv("OFFER_NOW") == "1" and
            now.astimezone(TR).hour < cfg["telegram_editorial"].get("offer_hour", 20)):
        return
    if job and (job["status"] not in TERMINAL or job["target_date"] >= target):
        return
    start = state.get("trial_start_date", target)
    trial_days = editorial.get("trial_days", 0)
    if trial_days and datetime.fromisoformat(target) >= datetime.fromisoformat(start) + timedelta(days=trial_days):
        if not state.get("trial_complete_notified"):
            send("📊 Gölgedefter'in bir haftalık günlük video denemesi tamamlandı. "
                 "Yeni üretim durdu; maliyet ve video performansını değerlendirip devam edebiliriz.")
            state["trial_complete_notified"] = True
            persist(state)
        return
    hist = ROOT / "data/golgedefter/history.json"
    ideas = generate_ideas(cfg, json.loads(hist.read_text()) if hist.exists() else [])
    state["job"] = {"id": uuid.uuid4().hex[:12], "target_date": target,
                    "status": "topic", "ideas": ideas}
    state.setdefault("trial_start_date", target)
    persist(state)
    job = state["job"]


def deliver(state):
    job = state.get("job", {})
    if not job:
        return
    status = job["status"]
    marker = f"{job['id']}:{status}:{job.get('selected', '')}"
    if job.get("delivered") == marker:
        return
    if status == "topic":
        text = f"📕 Gölgedefter — {job['target_date']} için konu seç\n\n"
        text += "\n\n".join(f"{i+1}. {v['title']}\n{v['angle'][:350]}\nNeden: {v['why'][:180]}"
                             for i, v in enumerate(job["ideas"]))
        send(text + "\n\nSeçmezsen üretim bekler. Düğmeler 24 saat, yaklaşık 5 dakikada bir kontrol edilir (GitHub gecikebilir).",
             [(f"{i+1}. {v['title']}"[:60], key(job, f"choose-{i}"))
              for i, v in enumerate(job["ideas"])] + [("Bugün pas geç", key(job, "cancel"))])
    elif status == "confirm":
        # Confirm message delivery is durable: if Telegram fails after we persist
        # the click/offset, the next scheduled poll retries this same message.
        idea = job["ideas"][job["selected"]]
        send(f"Seçtin: {idea['title']}\nÜretime başlansın mı?",
             [("🎬 Üretime başla", key(job, f"produce-{job['selected']}")),
              ("İptal", key(job, "cancel"))])
    elif status == "review":
        send(review_message(job), review_buttons(job))
    elif status in {"failed", "dispatch_uncertain"}:
        send("⚠️ Gölgedefter işi durdu. Çift yüklemeyi önlemek için otomatik tekrar yapılmıyor. "
             "Kayıt incelenebilir veya bu işi kapatıp yeni konu seçebilirsin.",
             [("Bu işi kapat", key(job, "cancel"))])
    else:
        return
    job["delivered"] = marker
    persist(state)


def dispatch(state):
    job = state.get("job", {})
    if job.get("status") not in {"approved", "publish_approved"}:
        return
    mode = "produce" if job["status"] == "approved" else "publish"
    job["status"] = mode + "_queued"
    job["queued_at"] = datetime.now(timezone.utc).isoformat()
    persist(state)
    # Persist first. An uncertain dispatch is never automatically repeated.
    token = os.environ["GITHUB_TOKEN"]
    repo = os.environ["GITHUB_REPOSITORY"]
    try:
        r = requests.post(f"https://api.github.com/repos/{repo}/actions/workflows/otoyt_tr.yml/dispatches",
                          headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                          json={"ref": "main", "inputs": {"editorial_job": job["id"],
                                                             "editorial_action": mode}}, timeout=30)
        if r.status_code != 204:
            raise ValueError("Dispatch rejected")
    except Exception:
        job["status"] = "dispatch_uncertain"
        persist(state)
        send("⚠️ Gölgedefter: başlatma sonucu doğrulanamadı. Çift üretimi önlemek için otomatik tekrar durdu.")
        raise RuntimeError("GitHub dispatch outcome uncertain") from None


def reconcile(state):
    """Recover only work that never claimed its job. Interrupted uploads need review."""
    job = state.get("job", {})
    status = job.get("status")
    if status not in {"produce_queued", "publish_queued", "producing", "publishing"}:
        return
    mode = "produce" if status in {"produce_queued", "producing"} else "publish"
    repo = os.environ["GITHUB_REPOSITORY"]
    rows = github("GET", f"https://api.github.com/repos/{repo}/actions/workflows/otoyt_tr.yml/runs",
                  params={"per_page": 100}).json()["workflow_runs"]
    rows = [r for r in rows if r.get("display_title") == f"gd-{job['id']}-{mode}"]
    if any(r["status"] != "completed" for r in rows):
        return
    if status.endswith("_queued"):
        age = datetime.now(timezone.utc) - datetime.fromisoformat(job["queued_at"])
        if not rows and age < timedelta(hours=1):
            return
        # No worker claimed the persisted queued job: dispatch can safely be repeated.
        job["status"] = "approved" if mode == "produce" else "publish_approved"
    else:
        job["status"] = "failed"
    persist(state)


def claim(cfg, job_id, mode):
    guard(cfg)
    state = load()
    job = state.get("job", {})
    required = {"produce": "produce_queued", "publish": "publish_queued"}
    if (not enabled(cfg) or mode not in required or not job_id or
            job.get("id") != job_id or job.get("status") != required[mode]):
        return None
    job["status"] = "producing" if mode == "produce" else "publishing"
    persist(state)
    return job


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def stage(job, script, idea, shorts, thumb, thumb_alt, main_srt, sub_srt,
          tr_meta, tr_blocks, final, credits):
    import release
    import tempfile
    import shutil
    if not release.enabled():
        raise RuntimeError("Persistent preview storage unavailable")
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        files = {}
        def copy(name, src):
            if not src:
                return None
            dst = folder / name
            shutil.copy(src, dst)
            files[name] = digest(dst)
            return name
        bundle = {"job_id": job["id"], "channel_id": CHANNEL_ID, "script": script,
                  "idea": idea, "tr_meta": tr_meta, "tr_blocks": tr_blocks, "credits": credits,
                  "final": copy("long.mp4", final), "thumb": copy("thumb.jpg", thumb),
                  "thumb_alt": copy("thumb-alt.jpg", thumb_alt),
                  "main_srt": copy("main.srt", main_srt), "sub_srt": copy("sub.srt", sub_srt),
                  "shorts": [{**sh, "file": copy(f"short-{i}.mp4", sh["file"])}
                             for i, sh in enumerate(shorts)], "files": files}
        manifest = folder / "bundle.json"
        manifest.write_text(json.dumps(bundle, ensure_ascii=False, default=str), encoding="utf-8")
        tag = f"review-gd-{job['id']}"
        rid, url = release.create(tag, f"Gölgedefter ön izleme: {script['title']}",
                                  "Yayın onayı bekleniyor. Otomatik silmeyin.", list(folder.iterdir()), draft=True)
        state = load()
        current = state["job"]
        if current["id"] != job["id"] or current["status"] != "producing":
            raise RuntimeError("Editorial state changed during production")
        current.update(status="review", title=script["title"], preview_url=url,
                       bundle={"release_id": rid, "tag": tag, "manifest_sha256": digest(manifest)})
        persist(state)
        # The lightweight control job delivers the persisted review on its next tick.
        # A Telegram outage cannot discard an already rendered video.


def read_bundle(folder, job):
    folder = Path(folder)
    manifest = folder / "bundle.json"
    if digest(manifest) != job["bundle"]["manifest_sha256"]:
        raise RuntimeError("Preview manifest changed after review")
    bundle = json.loads(manifest.read_text(encoding="utf-8"))
    if bundle["job_id"] != job["id"] or bundle["channel_id"] != CHANNEL_ID:
        raise RuntimeError("Wrong preview/channel")
    for name, sha in bundle["files"].items():
        if Path(name).name != name or digest(folder / name) != sha:
            raise RuntimeError("Preview file integrity check failed")
    return bundle


def main():
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    guard(cfg)
    if not enabled(cfg):
        print("Telegram editorial disabled")
        return
    owner = owner_id()
    if tg("getWebhookInfo").get("url"):
        raise RuntimeError("Existing Telegram webhook; not replacing it")
    state = load()
    updates = tg("getUpdates", offset=state.get("offset", 0), timeout=0,
                 allowed_updates=["message", "callback_query"])
    # Public GitHub Actions logs: only totals and state names, NEVER IDs, messages or keys.
    print(f"Telegram poll: updates={len(updates)} "
          f"callback_count={sum(bool(u.get('callback_query')) for u in updates)} "
          f"job_status={state.get('job', {}).get('status', 'none')}", flush=True)
    for update in updates:
        event = transition(state, update, owner)
        print(f"Telegram event: kind={'callback' if update.get('callback_query') else 'message'} "
              f"processed={bool(event)} result={event or 'none'}", flush=True)
        state["offset"] = update["update_id"] + 1
        persist(state)
        job = state.get("job", {})
        if event == "confirm":
            deliver(state)  # Durable message; retries if Telegram is unavailable.
        elif event == "need_note":
            send("Önce neyi değiştireceğimizi mesaj olarak yaz, sonra Düzelt'e dokun.", review_buttons(job))
        elif event in {"approved", "publish_approved", "cancelled"}:
            send({"approved": "🎬 Üretim onayın kaydedildi.",
                  "publish_approved": "✅ Yayın onayın kaydedildi.",
                  "cancelled": "⏸ İptal edildi; video silinmedi."}[event])
        cb = update.get("callback_query", {})
        if cb.get("id") and cb.get("from", {}).get("id") == owner:
            try:
                tg("answerCallbackQuery", callback_query_id=cb["id"], text="İşlendi" if event else "Eski/geçersiz seçim")
            except RuntimeError:
                pass  # Telegram expires callback-query popups before an Actions poll may run.
    reconcile(state)
    offer(state, cfg, datetime.now(timezone.utc))
    deliver(state)
    dispatch(state)


if __name__ == "__main__":
    import sys
    if "--check-job" in sys.argv:
        job = load().get("job", {})
        expected = {"produce": "produce_queued", "publish": "publish_queued"}
        if not (os.getenv("DRY_RUN") == "1" or (os.getenv("EDITORIAL_JOB")
                and job.get("id") == os.getenv("EDITORIAL_JOB")
                and job.get("status") == expected.get(os.getenv("EDITORIAL_ACTION")))):
            with open(os.environ["GITHUB_ENV"], "a") as out:
                out.write("SKIP=1\n")
            print("Telegram onayı yok; üretim/yayın bekliyor.")
    else:
        main()
