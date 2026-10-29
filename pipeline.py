"""
OtoYT v3 (ÜCRETSİZ) - Faceless YouTube otomasyon hattı

Akış:
  Analitik → Konu → Senaryo → Web'li bilgi doğrulama → Editör puanı
  → Seslendirme (Kokoro) → Görseller (Pexels + AI diyagram)
  → Render (16:9) → Short (9:16) → 3 thumbnail + hakem → YouTube → (Instagram)

Servisler (hepsi ücretsiz): Gemini, Cloudflare Workers AI, Kokoro, Pexels, YouTube API, GitHub Actions

Çalıştırma:
  python pipeline.py              # üret + yükle
  DRY_RUN=1 python pipeline.py    # üret, yükleme yapma (out/ klasörüne kaydeder)
  python selftest.py              # API'siz render testi
"""
import json, os, random, re, shutil, sys, tempfile, time, traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import yaml

import ai
import media
import publish
import voice

ROOT = Path(__file__).parent
CFG = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
HISTORY = ROOT / "history.json"
OUT = ROOT / "out"
DRY_RUN = os.getenv("DRY_RUN", "").lower() in ("1", "true", "yes")


# ---------------------------------------------------------------- yardımcılar
def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def notify(text):
    tok, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if tok and chat:
        try:
            requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                          json={"chat_id": chat, "text": text[:4000]}, timeout=20)
        except Exception:
            pass


def llm_json(system, user, required=(), images=None, judge=False):
    models = CFG["judge_models"] if judge else CFG["text_models"]
    return ai.gemini_json(system, user, models=models, required=required, images=images,
                          temperature=0.3 if judge else 0.8)


def load_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- 1. konu (analitikle)
def choose_pillar(report):
    pillars = CFG["content_pillars"]
    sc = report["pillar_scores"]
    if not sc or random.random() < CFG.get("explore_rate", 0.25):
        return random.choice(pillars)
    avg = sum(sc.values()) / len(sc)
    weights = [max(sc.get(p, avg), 0.1) ** 2 for p in pillars]
    return random.choices(pillars, weights=weights)[0]


def pick_topic(history, report):
    past = [h["title"] for h in history if h.get("type", "long") == "long"][-300:]
    pillar = choose_pillar(report)
    perf = ""
    if report["best"]:
        perf += "\nBest performing videos (do more like these):\n" + "\n".join(
            f"- {r['title']} ({r['views']} views, {r['avg_pct']:.0f}% watched)" for r in report["best"])
    if report["worst"]:
        perf += "\nWorst performing videos (avoid these patterns):\n" + "\n".join(
            f"- {r['title']} ({r['views']} views, {r['avg_pct']:.0f}% watched)" for r in report["worst"])
    data = llm_json(
        f"You are a YouTube strategist. Channel: {CFG['channel_name']}. Brief: {CFG['channel_brief']}\n"
        f"Audience: {CFG['audience']}. Language: {CFG['language']}.",
        f"Content pillar for this video: {pillar}\n{perf}\n"
        "Already published titles (DO NOT repeat or closely paraphrase):\n- " + "\n- ".join(past or ["(none)"]) +
        "\n\nPropose 6 specific video ideas with high curiosity and broad appeal, each with a clear angle "
        "a viewer can't get from the first search result. Pick the one most likely to get high click-through "
        "AND high retention.\n"
        'Return JSON: {"ideas":[{"title":"...","angle":"..."}],"chosen_index":0}',
        required=("ideas", "chosen_index"))
    idea = data["ideas"][min(int(data["chosen_index"]), len(data["ideas"]) - 1)]
    idea["pillar"] = pillar
    return idea


