"""
Yükleme (YouTube API veya Ayrshare) + YouTube Analytics okuma.
config.yaml > uploader: "youtube" | "ayrshare"
"""
import math
import os
from pathlib import Path
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


UPLOAD_WARNINGS = []  # planlı yayın saati kaydedilmeyen videolar (API kilidi vb.)


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
    try:  # API onayı/kilit kontrolü: planlı yayın gerçekten kaydedildi mi?
        st = yt.videos().list(part="status", id=vid).execute()["items"][0]["status"]
        if meta.get("publish_at") and not st.get("publishAt"):
            UPLOAD_WARNINGS.append(vid)
    except Exception:
        pass
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


def srt_to_ytt(srt_path, out_path, top=True, size=None):
    """SRT → YouTube zamanlı metin (srv3). Seçilen altyazı gömülü yazının hemen üstünde görünür, çakışmaz."""
    import re
    from xml.sax.saxutils import escape
    txt = open(srt_path, encoding="utf-8-sig").read().replace("\r", "")
    def ms(s):
        h, m, rest = s.strip().replace(".", ",").split(":")
        se, mi = rest.split(",")
        return ((int(h) * 60 + int(m)) * 60 + int(se)) * 1000 + int(mi)
    ps = []
    size = size or int(os.getenv("CAPTION_SIZE", "75"))  # yazı boyutu (%): küçük, videonun gömülü yazısını ezmesin
    pen = ' p="1"' if size != 100 else ""
    for blk in re.split(r"\n\s*\n", txt.strip()):
        ls = [l for l in blk.split("\n") if l.strip()]
        i = next((k for k, l in enumerate(ls) if "-->" in l), None)
        if i is None:
            continue
        a, b = ls[i].split("-->")
        t0, t1 = ms(a), ms(b.split()[0])
        body = escape(" ".join(ls[i + 1:]).strip())
        if body:
            ps.append(f'<p t="{t0}" d="{max(1, t1 - t0)}" wp="1" ws="1"{pen}>{body}</p>')
    # üstte bölüm kartı (sol) ve bilgi kutusu (sağ) var; gömülü yazı en altta (~%82'den aşağı).
    # Seçilen iz, gömülü yazının hemen ÜSTÜNE oturur: iki dilli altyazı gibi alt alta, hiçbir şeyle çakışmaz.
    pos = 'ap="7" ah="50" av="78"' if top else 'ap="7" ah="50" av="94"'
    xml = ('<?xml version="1.0" encoding="utf-8"?>\n<timedtext format="3">\n<head>\n'
           f'<pen id="1" sz="{size}"/>\n<wp id="1" {pos}/>\n<ws id="1" ju="2"/>\n</head>\n<body>\n' + "\n".join(ps) + "\n</body>\n</timedtext>\n")
    open(out_path, "w", encoding="utf-8").write(xml)
    return out_path


def upload_caption(video_id, srt_path, lang, name, top=True):
    """Videoya altyazı izi ekler (izleyici CC ile seçer). youtube.force-ssl izni gerekir.
    top=True: iz ekranın üstünde gösterilir (gömülü alt yazıyla çakışmasın); olmazsa düz SRT yüklenir."""
    from googleapiclient.http import MediaFileUpload
    yt = _yt()
    try:  # aynı dilde elle eklenmiş (otomatik olmayan) iz varsa tekrar ekleme
        for c in yt.captions().list(part="snippet", videoId=video_id).execute().get("items", []):
            s = c["snippet"]
            if s.get("language") == lang and s.get("trackKind") != "asr":
                return c["id"]
    except Exception:
        pass
    body = {"snippet": {"videoId": video_id, "language": lang, "name": name, "isDraft": False}}
    files = []
    if top:
        try:
            files.append(srt_to_ytt(srt_path, str(srt_path) + ".srv3", top=True))
        except Exception:
            pass
    files.append(str(srt_path))
    err = None
    for f in files:
        try:
            r = _retry(lambda: yt.captions().insert(
                part="snippet", body=body,
                media_body=MediaFileUpload(f, mimetype="application/octet-stream")).execute(),
                tries=2, what=f"{lang} altyazı")["id"]
            print(f"[altyazı] {video_id} {lang}: {'ÜSTTE (srv3)' if f.endswith('.srv3') else 'düz SRT'}")
            return r
        except Exception as e:
            print(f"[altyazı] {video_id} {lang} {Path(f).suffix} reddedildi: {str(e)[:150]}")
            err = e
    raise err


