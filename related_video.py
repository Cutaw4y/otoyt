"""YouTube Shorts 'İlgili video' için Studio görev kaydı.

YouTube Data API videonun related-video alanını güncellemeyi desteklemez.
Bu kayıtlar yalnızca KANITLI kısa/uzun video eşleşmeleridir; Studio'daki
bağlantının gerçekten eklenmiş olduğunu KESİNLİKLE iddia etmez.

Kullanım:
  python related_video.py              # iki kanalın kontrol listesini gösterir
  python related_video.py --json       # yapılandırılmış, salt okunur rapor
  python related_video.py --sync       # geçmişteki eşleşmeleri taslak görev listesine kaydeder
"""
import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
CHANNELS = (("Beyond Qaf", "."), ("Gölgedefter", "data/golgedefter"))


def validate(video_id):
    if not isinstance(video_id, str) or not VIDEO_ID.fullmatch(video_id):
        raise ValueError("Geçersiz YouTube video kimliği")
    return video_id


def task(short_id, parent_id, title, channel):
    short_id, parent_id = validate(short_id), validate(parent_id)
    if short_id == parent_id:
        raise ValueError("Short kendi kendisine ilgili video olamaz")
    return {
        "short_id": short_id,
        "parent_id": parent_id,
        "title": str(title or "").strip()[:150],
        "channel": str(channel or "").strip()[:100],
        "state": "studio_unverified",
        "studio_url": f"https://studio.youtube.com/video/{short_id}/edit",
        "parent_url": f"https://youtu.be/{parent_id}",
    }


def read_list(path):
    p = Path(path)
    if not p.exists():
        return []
    value = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError(f"Liste bekleniyordu: {p}")
    return value


def register(queue_path, short_id, parent_id, title, channel):
    """Mevcut Short'u idempotent kaydeder; yanlış parent güncellemesi güvenle reddedilir."""
    queue_path = Path(queue_path)
    item = task(short_id, parent_id, title, channel)
    current = read_list(queue_path)
    for old in current:
        if old.get("short_id") == short_id:
            if old.get("parent_id") != parent_id:
                raise ValueError("Aynı Short için iki farklı uzun video kayıtlı; elle kontrol edilmeli")
            # Studio'daki onaylandı durumunu otomatik varsayma/değiştirme.
            return False
    item["registered_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    current.append(item)
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    temp = queue_path.with_name(queue_path.name + ".tmp")
    temp.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, queue_path)
    return True


def history_links(history_path, channel):
    """Sadece doğrulanabilir Short -> uzun video eşleşmelerini çıkar."""
    history = read_list(history_path)
    longs = {row.get("video_id") for row in history
             if isinstance(row, dict) and row.get("type", "long") == "long"}
    entries = {}
    for row in history:
        if not isinstance(row, dict) or row.get("type") != "short":
            continue
        short_id, parent = row.get("video_id"), row.get("parent")
        if parent not in longs:
            continue  # eski/elle yüklenen Shorts'ta tahminde bulunma
        try:
            current = task(short_id, parent, row.get("title"), channel)
        except ValueError:
            continue
        if short_id in entries and entries[short_id]["parent_id"] != parent:
            raise ValueError(f"{channel}: {short_id} iki farklı uzun videoya bağlanmış")
        entries[short_id] = current
    return list(entries.values())


def collect(root, sync=False):
    """Dış API çağrısı yok. sync=False iken kesinlikle dosya değiştirmez."""
    root = Path(root)
    result = []
    for channel, folder in CHANNELS:
        data = root / folder
        history = history_links(data / "history.json", channel)
        queue = data / "related_video_tasks.json"
        if sync:
            for row in history:
                register(queue, row["short_id"], row["parent_id"], row["title"], channel)
        tracked = {t["short_id"]: t for t in read_list(queue) if isinstance(t, dict)
                   and isinstance(t.get("short_id"), str)}
        for row in history:
            saved = tracked.get(row["short_id"])
            if saved and saved.get("parent_id") != row["parent_id"]:
                raise ValueError(f"{channel}: geçmiş ve ilgili video kuyruğu uyuşmuyor")
            # Kaydedilmiş görevin bile gerçekten Studio'da uygulanmış olduğu doğrulanamaz.
            result.append({**row, "state": "studio_unverified"})
    return result


def markdown(rows):
    lines = [
        "# Shorts → uzun video: YouTube Studio kontrol listesi",
        "",
        "Bu liste yalnızca doğru hedefleri eşleştirir; bağlantıları YouTube'a EKLEMEZ.",
        "YouTube Studio → İçerik → Short → İlgili video → hedef uzun video → Kaydet.",
        "Uzun videonun herkese açık veya liste dışı olması gerekir.",
        "",
        "| Kanal | Short (Studio'yu aç) | İlgili uzun video | Durum |",
        "| --- | --- | --- | --- |",
    ]
    for row in rows:
        title = row["title"].replace("|", "/").replace("\n", " ").strip()
        lines.append(f"| {row['channel']} | [{title or row['short_id']}]({row['studio_url']}) "
                     f"| [Uzun video]({row['parent_url']}) | Studio'da doğrulanmadı |")
    return "\n".join(lines) + "\n"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    p.add_argument("--sync", action="store_true",
                   help="Geçmişte eşleşmiş Shorts için yalnızca yerel görev kaydı oluşturur")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    rows = collect(args.root, sync=args.sync)
    print(json.dumps(rows, ensure_ascii=False, indent=2) if args.json else markdown(rows))


if __name__ == "__main__":
    main()
