"""Salt okunur YouTube/Instagram veri tutarlılığı raporu.

Bu dosya API anahtarı istemez, video oluşturmaz, video yayınlamaz ve hiçbir
GitHub/YouTube/Instagram kaydını değiştirmez.

Kullanım:
  python audit_state.py
  python audit_state.py --json
  python audit_state.py --root tests/fixtures
"""
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


CHANNELS = (
    ("Beyond Qaf", ".", "ig-queue-beyondqaf"),
    ("Gölgedefter", "data/golgedefter", "ig-queue"),
)


def _read_list(path, errors):
    if not path.is_file():
        errors.append(f"Zorunlu kayıt bulunamadı: {path}")
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        errors.append(f"JSON okunamadı: {path} ({type(exc).__name__})")
        return []
    if not isinstance(value, list):
        errors.append(f"Kayıt liste değil: {path}")
        return []
    return value


def _within(root, child):
    try:
        child.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def audit_channel(root, label, data_dir, default_release, expected_shorts=3):
    """Geçmişi ve kuyruğu denetle; bulgu oluştur, hiçbir dosyaya yazma."""
    base = root if data_dir == "." else root / data_dir
    errors, warnings = [], []
    history = _read_list(base / "history.json", errors)
    queue = _read_list(base / "ig_queue.json", errors)
    if not isinstance(expected_shorts, int) or expected_shorts < 1:
        raise ValueError("Beklenen Shorts sayısı pozitif tam sayı olmalı")
    clean_history = []
    seen_videos = {}
    for i, row in enumerate(history):
        if not isinstance(row, dict):
            errors.append(f"{label}: geçmiş satırı {i} nesne değil")
            continue
        typ, vid = row.get("type", "long"), row.get("video_id")
        if typ not in ("long", "short", "reel"):
            warnings.append(f"{label}: bilinmeyen kayıt türü {typ!r}")
        if vid:
            ident = (typ, str(vid))
            if ident in seen_videos:
                errors.append(f"{label}: aynı {typ} video kimliği tekrar kaydedilmiş: {vid}")
            else:
                seen_videos[ident] = i
        clean_history.append(row)
    longs = [h for h in clean_history if h.get("type", "long") == "long" and h.get("video_id")]
    short_rows = [h for h in clean_history if h.get("type") == "short" and h.get("video_id")]
    reels = [h for h in clean_history if h.get("type") == "reel" and h.get("video_id")]
    shorts = defaultdict(set)
    for row in short_rows:
        if row.get("parent"):
            shorts[str(row["parent"])].add(str(row["video_id"]))
    parent_ids = {str(h["video_id"]) for h in longs}
    orphans = sorted(p for p in shorts if p not in parent_ids)
    if orphans:
        warnings.append(f"{label}: kaydı olmayan uzun video bağlantıları: {', '.join(orphans)}")
    unlinked = sum(not h.get("parent") for h in short_rows)
    if unlinked:
        warnings.append(
            f"{label}: {unlinked} eski/elle yüklenen Short parent bağlantısı olmadan kayıtlı; "
            "kayıp Short olduğu anlamına gelmez"
        )
    incomplete = []
    for row in longs:
        count = len(shorts.get(str(row["video_id"]), ()))
        if 0 < count < expected_shorts:
            incomplete.append({"parent": row["video_id"], "count": count, "expected": expected_shorts})
        elif count == 0:
            # Eski manuel paketlerin parent alanı yok; bu kesin eksik video değildir.
            warnings.append(f"{label}: {row['video_id']} için eşleştirilmiş Short bulunamadı; manuel geçmişi kontrol et")
    if incomplete:
        warnings.append(f"{label}: {len(incomplete)} uzun videonun kayıtlı Shorts sayısı hedefin altında")
    seen_queue = set()
    clean_queue = []
    for i, item in enumerate(queue):
        if not isinstance(item, dict):
            errors.append(f"{label}: kuyruk satırı {i} nesne değil")
            continue
        title = str(item.get("title") or f"sıra {i+1}")
        path, asset = item.get("path"), item.get("asset")
        if bool(path) == bool(asset):
            errors.append(f"{label}: {title}: path veya asset bilgilerinden tam biri gerekli")
            continue
        if path:
            local = root / str(path)
            if not _within(root, local):
                errors.append(f"{label}: {title}: repo dışına çıkan dosya yolu")
            elif not local.is_file():
                errors.append(f"{label}: {title}: yerel Reel dosyası bulunamadı ({path})")
            identity = ("local", str(path))
        else:
            identity = (str(item.get("release") or default_release), str(asset))
        if identity in seen_queue:
            warnings.append(f"{label}: Reel kuyruğunda aynı kaynak birden fazla: {identity[1]}")
        seen_queue.add(identity)
        clean_queue.append({"title": title, "source": identity[0], "file": identity[1]})
    return {
        "channel": label,
        "counts": {"long": len(longs), "short": len(short_rows), "reel": len(reels),
                   "queued_reel": len(clean_queue), "unlinked_short": unlinked},
        "missing_short_groups": incomplete,
        "queued": clean_queue,
        "errors": errors,
        "warnings": warnings,
    }


def audit(root):
    """İki kanal için tek birleşik rapor, tamamen salt okunur."""
    root = Path(root)
    channels = [
        audit_channel(root, label, base, release_name)
        for label, base, release_name in CHANNELS
    ]
    return {
        "read_only": True,
        "release_assets_checked": False,
        "channels": channels,
        "errors": sum(len(c["errors"]) for c in channels),
        "warnings": sum(len(c["warnings"]) for c in channels),
        "note": ("GitHub Release dosyaları çevrimdışı kontrolde doğrulanmaz. "
                 "Eski manuel Shorts bağlantısız olabilir; kayıt eksikliği yükleme başarısızlığı kanıtı değildir."),
    }


def main():
    parser = argparse.ArgumentParser(description="YouTube otomasyonu salt okunur veri denetimi")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--json", action="store_true", help="Makine tarafından okunabilir rapor")
    args = parser.parse_args()
    report = audit(args.root)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for c in report["channels"]:
            print(f"{c['channel']}: {c['counts']}")
            for problem in c["errors"]:
                print("HATA:", problem)
            for problem in c["warnings"]:
                print("UYARI:", problem)
        print(f"Toplam: {report['errors']} hata, {report['warnings']} uyarı.")
        print(report["note"])
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
