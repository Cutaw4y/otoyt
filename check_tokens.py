"""İki kanalın YouTube izinlerini ve Gemini anahtarlarını dener; sonuçları GitHub uyarısı olarak yazar.
Hiçbir şey yüklemez, gizli değer yazdırmaz."""
import os
import requests


def channel(refresh, cid, secret):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    c = Credentials(None, refresh_token=refresh, client_id=cid, client_secret=secret,
                    token_uri="https://oauth2.googleapis.com/token")
    it = build("youtube", "v3", credentials=c, cache_discovery=False).channels().list(
        part="snippet", mine=True).execute().get("items", [])
    return it[0]["snippet"]["title"] if it else "(kanal yok)"


def gemini(key):
    r = requests.post("https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-lite-latest:generateContent",
                      headers={"x-goog-api-key": key}, json={"contents": [{"parts": [{"text": "Say OK"}]}]}, timeout=60)
    return f"HTTP {r.status_code}"


cid, sec = os.getenv("YT_CLIENT_ID"), os.getenv("YT_CLIENT_SECRET")
for label, tok in (("Cutaway izni (YT_REFRESH_TOKEN)", os.getenv("YT_REFRESH_TOKEN")),
                   ("Golgedefter izni (YT2_REFRESH_TOKEN)", os.getenv("YT2_REFRESH_TOKEN"))):
    try:
        print(f"::notice title={label}::kanal = {channel(tok, cid, sec)}" if tok else f"::warning title={label}::yok")
    except Exception as e:
        print(f"::error title={label}::{str(e)[:200]}")
for label, key in (("Gemini GEMINI_API_KEY", os.getenv("GEMINI_API_KEY")),
                   ("Gemini GEMINI_PAID_API_KEY", os.getenv("GEMINI_PAID_API_KEY"))):
    try:
        print(f"::notice title={label}::{gemini(key)}" if key else f"::warning title={label}::yok")
    except Exception as e:
        print(f"::error title={label}::{str(e)[:200]}")


# Ses testi yalnızca istenirse (TTS_TEST=1): ücretsiz günlük ses hakkını boşa harcamasın
key = os.getenv("GEMINI_PAID_API_KEY")
for model in (("gemini-3.1-flash-tts-preview", "gemini-2.5-flash-preview-tts") if os.getenv("TTS_TEST") else ()):
    try:
        r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                          headers={"x-goog-api-key": key}, timeout=120, json={
            "contents": [{"parts": [{"text": "Gizemli bir sesle oku: Anlatılana göre o gece rüzgâr hiç durmadı."}]}],
            "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {"voiceConfig": {
                "prebuiltVoiceConfig": {"voiceName": "Charon"}}}}})
        msg = r.text[:300].replace("\n", " ") if r.status_code != 200 else f"ses {len(r.text)//1000} KB"
        print(f"::notice title=TTS {model}::HTTP {r.status_code} {msg}")
    except Exception as e:
        print(f"::error title=TTS {model}::{str(e)[:200]}")


# Son videoların altyazı izleri ve durumları (Cutaway)
if os.getenv("YT_REFRESH_TOKEN"):
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        c = Credentials(None, refresh_token=os.getenv("YT_REFRESH_TOKEN"), client_id=cid, client_secret=sec,
                        token_uri="https://oauth2.googleapis.com/token")
        yt = build("youtube", "v3", credentials=c, cache_discovery=False)
        up = yt.channels().list(part="contentDetails", mine=True).execute()["items"][0][
            "contentDetails"]["relatedPlaylists"]["uploads"]
        ids = [i["contentDetails"]["videoId"] for i in yt.playlistItems().list(
            part="contentDetails", playlistId=up, maxResults=5).execute().get("items", [])]
        for v in yt.videos().list(part="snippet,status,processingDetails", id=",".join(ids)).execute()["items"]:
            caps = yt.captions().list(part="snippet", videoId=v["id"]).execute().get("items", [])
            tr = "; ".join(f"{x['snippet']['language']}/{x['snippet'].get('trackKind')}/"
                           f"{x['snippet'].get('status')}/draft={x['snippet'].get('isDraft')}" for x in caps)
            print(f"::notice title=Video {v['id']}::{v['snippet']['title'][:50]} | "
                  f"{v['status']['privacyStatus']} {v['status'].get('publishAt', '')} | "
                  f"işleme={v.get('processingDetails', {}).get('processingStatus')} | altyazı: {tr or 'YOK'}")
    except Exception as e:
        print(f"::error title=Video kontrol::{str(e)[:200]}")


# Google Drive (telefondan yükleme paketleri)
import drive
if drive.enabled():
    try:
        d = drive._drive()
        d.files().list(pageSize=1, fields="files(id)").execute()
        print("::notice title=Google Drive::bağlantı tamam (paketler 'OtoYT YÜKLEME' klasörüne gidecek)")
    except Exception as e:
        print(f"::error title=Google Drive::{str(e)[:200]}")
else:
    print("::warning title=Google Drive::GDRIVE_* anahtarları yok")


# Gölgedefter Instagram bağlantısı (hiçbir şey paylaşmaz, sadece hesabı okur)
tok, iid = os.getenv("GD_IG_ACCESS_TOKEN"), os.getenv("GD_IG_USER_ID")
if not (tok and iid):
    print("::warning title=Instagram (golge.defter)::anahtar yok")
else:
    try:
        base = "https://graph.instagram.com" if tok.startswith("IG") else "https://graph.facebook.com"
        r = requests.get(f"{base}/{'me' if tok.startswith('IG') else iid}",
                         params={"fields": "username"}, headers={"Authorization": f"OAuth {tok}"}, timeout=30)
        j = r.json()
        if r.ok:
            print(f"::notice title=Instagram (golge.defter)::bağlı hesap = @{j.get('username')}")
            lim = requests.get(f"{base}/{'me' if tok.startswith('IG') else iid}/content_publishing_limit",
                               params={"fields": "quota_usage,config"},
                               headers={"Authorization": f"OAuth {tok}"}, timeout=30)
            if lim.ok:
                d = (lim.json().get("data") or [{}])[0]
                print(f"::notice title=Instagram yayın izni::VAR (24 saatte {d.get('quota_usage', 0)}/"
                      f"{(d.get('config') or {}).get('quota_total', 100)} paylaşım kullanıldı)")
            else:
                print(f"::error title=Instagram yayın izni::{lim.json().get('error', {}).get('message', lim.text)[:200]}")
        else:
            print(f"::error title=Instagram (golge.defter)::{j.get('error', {}).get('message', r.text)[:200]}")
    except Exception as e:
        print(f"::error title=Instagram (golge.defter)::{str(e)[:200]}")


# Paket teslimi (GitHub Releases) deneme: küçük dosyalı release aç, yükle, sil
import release
if release.enabled():
    try:
        import tempfile, pathlib
        t = pathlib.Path(tempfile.mkdtemp()) / "deneme.txt"
        t.write_text("otoyt release testi")
        rid, url = release.create("paket-test-kontrol", "TEST (silinecek)", "test", [t])
        print(f"::notice title=Paket teslimi::release açıldı ve dosya yüklendi; siliniyor → {release.remove(rid, 'paket-test-kontrol')}")
    except Exception as e:
        print(f"::error title=Paket teslimi::{str(e)[:200]}")
