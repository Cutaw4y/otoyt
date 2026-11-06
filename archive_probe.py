"""Arşiv video kaynaklarının GitHub üzerinde erişilebilirlik testi (yalnız arama + küçük indirme; yayın yok)."""
import requests
H = {"User-Agent": "OtoYT/1.0 (educational; https://cutaw4y.github.io)"}
for q in ("Ottoman Istanbul", "sleep laboratory", "Balkan village 1930s"):
    r = requests.get("https://commons.wikimedia.org/w/api.php", headers=H, timeout=30, params={
        "action": "query", "format": "json", "generator": "search", "gsrsearch": f"{q} filetype:video",
        "gsrnamespace": 6, "gsrlimit": 5, "prop": "imageinfo", "iiprop": "url|size|mime"})
    pages = list((r.json().get("query") or {}).get("pages", {}).values())
    print(f"::notice title=Commons {q}::{len(pages)} sonuç: " + " | ".join(p['title'][:50] for p in pages[:3]))
    r = requests.get("https://archive.org/advancedsearch.php", timeout=30, params={
        "q": f"({q}) AND mediatype:(movies) AND (collection:(prelinger) OR collection:(publicdomainmovies) OR licenseurl:*publicdomain*)",
        "fl[]": ["identifier", "title"], "rows": 5, "output": "json"})
    docs = r.json().get("response", {}).get("docs", [])
    print(f"::notice title=Archive {q}::{len(docs)} sonuç: " + " | ".join(str(d.get('title'))[:50] for d in docs[:3]))
