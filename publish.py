"""
Yükleme (YouTube API veya Ayrshare) + YouTube Analytics okuma.
config.yaml > uploader: "youtube" | "ayrshare"
"""
import math
import os
import time
from datetime import date, datetime, timedelta

import requests

# Not: token yenilenirken kapsam belirtilmez → izin verilmiş tüm kapsamlar gelir
# (yeni bir kapsam eklendiğinde eski token bozulmasın diye).
YT_SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl",
             "https://www.googleapis.com/auth/youtube.upload",
             "https://www.googleapis.com/auth/youtube",
             "https://www.googleapis.com/auth/yt-analytics.readonly"]


def _retry(fn, tries=4, base=10, what="işlem"):
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # ağ / 5xx / geçici kota hataları
            last = e
            msg = str(e)
            if any(k in msg for k in ("quotaExceeded", "uploadLimitExceeded", "forbidden", "invalid_grant")):
                raise  # beklemekle düzelmeyen hatalar
            time.sleep(base * (2 ** i))
    raise RuntimeError(f"{what} {tries} denemede başarısız: {last}")


# ================================================================ YouTube API
def _creds():
    from google.oauth2.credentials import Credentials
    return Credentials(None, refresh_token=os.environ["YT_REFRESH_TOKEN"],
                       client_id=os.environ["YT_CLIENT_ID"],
                       client_secret=os.environ["YT_CLIENT_SECRET"],
                       token_uri="https://oauth2.googleapis.com/token")


def _yt():
    from googleapiclient.discovery import build
    return build("youtube", "v3", credentials=_creds(), cache_discovery=False)


def youtube_upload(video, meta, thumb=None):
    from googleapiclient.http import MediaFileUpload
    yt = _yt()
    status = {"selfDeclaredMadeForKids": meta["made_for_kids"],
              "containsSyntheticMedia": meta["synthetic"]}
    if meta.get("publish_at"):
        status.update(privacyStatus="private", publishAt=meta["publish_at"])
    else:
        status["privacyStatus"] = meta["privacy"]
    body = {"snippet": {"title": meta["title"][:100], "description": meta["description"][:4900],
                        "tags": meta["tags"], "categoryId": meta["category_id"],
                        "defaultLanguage": meta["lang"], "defaultAudioLanguage": meta["lang"]},
            "status": status}
    if meta.get("localizations"):  # ör. Türk izleyiciye Türkçe başlık/açıklama
        body["localizations"] = meta["localizations"]

    def do():
        part = "snippet,status,localizations" if "localizations" in body else "snippet,status"
        req = yt.videos().insert(part=part, body=body, notifySubscribers=True,
                                 media_body=MediaFileUpload(str(video), chunksize=8 << 20, resumable=True))
        resp = None
        while resp is None:
            _, resp = req.next_chunk(num_retries=5)
        return resp["id"]

    vid = _retry(do, what="YouTube yükleme")
    if thumb:
        try:
            _retry(lambda: yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(str(thumb))).execute(),
                   tries=2, what="thumbnail")
        except Exception as e:
            print(f"Thumbnail yüklenemedi (kanal doğrulanmamış olabilir): {e}")
    return vid


# ================================================================ Ayrshare (B planı)
AYR = "https://api.ayrshare.com/api"


def _ayr_headers():
    return {"Authorization": f"Bearer {os.environ['AYRSHARE_API_KEY']}"}


def _ayr_upload_file(path, ctype):
    def do():
        r = requests.get(f"{AYR}/media/uploadUrl", headers=_ayr_headers(),
                         params={"fileName": os.path.basename(path), "contentType": ctype}, timeout=60)
        r.raise_for_status()
        j = r.json()
        with open(path, "rb") as fh:
            p = requests.put(j["uploadUrl"], data=fh, headers={"Content-Type": j["contentType"]}, timeout=1800)
        p.raise_for_status()
        return j["accessUrl"]
    return _retry(do, what="Ayrshare dosya yükleme")


def ayrshare_upload(video, meta, thumb=None):
    yt_opts = {"title": meta["title"][:100], "visibility": "private" if meta.get("publish_at") else meta["privacy"],
               "madeForKids": meta["made_for_kids"], "containsSyntheticMedia": meta["synthetic"],
               "categoryId": int(meta["category_id"]), "tags": meta["tags"],
               "shorts": bool(meta.get("is_short"))}
    if meta.get("publish_at"):
        yt_opts["publishAt"] = meta["publish_at"]
    if thumb and not meta.get("is_short"):
        yt_opts["thumbNail"] = _ayr_upload_file(thumb, "jpg")
    url = _ayr_upload_file(video, "mp4")
    body = {"post": meta["description"][:4900], "platforms": ["youtube"],
            "mediaUrls": [url], "youTubeOptions": yt_opts}

    def do():
        r = requests.post(f"{AYR}/post", headers=_ayr_headers(), json=body, timeout=600)
        j = r.json()
        if r.status_code >= 400 or j.get("status") == "error":
            raise RuntimeError(f"Ayrshare: {j}")
        for p in j.get("postIds", []):
            if p.get("platform") == "youtube":
                return p.get("id") or p.get("postUrl", "")
        return j.get("id", "")
    return _retry(do, tries=2, what="Ayrshare paylaşım")


