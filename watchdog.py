"""Bekçi: her saat çalışır. Bugün video günü olan kanalın videosu çıkmadıysa ve çalışan üretim yoksa
üretimi kendisi başlatır (zamanlayıcı gecikmesi/atlaması, hata, zaman aşımı → otomatik yeniden deneme).
Günde en fazla MAX_TRIES deneme; hepsi başarısızsa Telegram'a bir kez haber verir ve durur.
Claude olmasa da iş akışı kendi kendine toparlanır."""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).parent
REPO = os.environ.get("GITHUB_REPOSITORY", "Cutaw4y/otoyt")
API = f"https://api.github.com/repos/{REPO}"
H = {"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}", "Accept": "application/vnd.github+json"}
MAX_TRIES = 2   # bekçinin günlük otomatik yeniden deneme sayısı
TR = timezone(timedelta(hours=3))

# kanal → (iş akışı, geçmiş dosyası, video günleri (TR, 0=Pzt), en erken müdahale saati (TR), ad)
CHANNELS = [
    ("otoyt.yml", ROOT / "history.json", {0, 2, 4}, 9, "Beyond Qaf"),
    ("otoyt_tr.yml", ROOT / "data/golgedefter/history.json", {1, 3, 5}, 13, "Gölgedefter"),
]


def tg(text):
    tok, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if tok and chat:
        try:
            requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                          json={"chat_id": chat, "text": text}, timeout=20)
        except Exception:
            pass


def runs(wf, since):
    r = requests.get(f"{API}/actions/workflows/{wf}/runs", headers=H,
                     params={"per_page": 30, "created": f">={since}"}, timeout=30)
    r.raise_for_status()
    return r.json().get("workflow_runs", [])


def main():
    now = datetime.now(TR)
    today_utc = datetime.now(timezone.utc).date().isoformat()
    state_f = ROOT / "watchdog_state.json"
    state = json.loads(state_f.read_text()) if state_f.exists() else {}
    for wf, hist_f, days, start_h, name in CHANNELS:
        if now.weekday() not in days or now.hour < start_h or now.hour >= 23:
            continue
        hist = json.loads(hist_f.read_text(encoding="utf-8")) if hist_f.exists() else []
        if any(h.get("type", "long") == "long" and str(h.get("date", "")).startswith(today_utc) for h in hist):
            continue  # bugünün videosu çıktı
        rs = runs(wf, today_utc)
        busy = ("queued", "in_progress", "waiting", "pending", "requested")
        if any(r["status"] in busy for w in ("otoyt.yml", "otoyt_tr.yml") for r in runs(w, today_utc)):
            continue  # bir kanal şu an üretiyor (iki kanal aynı anda üretmez; sıradaki işi bozmayalım)
        key = f"{wf}:{today_utc}"
        tries = int(state.get(key + ":tries", 0))  # yalnız bekçinin kendi denemeleri sayılır
        if tries >= MAX_TRIES:
            if not state.get(key + ":gave_up"):
                state[key + ":gave_up"] = True
                tg(f"🆘 CLAUDE'A YAZ — {name}: bekçi bugün {tries} kez yeniden denedi, video hâlâ çıkmadı. Bekçi durdu. "
                   "Claude'a 'kontrol et' yazman yeterli; yarınki video normal saatinde denenecek.")
            continue
        r = requests.post(f"{API}/actions/workflows/{wf}/dispatches", headers=H,
                          json={"ref": "main", "inputs": {"dry_run": "false"}}, timeout=30)
        state[key + ":tries"] = tries + 1
        print(f"{name}: üretim başlatıldı ({r.status_code}), bugünkü deneme {tries + 1}")
        tg(f"🔁 {name}: bugünün videosu henüz çıkmamıştı (gecikme veya hata). Bekçi üretimi otomatik başlattı "
           f"(bugünkü {tries + 1}. deneme). Yarıda kalan konu varsa aynısıyla devam edilir. Senden bir şey gerekmiyor.")
    # Instagram bekçisi: bugün video çıktıysa ama Reels paylaşılmadıysa günlük Instagram işini hemen çalıştır
    for wf_ig, hist_f, qfile, name in (("ig_en.yml", ROOT / "history.json", ROOT / "ig_queue.json", "Beyond Qaf"),
                                       ("ig_tr.yml", ROOT / "data/golgedefter/history.json",
                                        ROOT / "data/golgedefter/ig_queue.json", "Gölgedefter")):
        hist = json.loads(hist_f.read_text(encoding="utf-8")) if hist_f.exists() else []
        if not any(h.get("type", "long") == "long" and str(h.get("date", "")).startswith(today_utc) for h in hist):
            continue
        if any(h.get("type") == "reel" and str(h.get("date", "")).startswith(today_utc) for h in hist):
            continue  # bugün Reel paylaşıldı
        q = json.loads(qfile.read_text(encoding="utf-8")) if qfile.exists() else []
        key = f"{wf_ig}:{today_utc}:ig"
        if not q or state.get(key, 0) >= 2:
            continue
        if any(r["status"] in ("queued", "in_progress") for r in runs(wf_ig, today_utc)):
            continue
        state[key] = state.get(key, 0) + 1
        requests.post(f"{API}/actions/workflows/{wf_ig}/dispatches", headers=H, json={"ref": "main"}, timeout=30)
        print(f"{name}: bugün Reel paylaşılmamıştı, Instagram işi başlatıldı")
    state_f.write_text(json.dumps(state, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
