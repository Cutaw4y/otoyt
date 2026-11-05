"""Kayıpsız Shorts kurtarma: özel Drive yedeği + YouTube etiketinden uzlaştırma.

Önemli: Yüklemesi belirsiz bir videoyu ASLA otomatik ikinci kez yükleme.
Yayımlanmamış videolar GitHub Releases'a konmaz (depo herkese açık).
"""
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import drive
import publish


def marker_for(parent, index):
    if not parent or len(parent) != 11 or index < 1:
        raise ValueError("Shorts kurtarma kimliği geçersiz")
    return f"otoyt_rescue_{parent}_{index}"


def with_marker(meta, marker):
    """İzleyicinin açıklamasına eklenmeden YouTube meta etiketine işaret koy."""
    m = dict(meta)
    tags = list(m.get("tags") or [])
    tags = [t for t in tags if t != marker]
    # YouTube toplam etiket limiti için yeterli pay bırak.
    while tags and len(",".join(tags + [marker]).encode("utf-8")) > 440:
        tags.pop()
    m["tags"] = tags + [marker]
    return m


def stage(video, channel, parent, index, title, pillar, meta):
    """Her Short'u ilk yüklemeden ÖNCE özel Drive'a yedekle."""
    if not drive.enabled():
        return None
    marker = marker_for(parent, index)
    import hashlib
    sha = hashlib.sha256()
    with open(video, "rb") as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b""):
            sha.update(block)
    record = {
        "version": 1, "marker": marker, "parent": parent,
        "index": index, "title": title, "pillar": pillar,
        "sha256": sha.hexdigest(), "meta": with_marker(meta, marker),
    }
    return drive.stage_short_backup(video, channel, marker, record)


def is_explicit_quota_error(exc):
    text = str(exc).lower()
    return any(s in text for s in ("quotaexceeded", "uploadlimitexceeded", "dailylimitexceeded"))


def upload(video, meta, uploader, backup_id=None):
    """Durum uploading olarak kaydedildikten sonra tam bir kez YouTube'a yükle."""
    if backup_id:
        drive.short_backup_status(backup_id, "uploading")
    try:
        result = publish.upload(video, meta, None, uploader)
    except Exception as e:
        if backup_id:
            # Geçerli upload ID'sinin dönmemesi işlemin başarısız olduğunu KANITLAMAZ.
            state = "deferred" if is_explicit_quota_error(e) else "uncertain"
            try:
                drive.short_backup_status(backup_id, state)
            except Exception:
                pass  # uploading kalır: sonraki iş yine körlemesine yüklemez
        raise
    if backup_id:
        try:
            drive.short_backup_status(backup_id, "uploaded", result)
        except Exception:
            # YouTube gerçekten başarılı oldu; Drive durumu tekrar uzlaştırılabilir.
            pass
    return result


def _record_history(history, item, video_id):
    marker = item["marker"]
    if any(h.get("video_id") == video_id or h.get("recovery_marker") == marker for h in history):
        return False
    history.append({
        "date": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "type": "short", "title": item["title"], "pillar": item.get("pillar"),
        "video_id": video_id, "parent": item["parent"], "recovery_marker": marker,
    })
    return True


def _future_meta(meta):
    """Kaçırılmış bir planlama saatine YouTube upload yapma; yakın geleceğe kaydır."""
    m = dict(meta)
    if m.get("publish_at"):
        try:
            due = datetime.fromisoformat(m["publish_at"].replace("Z", "+00:00"))
            if due <= datetime.now(timezone.utc) + timedelta(minutes=30):
                m["publish_at"] = (datetime.now(timezone.utc) + timedelta(hours=3)
                                   ).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (ValueError, TypeError):
            raise RuntimeError("Shorts yayın tarihi geçersiz")
    return m


def _identity_check(config):
    """Token'ın doğru kanala ait olduğunu içerik değiştirmeden doğrula."""
    title, channel_id = publish.my_channel()
    expected_id = config.get("channel_id")
    if not channel_id or expected_id and channel_id != expected_id:
        raise RuntimeError("YouTube kanal kimliği doğrulanamadı; Shorts kurtarma durduruldu")
    if not expected_id:
        canonical = lambda s: (s or "").replace("İ", "i").replace("I", "ı").casefold().replace(" ", "")
        allowed = [config["channel_name"]] + list(config.get("channel_aliases") or [])
        if canonical(title) not in {canonical(a) for a in allowed}:
            raise RuntimeError("Yanlış YouTube hesabı; Shorts kurtarma durduruldu")
    return channel_id


