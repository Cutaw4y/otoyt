import os
import requests

# Vertex AI (Google Cloud kredisi) denemesi
sa = os.getenv("GCP_SA_KEY")
if sa:
    try:
        import json as _j
        from google.oauth2 import service_account
        from google.auth.transport.requests import Request
        info = _j.loads(sa)
        cr = service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/cloud-platform"])
        cr.refresh(Request())
        H = {"Authorization": f"Bearer {cr.token}", "Content-Type": "application/json"}
        proj = info["project_id"]
        print(f"::notice title=Vertex::anahtar geçerli, proje {proj}")
        r = requests.get("https://aiplatform.googleapis.com/v1beta1/publishers/google/models?pageSize=200",
                         headers=H, timeout=60)
        names = [m["name"].split("/")[-1] for m in r.json().get("publisherModels", [])] if r.ok else []
        gem = sorted(n for n in names if "gemini" in n)
        print(f"::notice title=Vertex modeller::HTTP {r.status_code} {', '.join(gem)[:900] or r.text[:200]}")
        for m in ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite"] + [g for g in gem if g.startswith("gemini-3")][:4]:
            u = f"https://aiplatform.googleapis.com/v1/projects/{proj}/locations/global/publishers/google/models/{m}:generateContent"
            rr = requests.post(u, headers=H, timeout=120, json={"contents": [{"role": "user", "parts": [{"text": "Türkiye'nin başkenti? Tek kelime."}]}]})
            print(f"::notice title=Vertex {m}::HTTP {rr.status_code} {rr.text[:140].replace(chr(10), ' ')}")
        for tm in ("gemini-2.5-flash-tts", "gemini-2.5-pro-tts"):
            rr = requests.post("https://texttospeech.googleapis.com/v1/text:synthesize", headers={**H, "x-goog-user-project": proj},
                               timeout=120, json={"input": {"text": "Anlatılana göre o gece rüzgâr hiç durmadı.",
                                                            "prompt": "Gizemli bir belgesel anlatıcısı gibi oku"},
                                                  "voice": {"languageCode": "tr-TR", "name": "Charon", "modelName": tm},
                                                  "audioConfig": {"audioEncoding": "LINEAR16", "sampleRateHertz": 24000}})
            ok = rr.ok and len(rr.json().get("audioContent", "")) > 1000
            print(f"::notice title=Vertex TTS {tm}::HTTP {rr.status_code} {'ses geldi' if ok else rr.text[:200]}")
    except Exception as e:
        print(f"::error title=Vertex::{str(e)[:250]}")