# ---------------------------------------------------------------- 2. senaryo
SCRIPT_SCHEMA = (
    'Return JSON: {"title":"<=70 chars","description":"150-300 words, include 3 hashtags",'
    '"tags":["8-15 tags"],'
    '"thumbnail_concepts":[{"image_prompt":"visual concept, NO text in image","text":"2-4 punchy words"}],'
    '"short_title":"<=60 chars hook title for the Shorts cut",'
    '"ig_caption":"Instagram Reel caption: 1-line hook, 2-3 short lines of value, '
    'CTA to watch the full video on YouTube via link in bio, then 5-8 relevant hashtags",'
    '"scenes":[{"narration":"2-3 sentences","visual":"footage or diagram",'
    '"broll_query":"2-4 word English stock footage query",'
    '"image_prompt":"explanatory illustration of exactly what this scene explains"}]}\n'
    "Give exactly 3 thumbnail_concepts with different ideas. EVERY scene needs BOTH broll_query and image_prompt. "
    "Use visual=diagram whenever a mechanism, inside view, process, comparison or scale must be SHOWN; "
    "use footage for real-world context. At least {ratio}% of scenes must be diagram. "
    "Write narration for the ear: spell out numbers and units in words (\"about forty percent\"), no symbols, "
    "no abbreviations, no parentheses.")


def _schema():
    return SCRIPT_SCHEMA.replace("{ratio}", str(int(CFG["min_diagram_ratio"] * 100)))


def write_script(idea):
    return llm_json(
        f"You write scripts for a faceless educational YouTube channel called {CFG['channel_name']}. "
        f"{CFG['channel_brief']}\nWrite in {CFG['language']}. Voice: {CFG['tone']}. Rules: {CFG['script_rules']}",
        f"Title idea: {idea['title']}\nAngle: {idea['angle']}\n"
        f"Target length: ~{CFG['target_words']} words of narration. The FIRST 3 scenes must work on their own "
        f"as a ~50 second Short (strong hook + payoff).\n{_schema()}",
        required=("title", "description", "tags", "thumbnail_concepts", "scenes"))


def revise_script(script, notes):
    return llm_json(
        f"You revise YouTube scripts. Language: {CFG['language']}. Rules: {CFG['script_rules']}",
        f"Problems to fix:\n{notes}\n\nCurrent script JSON:\n{json.dumps(script, ensure_ascii=False)}\n\n"
        "Fix every problem. Replace wrong facts with the correct ones given; remove or soften anything "
        f"uncertain. Keep the same structure and length. {_schema()}",
        required=("title", "description", "tags", "thumbnail_concepts", "scenes"))


# ---------------------------------------------------------------- 3. bilgi doğrulama (Google aramalı)
def fact_check(script):
    text = "\n".join(s["narration"] for s in script["scenes"])
    prompt = (
        "You are a rigorous fact-checker for an educational YouTube video. Extract every specific, checkable "
        "factual claim (numbers, dates, how-something-works statements, superlatives) from the script below, "
        "max 15. Verify EACH one with Google Search against reliable sources. Be strict.\n\n"
        f"Title: {script['title']}\nScript:\n{text}\n\n"
        'Return ONLY JSON, no other text: {"claims":[{"claim":"...","verdict":"correct|wrong|uncertain",'
        '"correction":"correct version if wrong/uncertain"}]}')
    out = ai.gemini(prompt, models=CFG["factcheck_models"], search=True, temperature=0.1)
    return ai.extract_json(out)["claims"]


def verify_loop(script):
    for rnd in range(CFG["max_factcheck_rounds"] + 1):
        try:
            claims = fact_check(script)
        except Exception as e:
            if CFG.get("require_web_factcheck", True):
                raise
            log(f"Doğrulama atlandı: {e}")
            return script, True
        bad = [c for c in claims if c.get("verdict") != "correct"]
        log(f"Doğrulama turu {rnd+1}: {len(claims)} iddia, {len(bad)} sorunlu")
        if not bad:
            return script, True
        if rnd == CFG["max_factcheck_rounds"]:
            return script, False
        notes = "\n".join(f"- [{c.get('verdict')}] {c.get('claim')} → {c.get('correction') or 'remove it'}"
                          for c in bad)
        script = revise_script(script, notes)
    return script, False