def replace_caption(video_id, srt_path, lang, name, top=True):
    """Aynı dildeki elle eklenmiş izi silip yenisini (üstte konumlu) yükler."""
    yt = _yt()
    for c in yt.captions().list(part="snippet", videoId=video_id).execute().get("items", []):
        s = c["snippet"]
        if s.get("language") == lang and s.get("trackKind") != "asr":
            yt.captions().delete(id=c["id"]).execute()
    return upload_caption(video_id, srt_path, lang, name, top=top)


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
# Yerel dosyayı doğrudan Meta'ya yükler (videoyu herkese açık bir sunucuda barındırmak gerekmez).
# İki giriş türü desteklenir:
#  - "Instagram girişi" token'ı (IG... ile başlar) → graph.instagram.com, 60 gün geçerli, her çalışmada yenilenir
#  - "Facebook girişi" Page token'ı (EA... ile başlar) → graph.facebook.com, süresiz
def _ig_base(token):
    return "https://graph.instagram.com" if token.startswith("IG") else "https://graph.facebook.com"


def instagram_check(expected_username=None):
    """Instagram token'ı doğru kanala mı ait? Doğrulanamazsa yayınlama."""
    token, ig_id = os.environ.get("IG_ACCESS_TOKEN", ""), os.environ.get("IG_USER_ID", "")
    if not token or not ig_id:
        raise RuntimeError("Instagram erişim anahtarı veya hesap kimliği eksik")
    # Instagram Login token'ının sahibi 'me' üzerinden doğrulanır; yapılandırmadaki ID tek başına yeterli değildir.
    who = "me" if token.startswith("IG") else ig_id
    r = requests.get(f"{_ig_base(token)}/{who}", params={"fields": "id,username", "access_token": token},
                     timeout=30)
    if not r.ok:
        raise RuntimeError(_ig_err(r))
    actual = (r.json().get("username") or "").strip().lstrip("@").casefold()
    expected = (expected_username or "").strip().lstrip("@").casefold()
    if expected and actual != expected:
        raise RuntimeError(f"YANLIŞ INSTAGRAM HESABI: beklenen @{expected}, bağlı @{actual or 'belirsiz'}")
    if not actual:
        raise RuntimeError("Instagram hesap adı doğrulanamadı; güvenlik nedeniyle yayın engellendi")
    return actual


def instagram_refresh():
    """Instagram girişi token'ının süresini 60 güne uzatır. Kalan gün sayısını döner (Page token'da None)."""
    token = os.environ.get("IG_ACCESS_TOKEN", "")
    if not token.startswith("IG"):
        return None
    r = requests.get("https://graph.instagram.com/refresh_access_token",
                     params={"grant_type": "ig_refresh_token", "access_token": token}, timeout=30)
    if not r.ok:
        raise RuntimeError(f"Instagram token yenileme: {_ig_err(r)}")
    j = r.json()
    if j.get("access_token") and j["access_token"] != token:
        os.environ["IG_ACCESS_TOKEN"] = j["access_token"]  # bu çalışma boyunca yenisini kullan
    return int(j.get("expires_in", 0)) // 86400


def _ig_err(r):
    try:
        e = r.json().get("error", {})
        return f"{r.status_code} {e.get('message', r.text[:300])} (code {e.get('code')})"
    except Exception:
        return f"{r.status_code} {r.text[:300]}"


