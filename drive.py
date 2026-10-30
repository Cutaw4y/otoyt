"""Elle yükleme paketlerini sahibinin Google Drive'ına koyar (telefondan yükleyebilsin diye).
Yalnızca drive.file izni: uygulama sadece kendi oluşturduğu dosyaları görür/siler.
Gerekli GitHub Secrets: GDRIVE_CLIENT_ID, GDRIVE_CLIENT_SECRET, GDRIVE_REFRESH_TOKEN (get_drive_token.bat ile alınır)."""
import os

FOLDER = "application/vnd.google-apps.folder"


def enabled():
    return all(os.getenv(k) for k in ("GDRIVE_CLIENT_ID", "GDRIVE_CLIENT_SECRET", "GDRIVE_REFRESH_TOKEN"))


def _drive():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials(None, refresh_token=os.getenv("GDRIVE_REFRESH_TOKEN"),
                        client_id=os.getenv("GDRIVE_CLIENT_ID"), client_secret=os.getenv("GDRIVE_CLIENT_SECRET"),
                        token_uri="https://oauth2.googleapis.com/token",
                        scopes=["https://www.googleapis.com/auth/drive.file"])
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _folder(d, name, parent=None):
    q = f"name = '{name.replace(chr(39), ' ')}' and mimeType = '{FOLDER}' and trashed = false"
    if parent:
        q += f" and '{parent}' in parents"
    r = d.files().list(q=q, fields="files(id)", spaces="drive").execute().get("files", [])
    if r:
        return r[0]["id"]
    body = {"name": name, "mimeType": FOLDER}
    if parent:
        body["parents"] = [parent]
    return d.files().create(body=body, fields="id").execute()["id"]


def upload_package(files, channel, name):
    """files: [Path]. Drive'da 'OtoYT YÜKLEME / <kanal> / <name>' klasörüne yükler. (klasör_id, bağlantı) döner."""
    from googleapiclient.http import MediaFileUpload
    d = _drive()
    root = _folder(d, "OtoYT YÜKLEME")
    ch = _folder(d, channel, root)
    fid = _folder(d, name, ch)
    for f in files:
        mime = "video/mp4" if f.suffix == ".mp4" else "text/plain"
        media = MediaFileUpload(str(f), mimetype=mime, resumable=True, chunksize=16 * 1024 * 1024)
        req = d.files().create(body={"name": f.name, "parents": [fid]}, media_body=media, fields="id")
        resp = None
        while resp is None:
            _, resp = req.next_chunk()
    return fid, f"https://drive.google.com/drive/folders/{fid}"


def trash(folder_id):
    """Tamamlanan paketin klasörünü Drive çöp kutusuna taşır (30 gün sonra Drive kendisi boşaltır)."""
    try:
        _drive().files().update(fileId=folder_id, body={"trashed": True}).execute()
        return True
    except Exception:
        return False