# ---------------------------------------------------------------- 4. editör puanı
def quality_gate(script):
    text = "\n".join(s["narration"] for s in script["scenes"])
    r = llm_json(
        "You are a strict senior YouTube editor. Score honestly; average AI scripts deserve 5-6.",
        f"Audience: {CFG['audience']}\nTitle: {script['title']}\nScript:\n{text}\n\n"
        "Score 1-10: originality, hook (first 15 seconds), clarity (would a curious non-expert truly "
        "understand?), value_density, audience_fit. Return JSON: {\"originality\":0,\"hook\":0,\"clarity\":0,"
        "\"value_density\":0,\"audience_fit\":0,\"problems\":[\"...\"]}",
        required=("problems",), judge=True)
    keys = ("originality", "hook", "clarity", "value_density", "audience_fit")
    scores = [float(r.get(k, 0)) for k in keys]
    return min(scores) >= CFG["min_quality_score"], r


# ---------------------------------------------------------------- 5. ses (Kokoro, ücretsiz)
def narrate(script, workdir):
    v = CFG["voice"]
    files, durs, words = voice.narrate(script["scenes"], workdir, voice=v["name"], lang=v["lang"],
                                       speed=v.get("speed", 1.0))
    for sc, d in zip(script["scenes"], durs):
        sc["duration"] = d
    full = media.concat(files, workdir / "voice.wav", workdir, "vo")
    return full, words


# ---------------------------------------------------------------- 6. görseller
def _download(url, out):
    with requests.get(url, stream=True, timeout=180) as d:
        d.raise_for_status()
        with open(out, "wb") as fh:
            for c in d.iter_content(1 << 20):
                fh.write(c)
    return out


def fetch_pixabay(query, out, ledger):
    key = os.getenv("PIXABAY_API_KEY")
    if not key or not query:
        return None
    try:
        r = requests.get("https://pixabay.com/api/videos/",
                         params={"key": key, "q": query[:100], "video_type": "film", "min_width": 1280,
                                 "safesearch": "true", "per_page": 30}, timeout=30)
        r.raise_for_status()
    except Exception as e:
        log(f"Pixabay hatası ({query}): {e}")
        return None
    hits = [h for h in r.json().get("hits", []) if not ledger.used(f"x{h['id']}")]
    random.shuffle(hits)
    for h in hits[:5]:
        vs = h.get("videos", {})
        # 1920'lik "medium"ı tercih et; 4K "large"ı gereksiz indirme
        cand = [vs.get(k) for k in ("medium", "large", "small")]
        v = next((c for c in cand if c and c.get("url") and c.get("width", 0) >= 1280), None)
        if not v:
            continue
        try:
            _download(v["url"], out)
            ledger.add(f"x{h['id']}")
            return out
        except Exception as e:
            log(f"Pixabay indirme hatası: {e}")
    return None


def fetch_pixabay_photo(query, out, ledger):
    key = os.getenv("PIXABAY_API_KEY")
    if not key or not query:
        return None
    try:
        r = requests.get("https://pixabay.com/api/",
                         params={"key": key, "q": query[:100], "image_type": "photo", "orientation": "horizontal",
                                 "min_width": 1280, "safesearch": "true", "per_page": 30}, timeout=30)
        r.raise_for_status()
        hits = [h for h in r.json().get("hits", []) if not ledger.used(f"xp{h['id']}")]
        if not hits:
            return None
        h = random.choice(hits[:10])
        _download(h["largeImageURL"], out)
        ledger.add(f"xp{h['id']}")
        return out
    except Exception as e:
        log(f"Pixabay foto hatası: {e}")
        return None


def fetch_stock(query, out, ledger):
    """Stok video: önce Pixabay, sonra Pexels (hangisinin anahtarı varsa)."""
    for fn in (fetch_pixabay, fetch_pexels):
        res = fn(query, out, ledger)
        if res:
            return res
    return None


def fetch_stock_photo(query, out, ledger):
    for fn in (fetch_pixabay_photo, fetch_pexels_photo):
        res = fn(query, out, ledger)
        if res:
            return res
    return None