def instagram_reel(video, caption, ai_label=True, poll_every=20, max_wait=900, dry=False,
                   expected_username=None):
    """Reel yayınlar; beklenen kanal hesabı eşleşmezse yayınlamadan hata verir."""
    if expected_username:
        instagram_check(expected_username)
    token, ig_id = os.environ["IG_ACCESS_TOKEN"], os.environ["IG_USER_ID"]
    IG = _ig_base(token)
    if token.startswith("IG"):
        ig_id = "me"  # Instagram girişi token'ı kendi hesabını "me" ile bulur (ID farklılıklarına karşı güvenli)
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
                              data=fh, timeout=(30, 240))  # takılırsa 4 dk sonra yeniden dener
        if not u.ok or not u.json().get("success", False):
            raise RuntimeError(f"Instagram yükleme: {_ig_err(u)}")
        return cid

    cid = _retry(create_and_upload, tries=6, base=30, what="Instagram yükleme")  # Meta ara ara ProcessingFailedError veriyor; aralıklı tekrar geçiyor

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
    if dry:
        return cid, "(test: yüklendi ve işlendi, yayınlanmadı)"

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


def retention(video_id, published):
    """İzleyici tutma eğrisi: [(videonun oranı 0-1, o noktada izleyen oranı)], süre (sn), görüntülenme.
    Yetki/veri yoksa None (sistem durmaz)."""
    if not os.getenv("YT_REFRESH_TOKEN"):
        return None
    try:
        import re as _re
        from googleapiclient.discovery import build
        ya = build("youtubeAnalytics", "v2", credentials=_creds(), cache_discovery=False)
        r = ya.reports().query(ids="channel==MINE", startDate=str(published)[:10], endDate=date.today().isoformat(),
                               metrics="audienceWatchRatio", dimensions="elapsedVideoTimeRatio",
                               filters=f"video=={video_id}").execute()
        curve = [(float(a), float(b)) for a, b in r.get("rows", [])]
        it = _yt().videos().list(part="contentDetails,statistics,snippet", id=video_id).execute()["items"][0]
        m = _re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", it["contentDetails"]["duration"])
        dur = sum(int(x or 0) * k for x, k in zip(m.groups(), (3600, 60, 1)))
        return {"curve": curve, "duration": dur, "views": int(it["statistics"].get("viewCount", 0)),
                "description": it["snippet"].get("description", "")}
    except Exception as e:
        print(f"İzleyici tutma verisi okunamadı ({video_id}): {str(e)[:200]}")
        return None


def reach_ctr(video_ids):
    """Küçük resim gösterimi ve tıklanma oranı (YouTube Reporting API, channel_reach_basic_a1; 01.2026'dan beri).
    İlk çağrıda rapor işi oluşturulur; YouTube ilk raporları 1-2 gün içinde üretir. Dönüş: {video_id: {'impr','ctr'}}."""
    ids = {v for v in video_ids if v}
    if not ids or not os.getenv("YT_REFRESH_TOKEN"):
        return {}
    try:
        import csv
        import io
        from google.auth.transport.requests import AuthorizedSession
        from googleapiclient.discovery import build
        creds = _creds()
        yr = build("youtubereporting", "v1", credentials=creds, cache_discovery=False)
        jobs = yr.jobs().list().execute().get("jobs", [])
        job = next((j for j in jobs if j.get("reportTypeId") == "channel_reach_basic_a1"), None)
        if not job:
            job = yr.jobs().create(body={"reportTypeId": "channel_reach_basic_a1", "name": "otoyt-reach"}).execute()
            print("Tıklanma oranı rapor işi oluşturuldu; ilk veriler 1-2 gün içinde gelir")
            return {}
        reports = yr.jobs().reports().list(jobId=job["id"]).execute().get("reports", [])
        sess, agg = AuthorizedSession(creds), {}
        for rep in reports[:45]:  # son ~45 günlük rapor
            r = sess.get(rep["downloadUrl"], timeout=60)
            if not r.ok:
                continue
            for row in csv.DictReader(io.StringIO(r.text)):
                v = row.get("video_id")
                if v in ids:
                    imp = float(row.get("video_thumbnail_impressions") or 0)
                    ctr = float(row.get("video_thumbnail_impressions_ctr") or 0)
                    a = agg.setdefault(v, [0.0, 0.0])
                    a[0] += imp
                    a[1] += imp * ctr
        return {v: {"impr": int(a[0]), "ctr": (a[1] / a[0]) if a[0] else 0.0} for v, a in agg.items()}
    except Exception as e:
        print(f"Tıklanma oranı okunamadı: {str(e)[:200]}")
        return {}


