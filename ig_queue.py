"""Instagram sırası: her çalışmada sıradaki 1 Reel'i paylaşır.
pipeline.py her videonun ilk Short'unu hemen paylaşır, kalanları GitHub Release'e ("ig-queue") yükleyip
data/<kanal>/ig_queue.json'a ekler. Bu betik günde bir çalışır (ig_tr.yml)."""
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import requests
import yaml

import publish

ROOT = Path(__file__).parent
CFG = yaml.safe_load((ROOT / os.getenv("OTOYT_CONFIG", "config.yaml")).read_text(encoding="utf-8"))
if CFG.get("ig_user_id"):
    os.environ["IG_USER_ID"] = str(CFG["ig_user_id"])
DATA = ROOT / CFG.get("data_dir", ".")
QUEUE = DATA / "ig_queue.json"
HISTORY = DATA / "history.json"
RELEASE = CFG.get("ig_release", "ig-queue")


def notify(text):
    if CFG.get("notify_prefix"):
        text = f"{CFG['notify_prefix']} {text}"
    tok, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if tok and chat:
        try:
            requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                          json={"chat_id": chat, "text": text[:4000]}, timeout=20)
        except Exception:
            pass
    print(text)


def load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def gh(*args):
    return subprocess.run(["gh", *args], capture_output=True, text=True, timeout=600)


def main():
    if not CFG.get("instagram_enabled") or not (os.getenv("IG_ACCESS_TOKEN") and os.getenv("IG_USER_ID")):
        print("Instagram kapalı ya da anahtar yok.")
        return
    q = load(QUEUE, [])
    if not q:
        print("Sıra boş.")
        return
    item = q[0]
    tmp = Path(tempfile.mkdtemp())
    rel = item.get("release", RELEASE)  # elle eklenen öğe videoyu başka bir Release'ten (ör. paket) alabilir
    local = ROOT / item["path"] if item.get("path") else None  # elle eklenen öğe videoyu repodan da alabilir
    if local:
        video, r = local, None
    else:
        r = gh("release", "download", rel, "-p", item["asset"], "-D", str(tmp))
        video = tmp / item["asset"]
    if (r is not None and r.returncode != 0) or not video.exists():
        # Kayıp Release dosyası veya geçici indirme hatası, kuyruktaki işi silmemeli.
        detail = r.stderr[:200] if r is not None else "yerel dosya yok"
        message = f"Instagram kaynağı bulunamadı; kuyruk KORUNDU: {item['title']} ({detail})"
        (DATA / "ig_last_error.txt").write_text(
            f"{datetime.now().isoformat(timespec='minutes')} {message}\n", encoding="utf-8")
        notify(f"🆘 {message}")
        raise RuntimeError(message)
    try:
        cap = publish.ig_link_cta(item["caption"], CFG.get("language_code") == "tr")  # "link profilde" çağrısı
        mid, link = publish.instagram_reel(str(video), cap, ai_label=CFG.get("instagram_ai_label", True),
                                           expected_username=CFG.get("ig_username"))
    except Exception as e:
        (DATA / "ig_last_error.txt").write_text(f"{datetime.now().isoformat(timespec='minutes')} "
                                                f"{item['title']}\n{e}\n", encoding="utf-8")
        if "190" in str(e) or "access token" in str(e).lower():
            notify("🆘 CLAUDE'A YAZ — Instagram anahtarı geçersiz (süresi dolmuş). Reels sırada bekliyor; "
                   "anahtar yenilenince otomatik paylaşılır.")
        else:
            notify(f"ℹ️ Instagram Reel yüklenemedi (yarın tekrar denenecek): {str(e)[:300]}")
        sys.exit(1)
    hist = load(HISTORY, [])
    hist.append({"date": datetime.now().isoformat(timespec="minutes"), "type": "reel", "title": item["title"],
                 "pillar": item.get("pillar"), "video_id": mid, "parent": item.get("parent")})
    save(HISTORY, hist)
    save(QUEUE, q[1:])
    if local:  # repoya elle konan video paylaşıldı: repoyu şişirmesin diye silinir
        local.unlink(missing_ok=True)
    elif rel == RELEASE:  # paket Release'indeki dosyaya dokunma
        gh("release", "delete-asset", RELEASE, item["asset"], "-y")
    notify(f"📸 Instagram Reel yayında: {link or mid}  (sırada {len(q) - 1} kaldı)")


if __name__ == "__main__":
    main()