def fetch_pexels(query, out, ledger):
    if not os.getenv("PEXELS_API_KEY") or not query:
        return None
    try:
        r = requests.get("https://api.pexels.com/videos/search",
                         headers={"Authorization": os.environ["PEXELS_API_KEY"]},
                         params={"query": query, "per_page": 30, "orientation": "landscape"}, timeout=30)
        r.raise_for_status()
    except Exception as e:
        log(f"Pexels hatası ({query}): {e}")
        return None
    vids = [v for v in r.json().get("videos", []) if not ledger.used(v["id"])]
    random.shuffle(vids)
    for v in vids[:5]:
        files = [f for f in v.get("video_files", []) if f.get("width") and f.get("link")]
        if not files:
            continue
        good = [f for f in files if 1280 <= f["width"] <= 2560] or files
        f = min(good, key=lambda f: abs(f["width"] - 1920))
        try:
            with requests.get(f["link"], stream=True, timeout=180) as d:
                d.raise_for_status()
                with open(out, "wb") as fh:
                    for c in d.iter_content(1 << 20):
                        fh.write(c)
            ledger.add(v["id"])
            return out
        except Exception as e:
            log(f"İndirme hatası: {e}")
    return None


def fetch_pexels_photo(query, out, ledger):
    """Görsel kotası biterse yedek: Pexels fotoğrafı (hareket efektiyle kullanılır)."""
    if not os.getenv("PEXELS_API_KEY") or not query:
        return None
    try:
        r = requests.get("https://api.pexels.com/v1/search",
                         headers={"Authorization": os.environ["PEXELS_API_KEY"]},
                         params={"query": query, "per_page": 30, "orientation": "landscape"}, timeout=30)
        r.raise_for_status()
        photos = [p for p in r.json().get("photos", []) if not ledger.used(f"p{p['id']}")]
        if not photos:
            return None
        p = random.choice(photos[:10])
        Path(out).write_bytes(requests.get(p["src"]["large2x"], timeout=120).content)
        ledger.add(f"p{p['id']}")
        return out
    except Exception as e:
        log(f"Pexels foto hatası: {e}")
        return None


_quota_hit = False


def gen_image(prompt, out, fallback_query=None, ledger=None):
    """AI diyagram (Cloudflare). Günlük ücretsiz kota biterse Pexels fotoğrafına düşer."""
    global _quota_hit
    out = Path(out).with_suffix(".jpg")
    if not os.getenv("CLOUDFLARE_API_TOKEN") or not os.getenv("CLOUDFLARE_ACCOUNT_ID"):
        _quota_hit = True  # Cloudflare kurulmamış → doğrudan Pexels fotoğrafı
    if not _quota_hit:
        try:
            return ai.cloudflare_image(prompt + ", wide 16:9 composition, subject centered", out)
        except ai.QuotaError as e:
            _quota_hit = True
            log(f"{e} → bu video için Pexels fotoğraflarına geçiliyor")
        except Exception as e:
            log(f"Görsel üretilemedi, yedek kullanılacak: {e}")
    if fallback_query and ledger is not None:
        for q in (fallback_query, CFG["fallback_broll"]):
            if fetch_stock_photo(q, out, ledger):
                return out
    raise RuntimeError("Ne AI görsel ne yedek fotoğraf bulunabildi")


def build_segments(script, workdir, ledger):
    style = CFG["visual_style"]
    scenes = script["scenes"]
    need = int(len(scenes) * CFG["min_diagram_ratio"] + 0.999)
    have = sum(1 for s in scenes if s.get("visual") == "diagram")
    for s in sorted(scenes, key=lambda s: -s.get("duration", 0)):
        if have >= need:
            break
        if s.get("visual") != "diagram":
            s["visual"], have = "diagram", have + 1

    segs, moves = [], ["in", "right", "out", "left"]
    for i, sc in enumerate(scenes):
        seg = workdir / f"seg_{i:02d}.mp4"
        clip = None
        if sc.get("visual") != "diagram":
            clip = fetch_stock(sc.get("broll_query", ""), workdir / f"raw_{i:02d}.mp4", ledger) \
                or fetch_stock(CFG["fallback_broll"], workdir / f"raw_{i:02d}.mp4", ledger)
        if clip:
            media.render_footage(clip, sc["duration"], seg)
        else:
            img = gen_image(f"{sc.get('image_prompt') or sc['narration']}. Style: {style}",
                            workdir / f"img_{i:02d}.jpg", sc.get("broll_query"), ledger)
            media.render_still(img, sc["duration"], seg, move=moves[i % 4])
        segs.append(seg)
    return segs