def score(stat):
    """İzlenme (log) × izleyici tutma. Tutma, algoritmanın en çok önemsediği sinyal."""
    return math.log1p(stat["views"]) * (0.5 + stat["avg_pct"] / 100)


def hist_dt(s):
    """Kayıt tarihini saat dilimsiz UTC'ye çevirir (eski kayıtlar dilimsiz, bazıları '+00:00' ile yazılmış)."""
    from datetime import timezone as _tz
    d = datetime.fromisoformat(str(s))
    return d.astimezone(_tz.utc).replace(tzinfo=None) if d.tzinfo else d


def performance_report(history, min_age_days=4):
    """Konu sütunlarının ağırlıkları + en iyi/kötü başlıklar (fikir üreticiye geri beslenir)."""
    cutoff = datetime.now() - timedelta(days=min_age_days)
    old = [h for h in history if h.get("type", "long") == "long" and h.get("video_id")
           and hist_dt(h["date"]) < cutoff]
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


def my_channel():
    """İzin anahtarının bağlı olduğu kanal: (ad, kimlik)."""
    items = _yt().channels().list(part="snippet", mine=True).execute().get("items", [])
    return (items[0]["snippet"]["title"], items[0]["id"]) if items else (None, None)


def video_privacy(video_id):
    items = _yt().videos().list(part="status", id=video_id).execute().get("items", [])
    return items[0]["status"]["privacyStatus"] if items else None


def post_comment(video_id, text):
    """Kanal adına üst düzey yorum yazar (YouTube API yorumu sabitlemeye izin vermez; sabitleme Studio'dan)."""
    yt = _yt()
    try:  # aynı yorum zaten yazılmışsa (kayıt kaybolduysa) tekrar yazma
        r = yt.commentThreads().list(part="snippet", videoId=video_id, maxResults=50, order="time",
                                     textFormat="plainText").execute()
        for t in r.get("items", []):
            if t["snippet"]["topLevelComment"]["snippet"].get("textOriginal", "").strip() == text.strip()[:9000]:
                return t["id"]
    except Exception:
        pass
    body = {"snippet": {"videoId": video_id, "topLevelComment": {"snippet": {"textOriginal": text[:9000]}}}}
    return yt.commentThreads().insert(part="snippet", body=body).execute()["id"]


def viewer_requests(max_items=40):
    """Kanalın videolarına gelen son yorumlar (en çok beğenilenler önde) → izleyici konu istekleri için."""
    yt = _yt()
    cid = yt.channels().list(part="id", mine=True).execute()["items"][0]["id"]
    r = yt.commentThreads().list(part="snippet", allThreadsRelatedToChannelId=cid, maxResults=100,
                                 order="time", textFormat="plainText").execute()
    out = []
    for it in r.get("items", []):
        c = it["snippet"]["topLevelComment"]["snippet"]
        if c.get("authorChannelId", {}).get("value") == cid:
            continue  # kendi yorumlarımız
        out.append((c.get("likeCount", 0), c.get("textOriginal", "")[:300]))
    out.sort(key=lambda x: -x[0])
    return [t for _, t in out[:max_items]]


