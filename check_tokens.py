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


# Gölgedefter Türkçe ses (Gemini TTS) ücretsiz katman testi
key = os.getenv("GEMINI_PAID_API_KEY")
for model in ("gemini-3.1-flash-tts-preview", "gemini-2.5-flash-preview-tts", "gemini-2.5-pro-preview-tts"):
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
        ids = ["5mnPjthOTDY"] + [i["contentDetails"]["videoId"] for i in yt.playlistItems().list(
            part="contentDetails", playlistId=up, maxResults=5).execute().get("items", [])]
        for v in yt.videos().list(part="snippet,status,processingDetails", id=",".join(ids)).execute()["items"]:
            caps = yt.captions().list(part="snippet", videoId=v["id"]).execute().get("items", [])
            tr = "; ".join(f"{x['snippet']['language']}/{x['snippet'].get('trackKind')}/"
                           f"{x['snippet'].get('status')}/draft={x['snippet'].get('isDraft')}" for x in caps)
            for x in caps:
                if x["snippet"].get("trackKind") != "asr" and v["id"] == ids[0]:
                    try:
                        raw = yt.captions().download(id=x["id"], tfmt="srv3").execute()
                        raw = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else raw
                        top = "ÜST" if ('ap="1"' in raw) else "alt/varsayılan"
                        print(f"::notice title=İz {x['snippet']['language']}::konum={top} | " + raw[:160].replace(chr(10), " "))
                    except Exception as e:
                        print(f"::warning title=İz {x['snippet']['language']}::{str(e)[:150]}")
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
