"""
Ücretsiz yapay zekâ katmanı
- Metin / JSON / görsel değerlendirme / web aramalı doğrulama: Google Gemini (ücretsiz katman)
- Görsel üretimi: Cloudflare Workers AI, FLUX.1 schnell (günde ~170 görsel ücretsiz)
Anahtarlar: GEMINI_API_KEY, CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_API_TOKEN
"""
import base64
import json
import os
import re
import time
from pathlib import Path

import requests

GEMINI = "https://generativelanguage.googleapis.com/v1beta/models"
CF = "https://api.cloudflare.com/client/v4/accounts"


LOG = print  # pipeline kendi kayıt fonksiyonunu buraya bağlar


class QuotaError(RuntimeError):
    pass


def extract_json(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise ValueError(f"JSON bulunamadı: {text[:200]}")
        return json.loads(m.group(0))


def _retry_delay(resp, attempt):
    try:
        for d in resp.json().get("error", {}).get("details", []):
            if "retryDelay" in d:
                return min(float(str(d["retryDelay"]).rstrip("s")) + 2, 90)
    except Exception:
        pass
    return min(15 * (2 ** attempt), 90)


_exhausted = set()
_last_call = {}
# Ücretsiz katman dakika sınırları (Flash 5/dk, Flash-Lite 10/dk) → istekler arasına pay bırak
MIN_GAP = {"lite": 7.0, "default": 13.0}


def _pace(model):
    gap = MIN_GAP["lite"] if "lite" in model else MIN_GAP["default"]
    wait = _last_call.get(model, 0) + gap - time.time()
    if wait > 0:
        time.sleep(wait)
    _last_call[model] = time.time()


def gemini(prompt, system=None, models=("gemini-flash-latest",), images=None, json_mode=False,
           search=False, temperature=0.8, tries=6):
    """Gemini çağrısı; 429/5xx'te bekler, model kalıcı hata verirse listedeki sonrakine geçer."""
    key = os.environ["GEMINI_API_KEY"]
    parts = [{"text": prompt}]
    for img in images or []:
        parts.append({"inline_data": {"mime_type": "image/jpeg",
                                      "data": base64.b64encode(Path(img).read_bytes()).decode()}})
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": temperature}}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if json_mode:
        body["generationConfig"]["responseMimeType"] = "application/json"
    if search:
        body["tools"] = [{"google_search": {}}]

    last = None
    for model in models:
        qkey = (model, bool(search))  # aramalı istekler ayrı kota → ayrı takip
        if qkey in _exhausted:
            continue
        for attempt in range(tries):
            _pace(model)
            try:
                r = requests.post(f"{GEMINI}/{model}:generateContent",
                                  headers={"x-goog-api-key": key}, json=body, timeout=300)
            except requests.RequestException as e:
                last = e
                time.sleep(10 * (attempt + 1))
                continue
            if r.status_code == 429 and ("PerDay" in r.text or "limit: 0" in r.text):
                # günlük kota bitti: beklemek anlamsız → bu çalışmada bu modeli bir daha deneme
                _exhausted.add(qkey)
                last = f"{model}: günlük ücretsiz kota doldu"
                LOG(f"Gemini {model} günlük kotası doldu → sonraki modele geçiliyor")
                break
            if r.status_code in (429, 500, 502, 503, 504):
                last = f"{r.status_code} {r.text[:200]}"
                wait = _retry_delay(r, attempt)
                LOG(f"Gemini {model} {r.status_code} → {wait:.0f} sn bekleniyor ({attempt+1}/{tries})")
                time.sleep(wait)
                continue
            if r.status_code in (400, 403, 404):
                last = f"{model}: {r.status_code} {r.text[:300]}"
                LOG(f"Gemini {model} kullanılamadı: {r.status_code} {r.text[:200]}")
                break  # bu model olmuyor → sonraki modele geç
            j = r.json()
            cands = j.get("candidates") or []
            text = "".join(p.get("text", "") for p in (cands[0].get("content", {}).get("parts", []) if cands else []))
            if not text:
                last = f"boş yanıt: {json.dumps(j)[:300]}"
                time.sleep(5)
                continue
            return text
    raise RuntimeError(f"Gemini yanıt vermedi: {last}")


def gemini_json(system, user, models, required=(), images=None, temperature=0.8):
    last = None
    for attempt in range(3):
        try:
            data = extract_json(gemini(user, system=system + "\nReply with valid JSON only.", models=models,
                                       images=images, json_mode=True, temperature=temperature))
            missing = [k for k in required if k not in data]
            if missing:
                raise KeyError(f"eksik alanlar: {missing}")
            return data
        except (ValueError, KeyError, json.JSONDecodeError) as e:
            last = e
    raise RuntimeError(f"Gemini geçerli JSON üretemedi: {last}")


def cloudflare_image(prompt, out, model="@cf/black-forest-labs/flux-1-schnell", steps=4):
    acc, tok = os.environ["CLOUDFLARE_ACCOUNT_ID"], os.environ["CLOUDFLARE_API_TOKEN"]
    last = None
    for attempt in range(3):
        try:
            r = requests.post(f"{CF}/{acc}/ai/run/{model}", headers={"Authorization": f"Bearer {tok}"},
                              json={"prompt": prompt[:2000], "steps": steps}, timeout=180)
        except requests.RequestException as e:
            last = e
            time.sleep(8 * (attempt + 1))
            continue
        if r.status_code == 429 or "neuron" in r.text.lower() and r.status_code >= 400:
            raise QuotaError(f"Cloudflare günlük ücretsiz kota doldu: {r.text[:200]}")
        if r.ok:
            img = (r.json().get("result") or {}).get("image")
            if img:
                Path(out).write_bytes(base64.b64decode(img))
                return out
        last = f"{r.status_code} {r.text[:300]}"
        time.sleep(8 * (attempt + 1))
    raise RuntimeError(f"Cloudflare görsel üretemedi: {last}")


def gemini_tts(text, voice="Charon", style=None, models=("gemini-3.1-flash-tts-preview",), tries=5):
    """Gemini ile seslendirme (Türkçe dahil). Dönüş: 24 kHz mono float32 numpy dizisi.
    'style' doğal dille üslup talimatıdır (ör. 'kısık, gizemli bir belgesel anlatıcısı gibi oku')."""
    import numpy as np
    key = os.environ["GEMINI_API_KEY"]
    prompt = f"{style}:\n{text}" if style else text
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["AUDIO"],
                                 "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}}
    last = None
    for model in models:
        for attempt in range(tries):
            try:
                r = requests.post(f"{GEMINI}/{model}:generateContent", headers={"x-goog-api-key": key},
                                  json=body, timeout=300)
            except requests.RequestException as e:
                last = e
                time.sleep(10 * (attempt + 1))
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                last = f"{r.status_code} {r.text[:200]}"
                wait = _retry_delay(r, attempt)
                LOG(f"Gemini TTS {model} {r.status_code} → {wait:.0f} sn bekleniyor")
                time.sleep(wait)
                continue
            if r.status_code in (400, 403, 404):
                last = f"{model}: {r.status_code} {r.text[:300]}"
                LOG(f"Gemini TTS {model} kullanılamadı: {r.status_code} {r.text[:150]}")
                break
            try:
                parts = r.json()["candidates"][0]["content"]["parts"]
                b64 = next(p["inlineData"]["data"] for p in parts if "inlineData" in p)
                pcm = np.frombuffer(base64.b64decode(b64), dtype="<i2").astype(np.float32) / 32768.0
                if pcm.size > 2400:
                    return pcm
                last = "boş ses"
            except Exception as e:
                last = f"yanıt çözülemedi: {e}"
            time.sleep(5)
    raise RuntimeError(f"Gemini TTS ses üretemedi: {last}")