def upload_caption(video_id, srt_path, lang, name):
    """Videoya altyazı izi ekler (izleyici ayarlardan seçer). youtube.force-ssl izni gerekir."""
    from googleapiclient.http import MediaFileUpload
    yt = _yt()
    body = {"snippet": {"videoId": video_id, "language": lang, "name": name, "isDraft": False}}
    return _retry(lambda: yt.captions().insert(
        part="snippet", body=body,
        media_body=MediaFileUpload(str(srt_path), mimetype="application/octet-stream")).execute(),
        tries=2, what=f"{lang} altyazı")["id"]


def set_branding(banner=None, description=None, keywords=None, country=None, lang=None):
    """Kanal afişini ve 'Hakkında' bilgilerini API ile ayarlar (profil resmi API ile değiştirilemez)."""
    from googleapiclient.http import MediaFileUpload
    yt = _yt()
    ch = yt.channels().list(part="brandingSettings", mine=True).execute()["items"][0]
    bs = ch.get("brandingSettings", {})
    bs.setdefault("channel", {})
    if description:
        bs["channel"]["description"] = description[:1000]
    if keywords:
        bs["channel"]["keywords"] = keywords[:500]
    if country:
        bs["channel"]["country"] = country
    if lang:
        bs["channel"]["defaultLanguage"] = lang
    if banner:
        url = yt.channelBanners().insert(
            media_body=MediaFileUpload(str(banner), mimetype="image/jpeg")).execute()["url"]
        bs.setdefault("image", {})["bannerExternalUrl"] = url
    yt.channels().update(part="brandingSettings", body={"id": ch["id"], "brandingSettings": bs}).execute()
    return ch["id"]


def upload(video, meta, thumb=None, uploader="youtube"):
    return ayrshare_upload(video, meta, thumb) if uploader == "ayrshare" else youtube_upload(video, meta, thumb)


# ================================================================ Instagram Reels
# Facebook Login for Business varyantı: yerel dosyayı doğrudan Meta'ya yükler
# (videoyu herkese açık bir sunucuda barındırmak gerekmez).
IG = "https://graph.facebook.com"


def _ig_err(r):
    try:
        e = r.json().get("error", {})
        return f"{r.status_code} {e.get('message', r.text[:300])} (code {e.get('code')})"
    except Exception:
        return f"{r.status_code} {r.text[:300]}"


def instagram_reel(video, caption, ai_label=True, poll_every=20, max_wait=900):
    """Reel yayınlar, (media_id, permalink) döner."""
    token, ig_id = os.environ["IG_ACCESS_TOKEN"], os.environ["IG_USER_ID"]
    auth = {"Authorization": f"OAuth {token}"}

    def create_and_upload():
        data = {"media_type": "REELS", "upload_type": "resumable",
                "caption": caption[:2200], "thumb_offset": "1500"}
        if ai_label:
            data["is_ai_generated"] = "true"
        r = requests.post(f"{IG}/{ig_id}/media", headers=auth, data=data, timeout=60)
        if r.status_code == 400 and ai_label and "is_ai_generated" in r.text:
            data.pop("is_ai_generated")  # alan desteklenmiyorsa etiketsiz devam et
            r = requests.post(f"{IG}/{ig_id}/media", headers=auth, data=data, timeout=60)
        if not r.ok:
            raise RuntimeError(f"Instagram konteyner: {_ig_err(r)}")
        j = r.json()
        cid, uri = j["id"], j.get("uri") or f"https://rupload.facebook.com/ig-api-upload/{j['id']}"
        size = os.path.getsize(video)
        with open(video, "rb") as fh:
            u = requests.post(uri, headers={**auth, "offset": "0", "file_size": str(size)},
                              data=fh, timeout=1800)
        if not u.ok or not u.json().get("success", False):
            raise RuntimeError(f"Instagram yükleme: {_ig_err(u)}")
        return cid

    cid = _retry(create_and_upload, tries=3, base=15, what="Instagram yükleme")

    waited = 0
    while True:  # Meta videoyu işleyene kadar bekle
        s = requests.get(f"{IG}/{cid}", headers=auth, params={"fields": "status_code,status"}, timeout=30).json()
        code = s.get("status_code")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram işleme hatası: {s}")
        if waited >= max_wait:
            raise RuntimeError(f"Instagram işleme {max_wait} sn'de bitmedi: {s}")
        time.sleep(poll_every)
        waited += poll_every

    def pub():
        r = requests.post(f"{IG}/{ig_id}/media_publish", headers=auth, data={"creation_id": cid}, timeout=120)
        if not r.ok:
            raise RuntimeError(f"Instagram yayın: {_ig_err(r)}")
        return r.json()["id"]

    mid = _retry(pub, tries=3, base=20, what="Instagram yayın")
    try:
        link = requests.get(f"{IG}/{mid}", headers=auth, params={"fields": "permalink"}, timeout=30).json().get("permalink", "")
    except Exception:
        link = ""
    return mid, link


