"""
TEK SEFERLİK: YouTube izin anahtarı (refresh token) almak için.
Windows'ta get_token.bat dosyasına çift tıklaman yeterli.
Bu klasörde client_secret.json (Google Cloud'dan indirilen) bulunmalı.
"""
import json
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl",   # altyazı yükleme
          "https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube",
          "https://www.googleapis.com/auth/yt-analytics.readonly"]

flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
info = json.load(open("client_secret.json"))["installed"]
text = (f"YT_CLIENT_ID={info['client_id']}\n"
        f"YT_CLIENT_SECRET={info['client_secret']}\n"
        f"YT_REFRESH_TOKEN={creds.refresh_token}\n")
open("youtube_anahtarlar.txt", "w").write(text)
print("\n" + text)
print("Bu 3 değer youtube_anahtarlar.txt dosyasına da kaydedildi.")
print("GitHub Secrets'a ekledikten sonra bu dosyayı SİL.")