def ensure_playlist(title, description, lang, cache):
    """Konu sütunu için oynatma listesi (bir kez oluşturulur, kimliği cache'te saklanır)."""
    if title in cache:
        return cache[title]
    try:  # önbellek kaybolduysa kanaldaki aynı adlı listeyi kullan (çift liste açma)
        tok = None
        while True:
            r = _yt().playlists().list(part="snippet", mine=True, maxResults=50, pageToken=tok).execute()
            for pl in r.get("items", []):
                if pl["snippet"]["title"] == title[:150]:
                    cache[title] = pl["id"]
                    return pl["id"]
            tok = r.get("nextPageToken")
            if not tok:
                break
    except Exception:
        pass
    body = {"snippet": {"title": title[:150], "description": description[:4900], "defaultLanguage": lang},
            "status": {"privacyStatus": "public"}}
    pid = _yt().playlists().insert(part="snippet,status", body=body).execute()["id"]
    cache[title] = pid
    return pid


def add_to_playlist(playlist_id, video_id):
    try:  # zaten listedeyse tekrar ekleme
        if _yt().playlistItems().list(part="id", playlistId=playlist_id, videoId=video_id).execute().get("items"):
            return
    except Exception:
        pass
    body = {"snippet": {"playlistId": playlist_id, "resourceId": {"kind": "youtube#video", "videoId": video_id}}}
    _yt().playlistItems().insert(part="snippet", body=body).execute()


def set_thumbnail(video_id, path):
    from googleapiclient.http import MediaFileUpload
    _yt().thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(path))).execute()


def recent_uploads(n=50):
    """Kanalın son yüklenen videoları: [{id, title, privacy, publishAt}] (elle yüklenenleri bulmak için)."""
    yt = _yt()
    up = yt.channels().list(part="contentDetails", mine=True).execute()["items"][0][
        "contentDetails"]["relatedPlaylists"]["uploads"]
    r = yt.playlistItems().list(part="contentDetails", playlistId=up, maxResults=n).execute()
    ids = [i["contentDetails"]["videoId"] for i in r.get("items", [])]
    if not ids:
        return []
    out = []
    for v in yt.videos().list(part="snippet,status", id=",".join(ids)).execute().get("items", []):
        out.append({"id": v["id"], "title": v["snippet"]["title"], "snippet": v["snippet"], "status": v["status"]})
    return out


def finish_video(video, meta, thumb=None):
    """Elle yüklenmiş videoya başlık/açıklama/etiket/kategori/dil, yapay içerik beyanı, (gizliyse) yayın zamanı
    ve küçük resim uygular. Gizlilik durumunu herkese açığa ÇEVİRMEZ."""
    from googleapiclient.http import MediaFileUpload
    yt = _yt()
    sn = {"title": meta["title"][:100], "description": meta["description"][:4900], "tags": meta["tags"],
          "categoryId": meta["category_id"], "defaultLanguage": meta["lang"], "defaultAudioLanguage": meta["lang"]}
    st = dict(video["status"])
    status = {"privacyStatus": st.get("privacyStatus", "private"),
              "selfDeclaredMadeForKids": meta.get("made_for_kids", False),
              "containsSyntheticMedia": meta.get("synthetic", True),
              "embeddable": st.get("embeddable", True), "license": st.get("license", "youtube"),
              "publicStatsViewable": st.get("publicStatsViewable", True)}
    if st.get("publishAt"):
        status["publishAt"] = st["publishAt"]
    elif status["privacyStatus"] == "private" and meta.get("publish_at"):
        status["publishAt"] = meta["publish_at"]  # sahibi "Özel" yüklediyse planlanan saatte yayına girer
    notes = []
    try:
        yt.videos().update(part="snippet,status", body={"id": video["id"], "snippet": sn, "status": status}).execute()
    except Exception as e:  # zamanlama/durum reddedilirse en azından bilgileri yaz
        notes.append("yayın saati/durumu ayarlanamadı")
        yt.videos().update(part="snippet", body={"id": video["id"], "snippet": sn}).execute()
    if thumb:
        try:
            yt.thumbnails().set(videoId=video["id"], media_body=MediaFileUpload(str(thumb))).execute()
        except Exception as e:
            notes.append("küçük resim konamadı")
    return notes


