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