# ---------------------------------------------------------------- 7. thumbnail
def make_thumbnails(script, workdir, ledger):
    cands = []
    for i, c in enumerate(script["thumbnail_concepts"][:3]):
        raw = gen_image(f"{c['image_prompt']}. {CFG['thumbnail_style']}. No text, no letters, no logos.",
                        workdir / f"thumb_raw_{i}.jpg", script["scenes"][0].get("broll_query"), ledger)
        cands.append(media.compose_thumbnail(raw, c["text"], workdir / f"thumb_{i}.jpg"))
    sheet = media.contact_sheet(cands, workdir / "thumbs_sheet.jpg")
    try:
        pick = llm_json(
            "You are a YouTube thumbnail expert who knows what gets clicked on mobile.",
            f"Video title: {script['title']}\nThe attached image shows 3 thumbnail candidates (#1, #2, #3). "
            "Pick the one with the highest expected click-through rate: instantly readable on a small phone "
            "screen, one clear focal point, strong curiosity that matches the title, no clutter. "
            'Return JSON: {"best":1,"reason":"..."}',
            images=[sheet], required=("best",), judge=True)
        idx = min(max(int(pick["best"]) - 1, 0), len(cands) - 1)
        log(f"Thumbnail seçimi: #{idx+1} - {pick.get('reason','')}")
    except Exception as e:
        log(f"Thumbnail hakemi çalışmadı, ilki seçildi: {e}")
        idx = 0
    return cands[idx]


# ---------------------------------------------------------------- ana akış
def meta(title, desc, tags, publish_at, is_short=False):
    return {"title": title, "description": desc, "tags": [t for t in tags if len(t) >= 2][:15],
            "category_id": CFG["category_id"], "lang": CFG["language_code"],
            "made_for_kids": bool(CFG.get("made_for_kids", False)),
            "synthetic": bool(CFG.get("declare_synthetic", True)),
            "privacy": CFG["publish_mode"] if CFG["publish_mode"] != "scheduled" else "private",
            "publish_at": publish_at, "is_short": is_short}