def set_playlist_image(playlist_id, path, wide_path=None):
    """Oynatma listesine özel kapak (playlistImages API, type=hero). Önce kare, olmazsa 16:9 dener."""
    from googleapiclient.http import MediaFileUpload
    yt = _yt()
    existing = []
    try:
        existing = yt.playlistImages().list(part="id,snippet", parent=playlist_id).execute().get("items", [])
    except Exception:
        pass
    errs = []
    for p, wh in ((path, None), (path, (1280, 1280)), (wide_path, (1280, 720)), (wide_path, None)):
        if not p:
            continue
        body = {"snippet": {"playlistId": playlist_id, "type": "hero"}}
        if wh:
            body["snippet"].update({"width": wh[0], "height": wh[1]})
        try:
            media = MediaFileUpload(str(p), mimetype="image/jpeg")
            if existing:
                body["id"] = existing[0]["id"]
                return yt.playlistImages().update(part="snippet", body=body, media_body=media).execute()
            return yt.playlistImages().insert(part="snippet", body=body, media_body=media).execute()
        except Exception as e:
            if "quota" in str(e).lower():
                raise
            errs.append(str(getattr(e, "content", b"") or e)[:300])
    raise RuntimeError(" | ".join(errs)[:900])


def ig_link_cta(caption, tr=True):
    """Reel açıklamasında YouTube'a yönlendiren cümleyi tek ve sabit bir çağrıya çevirir:
    YouTube geçen cümleler çıkarılır, hashtag satırının hemen üstüne "link profilde" satırı konur."""
    import re as _re
    cta = "Belgeselin tamamı YouTube'da, link profilde 👆" if tr else "Full video on YouTube, link in bio 👆"
    lines = caption.rstrip().split("\n")
    tag_i = len(lines)
    while tag_i > 0 and (not lines[tag_i - 1].strip() or lines[tag_i - 1].strip().startswith("#")):
        tag_i -= 1
    body, tags = lines[:tag_i], [l for l in lines[tag_i:] if l.strip()]
    out = []
    for l in body:
        parts = _re.split(r"(?<=[.!?])\s+", l)
        kept = [p for p in parts if not _re.search(r"youtube|\blink\b", p, _re.I)]
        if parts and not kept and l.strip():
            continue  # satırın tamamı yönlendirmeydi
        out.append(" ".join(kept))
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out + [cta, ""] + tags).strip()


def competition(query, region="US", lang="en", n=10):
    """YouTube'da bu arama için ilk n uzun video: [{title, views, channel, age_days}] (rakip analizi, ~101 birim)."""
    yt = _yt()
    r = yt.search().list(part="snippet", q=query, type="video", maxResults=n, regionCode=region,
                         relevanceLanguage=lang, videoDuration="medium").execute()
    ids = [i["id"]["videoId"] for i in r.get("items", [])]
    if not ids:
        return []
    st = yt.videos().list(part="statistics,snippet", id=",".join(ids)).execute().get("items", [])
    out = []
    for v in st:
        pub = datetime.fromisoformat(v["snippet"]["publishedAt"].replace("Z", "+00:00")).replace(tzinfo=None)
        out.append({"title": v["snippet"]["title"], "channel": v["snippet"]["channelTitle"],
                    "views": int(v["statistics"].get("viewCount", 0)), "age_days": (datetime.utcnow() - pub).days})
    return out
