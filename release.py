"""Elle yükleme paketini GitHub 'Release' olarak yayınlar: telefondan her dosya tek tek, zip'siz indirilir.
Release dosyaları Actions'ın 500 MB artifact kotasına sayılmaz. Paket tamamlanınca tamamlayıcı siler.
Gerekli: GITHUB_TOKEN (iş akışı verir) ve GITHUB_REPOSITORY."""
import os
import time
from pathlib import Path

import requests

API = "https://api.github.com"


def enabled():
    return bool(os.getenv("GITHUB_TOKEN") and os.getenv("GITHUB_REPOSITORY"))


def _h(extra=None):
    h = {"Authorization": f"Bearer {os.getenv('GITHUB_TOKEN')}", "Accept": "application/vnd.github+json",
         "X-GitHub-Api-Version": "2022-11-28"}
    h.update(extra or {})
    return h


def _repo():
    return os.getenv("GITHUB_REPOSITORY")


def create(tag, title, body, files, draft=False):
    """Release oluşturur, dosyaları yükler. (release_id, sayfa_bağlantısı) döner."""
    r = requests.post(f"{API}/repos/{_repo()}/releases", headers=_h(), timeout=60, json={
        "tag_name": tag, "target_commitish": os.getenv("GITHUB_SHA") or "main", "name": title,
        "body": body[:120000], "draft": draft, "prerelease": True})
    r.raise_for_status()
    rel = r.json()
    up = rel["upload_url"].split("{")[0]
    for f in files:
        f = Path(f)
        ctype = {"mp4": "video/mp4", "jpg": "image/jpeg", "srt": "text/plain", "txt": "text/plain"}.get(
            f.suffix.lstrip("."), "application/octet-stream")
        for attempt in range(3):
            try:
                with open(f, "rb") as fh:
                    u = requests.post(f"{up}?name={f.name}", data=fh, timeout=1800,
                                      headers=_h({"Content-Type": ctype, "Content-Length": str(f.stat().st_size)}))
                if u.status_code in (200, 201):
                    break
                if u.status_code == 422:  # aynı adla yarım kalmış yükleme: sil, tekrar dene
                    for a in requests.get(f"{API}/repos/{_repo()}/releases/{rel['id']}/assets", headers=_h(),
                                          timeout=60).json():
                        if a["name"] == f.name:
                            requests.delete(f"{API}/repos/{_repo()}/releases/assets/{a['id']}", headers=_h(), timeout=60)
                u.raise_for_status()
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(10)
    return rel["id"], rel["html_url"]


def remove(release_id, tag=None):
    """Tamamlanan paketin release'ini ve etiketini siler (sistemin kendi geçici kopyası)."""
    ok = True
    try:
        r = requests.delete(f"{API}/repos/{_repo()}/releases/{release_id}", headers=_h(), timeout=60)
        ok = r.status_code in (204, 404)
    except Exception:
        ok = False
    if tag:
        try:
            requests.delete(f"{API}/repos/{_repo()}/git/refs/tags/{tag}", headers=_h(), timeout=60)
        except Exception:
            pass
    return ok