# ================================================================ Analytics (öğrenme döngüsü)
def video_stats(video_ids):
    """{video_id: {'views':int, 'avg_pct':float}} — yetki yoksa boş döner, sistem durmaz."""
    ids = [v for v in video_ids if v and len(v) == 11]
    if not ids or not os.getenv("YT_REFRESH_TOKEN"):
        return {}
    out = {}
    try:
        from googleapiclient.discovery import build
        ya = build("youtubeAnalytics", "v2", credentials=_creds(), cache_discovery=False)
        for i in range(0, len(ids), 200):
            chunk = ids[i:i + 200]
            r = ya.reports().query(ids="channel==MINE", startDate="2020-01-01", endDate=date.today().isoformat(),
                                   metrics="views,averageViewPercentage", dimensions="video",
                                   filters="video==" + ",".join(chunk), maxResults=200).execute()
            for vid, views, pct in r.get("rows", []):
                out[vid] = {"views": int(views), "avg_pct": float(pct)}
    except Exception as e:
        print(f"Analytics okunamadı, yalnızca izlenme sayısı denenecek: {e}")
        try:
            yt = _yt()
            for i in range(0, len(ids), 50):
                r = yt.videos().list(part="statistics", id=",".join(ids[i:i + 50])).execute()
                for it in r.get("items", []):
                    out[it["id"]] = {"views": int(it["statistics"].get("viewCount", 0)), "avg_pct": 40.0}
        except Exception as e2:
            print(f"İstatistik okunamadı: {e2}")
    return out


def score(stat):
    """İzlenme (log) × izleyici tutma. Tutma, algoritmanın en çok önemsediği sinyal."""
    return math.log1p(stat["views"]) * (0.5 + stat["avg_pct"] / 100)


def performance_report(history, min_age_days=4):
    """Konu sütunlarının ağırlıkları + en iyi/kötü başlıklar (fikir üreticiye geri beslenir)."""
    cutoff = datetime.now() - timedelta(days=min_age_days)
    old = [h for h in history if h.get("type", "long") == "long" and h.get("video_id")
           and datetime.fromisoformat(h["date"]) < cutoff]
    stats = video_stats([h["video_id"] for h in old])
    rows = []
    for h in old:
        if h["video_id"] in stats:
            s = stats[h["video_id"]]
            rows.append({**h, **s, "score": score(s)})
    by_pillar = {}
    for r in rows:
        by_pillar.setdefault(r["pillar"], []).append(r["score"])
    rows.sort(key=lambda r: r["score"], reverse=True)
    return {"pillar_scores": {p: sum(v) / len(v) for p, v in by_pillar.items()},
            "best": rows[:5], "worst": rows[-5:] if len(rows) >= 10 else [], "n": len(rows)}


def clear_localizations():
    """Kanaldaki tüm videolardan çeviri başlık/açıklamalarını kaldırır (herkes ana dili görür).
    Dönüş: temizlenen video sayısı."""
    yt = _yt()
    ch = yt.channels().list(part="contentDetails", mine=True).execute()["items"][0]
    uploads = ch["contentDetails"]["relatedPlaylists"]["uploads"]
    ids, tok = [], None
    while True:
        r = yt.playlistItems().list(part="contentDetails", playlistId=uploads, maxResults=50,
                                    pageToken=tok).execute()
        ids += [i["contentDetails"]["videoId"] for i in r.get("items", [])]
        tok = r.get("nextPageToken")
        if not tok:
            break
    n = 0
    for k in range(0, len(ids), 50):
        for v in yt.videos().list(part="snippet,localizations", id=",".join(ids[k:k + 50])).execute().get("items", []):
            if not v.get("localizations"):
                continue
            sn = v["snippet"]
            body = {"id": v["id"], "localizations": {},
                    "snippet": {k2: sn[k2] for k2 in ("title", "description", "categoryId", "tags",
                                                      "defaultLanguage", "defaultAudioLanguage") if k2 in sn}}
            yt.videos().update(part="snippet,localizations", body=body).execute()
            n += 1
    return n
