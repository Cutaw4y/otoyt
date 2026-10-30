"""
TEK SEFERLİK: Google Drive izni (paketleri telefondan yükleyebilmen için).
get_drive_token.bat dosyasına çift tıkla. Bu klasörde client_secret.json bulunmalı.
Sadece 'drive.file' izni istenir: sistem Drive'ında YALNIZCA kendi koyduğu dosyaları görebilir.
"""
import json
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
info = json.load(open("client_secret.json"))["installed"]
text = (f"GDRIVE_CLIENT_ID={info['client_id']}\n"
        f"GDRIVE_CLIENT_SECRET={info['client_secret']}\n"
        f"GDRIVE_REFRESH_TOKEN={creds.refresh_token}\n")
open("drive_anahtarlar.txt", "w").write(text)
print("\nTAMAM. 3 değer drive_anahtarlar.txt dosyasına yazıldı.")
print("GitHub Secrets'a ekledikten sonra bu dosyayı ve client_secret.json'u SİL.")
