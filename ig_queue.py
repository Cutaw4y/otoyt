"""Instagram sırası: her çalışmada sıradaki 1 Reel'i paylaşır.
pipeline.py her videonun ilk Short'unu hemen paylaşır, kalanları GitHub Release'e ("ig-queue") yükleyip
data/<kanal>/ig_queue.json'a ekler. Bu betik günde bir çalışır (ig_tr.yml)."""
import json
import os
import re
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
    if not text.startswith("🆘"):  # sade Telegram: Instagram'da yalnız müdahale gereken durumlar bildirilir
        print(text)
        return
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
    """Bozulmuş var olan kuyruğu boş sanıp üzerine yazma; hata ver ve kayıtları koru."""
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise RuntimeError(f"Instagram kaydı bozuk/okunamadı: {path}; dosya KORUNDU") from e
    if not isinstance(data, list):
        raise RuntimeError(f"Instagram kaydı liste değil: {path}; dosya KORUNDU")
    return data


def save(path, data):
    """Kuyruğu atomik değiştir; işlem yarıda kesilirse eski JSON aynen kalsın."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def safe_error(error):
    """Açık GitHub deposuna yazılan hata kayıtlarında gizli token bırakma."""
    message = str(error)
    for key in ("IG_ACCESS_TOKEN", "GD_IG_ACCESS_TOKEN", "TELEGRAM_BOT_TOKEN",
                "GITHUB_TOKEN", "GH_TOKEN", "IG_APP_SECRET"):
        value = os.getenv(key, "")
        if len(value) >= 6:
            message = message.replace(value, "[REDACTED]")
    message = re.sub(r"(?i)(access_token|client_secret|api_key|apikey|token)\s*"
                     r"(=|:)\s*(['\"]?)[^&\s,\"']{6,}",
                     r"\1\2[REDACTED]", message)
    message = re.sub(r"(?i)Bearer\s+\S+", "Bearer [REDACTED]", message)
    return message[:700]


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
    queue_key = f"{item.get('release', RELEASE)}:{item.get('asset')}" if item.get("asset") else (
        f"local:{item.get('path')}")
    previous = load(HISTORY, [])
    if any(row.get("type") == "reel" and row.get("queue_key") == queue_key
           for row in previous if isinstance(row, dict)):
        # Reel gerçekten kaydedilmiş, sadece kuyruk ilerletme adımı kesilmiş.
        save(QUEUE, q[1:])
        notify(f"ℹ️ Instagram Reel zaten yayımlanmış; tekrar yüklemeden kuyruk düzeltildi: {item.get('title', '')}")
        return
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
        detail = safe_error(r.stderr[:200]) if r is not None else "yerel dosya yok"
        message = f"Instagram kaynağı bulunamadı; kuyruk KORUNDU: {item['title']} ({detail})"
        (DATA / "ig_last_error.txt").write_text(
            f"{datetime.now().isoformat(timespec='minutes')} {message}\n", encoding="utf-8")
        # Sıradaki diğer sağlıklı Reels engellenmesin: kayıp dosyayı sona taşı, silme.
        if len(q) > 1:
            save(QUEUE, q[1:] + [q[0]])
        notify(f"🆘 {message}")
        raise RuntimeError(message)
    try:
        cap = publish.ig_link_cta(item["caption"], CFG.get("language_code") == "tr")  # "link profilde" çağrısı
        mid, link = publish.instagram_reel(str(video), cap, ai_label=CFG.get("instagram_ai_label", True),
                                           expected_username=CFG.get("ig_username"))
    except Exception as e:
        (DATA / "ig_last_error.txt").write_text(f"{datetime.now().isoformat(timespec='minutes')} "
                                                f"{item['title']}\n{safe_error(e)}\n", encoding="utf-8")
        if "190" in str(e) or "access token" in str(e).lower():
            notify("🆘 CLAUDE'A YAZ — Instagram anahtarı geçersiz (süresi dolmuş). Reels sırada bekliyor; "
                   "anahtar yenilenince otomatik paylaşılır.")
        else:
            notify(f"ℹ️ Instagram Reel yüklenemedi (yarın tekrar denenecek): {safe_error(e)[:300]}")
        sys.exit(1)
    hist = load(HISTORY, [])
    hist.append({"date": datetime.now().isoformat(timespec="minutes"), "type": "reel", "title": item["title"],
                 "pillar": item.get("pillar"), "video_id": mid, "parent": item.get("parent"),
                 "queue_key": queue_key})
    save(HISTORY, hist)
    save(QUEUE, q[1:])
    if local:  # repoya elle konan video paylaşıldı: repoyu şişirmesin diye silinir
        local.unlink(missing_ok=True)
    elif rel == RELEASE:  # paket Release'indeki dosyaya dokunma
        gh("release", "delete-asset", RELEASE, item["asset"], "-y")
    notify(f"📸 Instagram Reel yayında: {link or mid}  (sırada {len(q) - 1} kaldı)")


if __name__ == "__main__":
    main()
