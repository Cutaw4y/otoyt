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


# Shorts yedekleri, herkese açık GitHub Releases yerine varsayılan olarak
# paylaşılmayan uygulama klasöründe tutulur. OAuth drive.file erişimiyle sınırlıdır.
def _shorts_folder(d, channel):
    root = _folder(d, "OtoYT SHORTS KURTARMA")
    return _folder(d, channel, root)


def list_short_backups(channel):
    """Kanalın özel Drive klasöründeki yedek video/meta kayıtlarını döndür."""
    d = _drive()
    fid = _shorts_folder(d, channel)
    out, token = [], None
    while True:
        res = d.files().list(
            q=f"'{fid}' in parents and trashed = false and mimeType = 'video/mp4'",
            fields="nextPageToken,files(id,name,description,appProperties,size,md5Checksum,createdTime)",
            pageSize=100, pageToken=token, spaces="drive").execute()
        out.extend(res.get("files", []))
        token = res.get("nextPageToken")
        if not token:
            break
    return out


def stage_short_backup(path, channel, marker, metadata):
    """Yüklemeden ÖNCE, dosyayı özel Drive klasörüne idempotent biçimde kopyala."""
    import json
    from googleapiclient.http import MediaFileUpload
    data = json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))
    if len(data.encode("utf-8")) > 15000:
        raise ValueError("Shorts kurtarma metaverisi fazla büyük")
    d = _drive()
    fid = _shorts_folder(d, channel)
    safe_name = marker.replace(":", "_") + ".mp4"
    # Bir önceki isteğin cevabı kaybolsa bile aynı dosyadan ikinci kopya oluşturma.
    found = d.files().list(
        q=f"name = '{safe_name}' and '{fid}' in parents and trashed = false",
        fields="files(id,name,description,appProperties,size)", spaces="drive").execute().get("files", [])
    if found:
        previous = json.loads(found[0].get("description") or "{}")
        # Önceki Google Drive yanıtı kaybolsa bile aynı videoya ikinci yedek oluşturma;
        # farklı ikili dosyayı aynı marker altında yeniden kullanmak da yasak.
        keys = ("marker", "parent", "index", "sha256")
        if any(previous.get(key) != metadata.get(key) for key in keys):
            raise RuntimeError("Kurtarma anahtarı mevcut ama dosya kimliği eşleşmiyor")
        return found[0]["id"]
    body = {"name": safe_name, "parents": [fid], "description": data,
            "appProperties": {"otoyt_marker": marker, "otoyt_state": "pending"}}
    media = MediaFileUpload(str(path), mimetype="video/mp4", resumable=True, chunksize=8 * 1024 * 1024)
    req = d.files().create(body=body, media_body=media, fields="id,size")
    response = None
    while response is None:
        _, response = req.next_chunk(num_retries=3)
    return response["id"]


def short_backup_status(file_id, state, youtube_id=None):
    """API sonucu belirsizse durumu 'uncertain' olarak sakla; kendiliğinden çift yükleme yapma."""
    props = {"otoyt_state": state}
    if youtube_id:
        props["youtube_id"] = youtube_id
    return _drive().files().update(fileId=file_id, body={"appProperties": props},
                                   fields="id,appProperties").execute()


def download_short_backup(file_id, dest):
    """Dosyayı yeniden yükleme için özel Drive'dan indir ve MD5 bütünlüğünü kontrol et."""
    import hashlib
    import io
    from googleapiclient.http import MediaIoBaseDownload
    from pathlib import Path
    d = _drive()
    info = d.files().get(fileId=file_id, fields="id,size,md5Checksum").execute()
    dest = Path(dest)
    temp = dest.with_suffix(dest.suffix + ".part")
    try:
        with temp.open("wb") as f:
            req = d.files().get_media(fileId=file_id)
            down = MediaIoBaseDownload(f, req, chunksize=8 * 1024 * 1024)
            done = False
            while not done:
                _, done = down.next_chunk(num_retries=3)
        if int(info.get("size", "-1")) != temp.stat().st_size:
            raise RuntimeError("Drive Shorts yedeği eksik indirildi")
        md5 = hashlib.md5()
        with temp.open("rb") as f:
            for chunk in iter(lambda: f.read(4 * 1024 * 1024), b""):
                md5.update(chunk)
        if info.get("md5Checksum") and info["md5Checksum"] != md5.hexdigest():
            raise RuntimeError("Drive Shorts yedeği checksum uyuşmazlığı")
        temp.replace(dest)
        return dest
    finally:
        temp.unlink(missing_ok=True)