def recover(config, history_path, *, max_uploads=2, notify=print):
    """Drive yedeklerini incele; yükleme ancak güvenli biçimde henüz başlamamışsa tekrarla."""
    if not drive.enabled():
        notify("Shorts kurtarma pasif: özel Google Drive bağlantısı bulunamadı")
        return {"disabled": True}
    if config.get("uploader", "youtube") != "youtube":
        notify("Shorts kurtarma YouTube dışı yükleyicide devre dışı")
        return {"disabled": True}
    _identity_check(config)
    channel = config["channel_name"]
    backups = drive.list_short_backups(channel)
    history_path = Path(history_path)
    history = json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else []
    # Gerçek YouTube yüklemeleriyle de uzlaştır: hata son yanıtı kaybolmuş olabilir.
    recent = publish.recent_uploads(n=50)
    found = {}
    for item in recent:
        for tag in item.get("snippet", {}).get("tags", []) or []:
            if tag.startswith("otoyt_rescue_"):
                found[tag] = item["id"]
    counts = {"backups": len(backups), "uploaded": 0, "reconciled": 0,
              "deferred": 0, "uncertain": 0, "errors": 0}
    for b in backups:
        fid = b["id"]
        try:
            item = json.loads(b.get("description") or "{}")
            mark = item["marker"]
            if b.get("appProperties", {}).get("otoyt_marker") != mark:
                raise RuntimeError("Drive Shorts metadata uyuşmazlığı")
            if item.get("parent") is None or "meta" not in item:
                raise RuntimeError("Drive Shorts metaverisi eksik")
            state = b.get("appProperties", {}).get("otoyt_state", "uncertain")
            confirmed = found.get(mark) or (b.get("appProperties") or {}).get("youtube_id")
            if confirmed:
                if _record_history(history, item, confirmed):
                    history_path.parent.mkdir(parents=True, exist_ok=True)
                    history_path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
                if state != "uploaded":
                    drive.short_backup_status(fid, "uploaded", confirmed)
                counts["reconciled"] += 1
                continue
            if state in ("uncertain", "uploading"):
                # YouTube son 50 video içinde görmemek, kesin yüklenmediği anlamına gelmez.
                counts["uncertain"] += 1
                notify(f"🆘 {channel}: Short yükleme sonucu BELİRSİZ ({mark}); çift yükleme yapılmadı")
                continue
            if state == "uploaded":
                # Geçmişte id kayboldu; operatör kontrol etsin.
                counts["uncertain"] += 1
                notify(f"🆘 {channel}: yüklenmiş Short kimliği bulunamadı ({mark})")
                continue
            if state not in ("pending", "deferred"):
                continue
            if counts["uploaded"] >= max_uploads:
                break
            # Yeni dosya yüklemeden önce her işaret için son 50 YT gönderisini tekrar tara.
            # Yeni/boş hesapta bile yalnızca staging'i kesin olanlar yüklenir.
            meta = _future_meta(item["meta"])
            with tempfile.TemporaryDirectory(prefix="short-rescue-") as tmp:
                local = Path(tmp) / f"{mark}.mp4"
                drive.download_short_backup(fid, local)
                try:
                    sid = upload(local, meta, "youtube", backup_id=fid)
                except Exception as e:
                    counts["errors"] += 1
                    if is_explicit_quota_error(e):
                        counts["deferred"] += 1
                        notify(f"ℹ️ {channel}: Shorts yükleme kotası dolu, sonraki denemeye ertelendi")
                    else:
                        notify(f"🆘 {channel}: kurtarma yüklemesi belirsiz ({mark}): {str(e)[:180]}")
                    break
            if _record_history(history, item, sid):
                history_path.parent.mkdir(parents=True, exist_ok=True)
                history_path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
            counts["uploaded"] += 1
            notify(f"✅ {channel}: kayıp Short güvenle yüklendi: https://youtu.be/{sid}")
        except Exception as e:
            counts["errors"] += 1
            notify(f"🆘 {channel}: Shorts kurtarma hatası; yedek silinmedi: {str(e)[:180]}")
    return counts


def main():
    import yaml
    root = Path(__file__).resolve().parent
    config = yaml.safe_load((root / os.getenv("OTOYT_CONFIG", "config.yaml")).read_text(encoding="utf-8"))
    data = root / config.get("data_dir", ".")
    from pipeline import notify
    result = recover(config, data / "history.json", notify=notify)
    print("Shorts recovery:", result)


if __name__ == "__main__":
    main()