def run_once():
    history = load_json(HISTORY, [])
    ledger = media.AssetLedger(ROOT / "assets_used.json")

    report = publish.performance_report(history)
    log(f"Analitik: {report['n']} video ölçüldü, sütun puanları: {report['pillar_scores']}")
    idea = pick_topic(history, report)
    log(f"Konu: {idea['title']}  [{idea['pillar']}]")
    script = write_script(idea)

    script, facts_ok = verify_loop(script)
    if not facts_ok:
        notify(f"⛔ İptal (bilgi doğrulanamadı): {script['title']}")
        log("Bilgi doğrulamadan geçemedi, bu tur atlanıyor.")
        return

    for attempt in range(CFG["max_rewrites"] + 1):
        ok, review = quality_gate(script)
        log(f"Editör: {review}")
        if ok:
            break
        if attempt == CFG["max_rewrites"]:
            notify(f"⛔ İptal (kalite düşük): {script['title']}\n{review.get('problems')}")
            return
        script = revise_script(script, "\n".join(f"- {p}" for p in review.get("problems", [])))
        script, facts_ok = verify_loop(script)
        if not facts_ok:
            notify(f"⛔ İptal (revizyon sonrası bilgi hatası): {script['title']}")
            return

    with tempfile.TemporaryDirectory() as tmp:
        w = Path(tmp)
        voice_file, words = narrate(script, w)
        log(f"Seslendirme: {media.duration(voice_file):.0f} sn")
        srt = media.write_srt(words, w / "subs.srt")
        segs = build_segments(script, w, ledger)
        silent = media.concat(segs, w / "silent.mp4", w, "segs")
        music = ROOT / "assets" / "music.mp3"
        final = media.mux_final(silent, voice_file, srt, w / "final.mp4", music=music)
        log(f"Ana video: {media.duration(final):.0f} sn")

        short = None
        if CFG.get("make_shorts", True):
            short, slen = media.make_short(segs, [s["duration"] for s in script["scenes"]], voice_file, words,
                                           w / "short.mp4", w, music=music)
            log(f"Short: {slen:.0f} sn")
        thumb = make_thumbnails(script, w, ledger)

        OUT.mkdir(exist_ok=True)
        for f in (final, thumb, short):
            if f:
                shutil.copy(f, OUT / Path(f).name)
        save_json(OUT / "script.json", script)

        if DRY_RUN:
            ledger.save()
            notify(f"🧪 Test videosu hazır (yüklenmedi): {script['title']}")
            log("DRY_RUN: yükleme atlandı, dosyalar out/ klasöründe.")
            return

        now = datetime.now(timezone.utc)
        sched = CFG["publish_mode"] == "scheduled"
        at = lambda h: (now + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ") if sched else None
        up = CFG.get("uploader", "youtube")

        desc = script["description"] + "\n\nStock footage: Pixabay / Pexels. Narration: AI voice."
        vid = publish.upload(final, meta(script["title"], desc, script["tags"],
                                         at(CFG["publish_delay_hours"])), thumb, up)
        history.append({"date": datetime.now().isoformat(timespec="minutes"), "type": "long",
                        "title": script["title"], "pillar": idea["pillar"], "video_id": vid})
        save_json(HISTORY, history)
        ledger.save()
        notify(f"✅ Yüklendi: {script['title']}\nhttps://youtu.be/{vid}\n"
               f"Yayın: {CFG['publish_delay_hours']} saat sonra. Beğenmezsen Studio'dan sil.")

        if short:
            try:
                sdesc = f"{script.get('short_title', script['title'])}\n\nFull video: https://youtu.be/{vid}\n#Shorts"
                sid = publish.upload(short, meta(script.get("short_title", script["title"])[:90] + " #Shorts",
                                                 sdesc, script["tags"], at(CFG["shorts_delay_hours"]),
                                                 is_short=True), None, up)
                history.append({"date": datetime.now().isoformat(timespec="minutes"), "type": "short",
                                "title": script.get("short_title", script["title"]),
                                "pillar": idea["pillar"], "video_id": sid, "parent": vid})
                save_json(HISTORY, history)
                notify(f"📱 Short yüklendi: https://youtu.be/{sid}")
            except Exception as e:
                notify(f"⚠️ Short yüklenemedi: {e}")

        if short and CFG.get("instagram_enabled") and CFG["publish_mode"] != "private":
            try:
                cap = script.get("ig_caption") or (
                    f"{script.get('short_title', script['title'])}\n\n"
                    "Full video on our YouTube channel (link in bio).\n\n"
                    + " ".join("#" + re.sub(r"\W", "", t) for t in script["tags"][:6]))
                mid, link = publish.instagram_reel(short, cap, ai_label=CFG.get("instagram_ai_label", True))
                history.append({"date": datetime.now().isoformat(timespec="minutes"), "type": "reel",
                                "title": script.get("short_title", script["title"]),
                                "pillar": idea["pillar"], "video_id": mid, "parent": vid})
                save_json(HISTORY, history)
                notify(f"📸 Instagram Reel yayında: {link or mid}")
            except Exception as e:
                notify(f"⚠️ Instagram Reel yüklenemedi: {e}")
        for f in OUT.glob("*"):
            f.unlink()
    log(f"Bitti: https://youtu.be/{vid}")


if __name__ == "__main__":
    try:
        run_once()
    except Exception:
        err = traceback.format_exc()
        notify(f"❌ OtoYT hata (video varsa GitHub Actions 'artifacts' altında):\n{err[-3000:]}")
        print(err, file=sys.stderr)
        sys.exit(1)
