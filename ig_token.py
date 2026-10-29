"""
TEK SEFERLİK: Instagram yayın anahtarı (süresiz Page token) + Instagram hesap ID'si.
Windows'ta ig_token.bat'a çift tıkla.

Önce gerekenler:
 1) Instagram hesabı "Profesyonel" (İşletme veya İçerik Üreticisi) olmalı
    ve bir Facebook Sayfasına bağlı olmalı.
 2) developers.facebook.com > Uygulama oluştur (Business). Ayarlar > Temel'den
    APP_ID ve APP_SECRET'i al.
 3) Araçlar > Graph API Explorer: uygulamanı seç, "User Token" al, şu izinleri işaretle:
    instagram_basic, instagram_content_publish, pages_show_list,
    pages_read_engagement, business_management  → "Generate Access Token"
"""
import requests

G = "https://graph.facebook.com"


def get(url, **params):
    r = requests.get(url, params=params, timeout=30)
    j = r.json()
    if "error" in j:
        raise SystemExit(f"HATA: {j['error'].get('message')}")
    return j


app_id = input("APP_ID: ").strip()
secret = input("APP_SECRET: ").strip()
short = input("Graph API Explorer'dan aldığın token: ").strip()

long_user = get(f"{G}/oauth/access_token", grant_type="fb_exchange_token", client_id=app_id,
                 client_secret=secret, fb_exchange_token=short)["access_token"]
pages = get(f"{G}/me/accounts", access_token=long_user,
            fields="name,access_token,instagram_business_account{id,username}")["data"]
linked = [p for p in pages if p.get("instagram_business_account")]
if not linked:
    raise SystemExit("HATA: Instagram'a bağlı bir Facebook Sayfası bulunamadı. "
                     "Instagram > Ayarlar > Hesap Merkezi'nden Sayfayı bağla.")
p = linked[0]
if len(linked) > 1:
    for i, x in enumerate(linked):
        print(f"{i+1}) {x['name']}  →  @{x['instagram_business_account']['username']}")
    p = linked[int(input("Hangisi? ") or 1) - 1]

info = get(f"{G}/debug_token", input_token=p["access_token"], access_token=f"{app_id}|{secret}")["data"]
exp = "SÜRESİZ ✅" if not info.get("expires_at") else f"bitiş (unix): {info['expires_at']}"
text = (f"IG_USER_ID={p['instagram_business_account']['id']}\n"
        f"IG_ACCESS_TOKEN={p['access_token']}\n")
open("instagram_anahtarlar.txt", "w").write(text)
print(f"\nHesap: @{p['instagram_business_account']['username']}  |  Token: {exp}\n")
print(text)
print("instagram_anahtarlar.txt dosyasına kaydedildi. GitHub Secrets'a ekledikten sonra SİL.")
