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
import json, math, os, random, re, shutil, subprocess, sys, tempfile, time, traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import yaml

import ai
import drive
import media
import release
import publish
import voice

ROOT = Path(__file__).parent
# Kanal seçimi: OTOYT_CONFIG=channels/golgedefter.yaml gibi (varsayılan: Cutaway → config.yaml)
CONFIG_PATH = ROOT / os.getenv("OTOYT_CONFIG", "config.yaml")
CFG = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
# Kanal başına anahtar eşlemesi (ör. YT_REFRESH_TOKEN ← YT2_REFRESH_TOKEN)
# seslendirme için öbür kanalın anahtarı da yedek olarak kullanılır (iki kanal farklı günlerde üretiyor;
# Google'ın ücretsiz günlük ses hakkı anahtar/proje başına → toplam ses hakkı iki katına çıkar)
_tts_keys = [os.getenv("GEMINI_API_KEY", ""), os.getenv("GEMINI_PAID_API_KEY", "")] + \
    [k.strip() for k in os.getenv("GEMINI_TTS_EXTRA_KEYS", "").split(",")]
for _k, _v in (CFG.get("env_map") or {}).items():
    if os.getenv(_v):
        os.environ[_k] = os.environ[_v]
ai.TTS_KEYS = [os.environ.get("GEMINI_API_KEY", "")] + [k for k in _tts_keys if k]  # önce kanalın kendi anahtarı
DATA = ROOT / CFG.get("data_dir", ".")          # kanalın geçmiş/kayıt dosyaları
ASSETS = ROOT / CFG.get("assets_dir", "assets")  # kanalın logo/intro/müzik dosyaları
DATA.mkdir(parents=True, exist_ok=True)
HISTORY = DATA / "history.json"
OUT = ROOT / "out"
DRY_RUN = os.getenv("DRY_RUN", "").lower() in ("1", "true", "yes")
UPLOAD_STARTED = False   # True olduktan sonra hata olursa otomatik tekrar deneme YAPILMAZ (çift yükleme olmasın)


# ---------------------------------------------------------------- yardımcılar
RUNLOG = DATA / "logs" / "last_run.txt"
if CFG.get("ig_user_id"):  # kanalın kendi Instagram hesabı config'de sabit: başka hesaba paylaşım asla olmaz
    os.environ["IG_USER_ID"] = str(CFG["ig_user_id"])
media.LANG = CFG.get("language_code", "en")
media.CHAPTER_LABEL = CFG.get("chapter_label", "CHAPTER")
media.THEME.update(CFG.get("caption_theme") or {})
_SECRET_ENVS = ("GEMINI_API_KEY", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "PIXABAY_API_KEY",
                "PEXELS_API_KEY", "YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN", "AYRSHARE_API_KEY",
                "IG_ACCESS_TOKEN", "IG_USER_ID", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
                "WHATSAPP_PHONE", "WHATSAPP_APIKEY", "YT2_CLIENT_ID", "YT2_CLIENT_SECRET",
                "YT2_REFRESH_TOKEN", "GEMINI_PAID_API_KEY")


def redact(text):
    """Kayda yazılan her şeyden anahtarları siler (log repoya da kaydedildiği için)."""
    text = str(text)
    for k in _SECRET_ENVS:
        v = os.getenv(k)
        if v and len(v) >= 4:
            text = text.replace(v, f"***{k}***")
    return re.sub(r"(key|token|apikey|signature|sig)=([^&\s\"']{6,})", r"\1=***", text, flags=re.I)


def log(msg):
    line = redact(f"[{datetime.now():%H:%M:%S}] {msg}")
    print(line, flush=True)
    try:
        RUNLOG.parent.mkdir(exist_ok=True)
        with open(RUNLOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def notify(text, code=None):
    """Telegram ve/veya WhatsApp'a (CallMeBot) bildirim. Hangisinin anahtarı varsa ona gider.
    code: Telegram'da dokununca kopyalanan kutu içinde gösterilir (başlık gibi)."""
    if CFG.get("notify_prefix"):  # birden çok kanal: mesaj hangi kanaldan geliyor belli olsun
        text = f"{CFG['notify_prefix']} {text}"
    tok, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if tok and chat:
        try:
            import html as _h
            body = ({"chat_id": chat, "parse_mode": "HTML",
                     "text": f"{_h.escape(text)}\n<code>{_h.escape(code)}</code>"[:4000]} if code else
                    {"chat_id": chat, "text": text[:4000]})
            requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", json=body, timeout=20)
        except Exception:
            pass
    phone, key = os.getenv("WHATSAPP_PHONE"), os.getenv("WHATSAPP_APIKEY")
    if code:
        text = f"{text}\n{code}"
    if phone and key:
        try:
            requests.get("https://api.callmebot.com/whatsapp.php",
                         params={"phone": phone.replace(" ", ""), "text": text[:1000], "apikey": key},
                         timeout=30)
        except Exception:
            pass


def llm_json(system, user, required=(), images=None, judge=False):
    models = CFG["judge_models"] if judge else CFG["text_models"]
    return ai.gemini_json(system, user, models=models, required=required, images=images,
                          temperature=0.3 if judge else 0.8)


def load_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


# ---------------------------------------------------------------- 1. konu (analitikle)
def choose_pillar(report):
    pillars = CFG["content_pillars"]
    sc = report["pillar_scores"]
    base = CFG.get("pillar_weights") or [1] * len(pillars)
    base = (list(base) + [1] * len(pillars))[:len(pillars)]
    if not sc or random.random() < CFG.get("explore_rate", 0.25):
        return random.choices(pillars, weights=base)[0]
    avg = sum(sc.values()) / len(sc)
    weights = [max(sc.get(p, avg), 0.1) ** 2 * b for p, b in zip(pillars, base)]
    return random.choices(pillars, weights=weights)[0]


def fetch_viewer_requests():
    """İzleyici yorumlarındaki konu istekleri (Jared Owen gibi: izleyiciye sor, onu çek)."""
    if DRY_RUN or not os.getenv("YT_REFRESH_TOKEN") or CFG.get("uploader", "youtube") != "youtube":
        return []
    try:
        return publish.viewer_requests()
    except Exception as e:
        log(f"Yorumlar okunamadı: {str(e)[:120]}")
        return []


def next_series(history):
    """config 'series': sırayla yayınlanacak planlı bölümler (ör. Cin serisi). Varsayılan: her 2 videodan biri.
    Senaryosu kaliteyi geçemeyen bölüm o çalışmada atlanır (history'ye 'tried' olarak düşer), sonra tekrar denenir."""
    items = CFG.get("series") or []
    if not items:
        return None
    longs = [h for h in history if h.get("type", "long") == "long"]
    done = {h.get("series_id") for h in longs if h.get("series_id")}
    tried = {h.get("title") for h in longs if not h.get("date")}  # bu çalışmada denenip reddedilenler
    every = int(CFG.get("series_every", 2))
    recent = [h for h in longs if h.get("date")][-(every - 1):] if every > 1 else []
    if any(h.get("series_id") for h in recent):
        return None  # araya normal video girsin
    for it in items:
        if it["id"] in done or it["title"] in tried:
            continue
        log(f"Seri bölümü seçildi: {it['title']}")
        notify(f"📚 Seri bölümü: {it['title']}{tr_note(it['title'])}")
        return {"title": it["title"], "angle": it["angle"], "pillar": it.get("pillar") or CFG["content_pillars"][0],
                "search_query": it.get("search_query", it["title"]), "series_id": it["id"]}
    return None


def pick_topic(history, report):
    s_ = next_series(history)
    if s_:
        return s_
    past = [h["title"] for h in history if h.get("type", "long") == "long"][-300:]
    pillar = choose_pillar(report)
    perf = ""
    reqs = fetch_viewer_requests() if not hasattr(pick_topic, "_reqs") else pick_topic._reqs
    pick_topic._reqs = reqs
    if reqs:
        perf += ("\nRecent viewer comments (if several viewers ask for a topic that fits the channel, PREFER it "
                 "even if it is outside this pillar):\n" + "\n".join(f"- {c}" for c in reqs[:30]))
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
        "AND high retention.\n" + (f"Idea rules: {CFG['idea_rules']}\n" if CFG.get("idea_rules") else "") +
        'Return JSON: {"ideas":[{"title":"...","angle":"...","search_query":"what a viewer would type to find '
        'videos on this topic"}],"chosen_index":0}',
        required=("ideas", "chosen_index"))
    idea = data["ideas"][min(int(data["chosen_index"]), len(data["ideas"]) - 1)]
    idea = differentiate(data["ideas"], idea)
    idea["pillar"] = pillar
    return idea


def differentiate(ideas, chosen):
    """Rakip analizi: her fikir için YouTube'da ilk 10 videoya bakar. Konu doymuşsa ya daha az işlenmiş fikri seçer
    ya da rakiplerin anlatmadığı özgün bir açı + 'neden bizden izlemeli' vaadi belirler."""
    if DRY_RUN or not os.getenv("YT_REFRESH_TOKEN") or not CFG.get("competition_check", True):
        return chosen
    region = "TR" if CFG.get("language_code") == "tr" else "US"
    rows = []
    for i, it in enumerate(ideas[:4]):  # her arama ~101 YouTube kota birimi
        try:
            comp = publish.competition(it.get("search_query") or it["title"], region=region,
                                       lang=CFG.get("language_code", "en"))
        except Exception as e:
            log(f"Rakip analizi yapılamadı: {str(e)[:120]}")
            return chosen
        big = [c for c in comp if c["views"] >= CFG.get("saturation_views", 100000)]
        rows.append((i, it, comp, big))
    lines = []
    for i, it, comp, big in rows:
        lines.append(f"#{i} {it['title']} — top results with {CFG.get('saturation_views', 100000)}+ views: {len(big)}/10\n"
                     + "\n".join(f"   · {c['title']} ({c['views']:,} views, {c['channel']}, {c['age_days']} days ago)"
                                  for c in comp[:6]))
    try:
        r = llm_json(
            "You are a YouTube strategist who wins against saturated competition.",
            "Our candidate ideas and what already ranks on YouTube for each:\n" + "\n".join(lines) +
            "\n\nChoose the idea where WE can win: prefer topics with few big videos; a saturated topic is allowed "
            "ONLY if you find an angle none of the top videos cover (a lesser-known document, witness, new research, "
            "a local/Turkish connection, a myth the popular videos repeat that is actually wrong, an unusual "
            "point of view). Write that angle concretely, and a one-sentence 'why watch ours' promise that the "
            "opening must deliver. Also give a fresh title idea that does NOT resemble the top results' titles. "
            'JSON: {"index":0,"angle":"...","why_ours":"...","title":"...","saturated":false}',
            required=("index", "angle"), judge=False)
        k = min(max(int(r["index"]), 0), len(rows) - 1)
        idea = dict(rows[k][1])
        idea["angle"] = f"{r['angle']} | WHY VIEWERS SHOULD WATCH OURS: {r.get('why_ours', '')}"
        if r.get("title"):
            idea["title"] = r["title"]
        idea["competitors"] = [c["title"] for c in rows[k][2][:6]]
        log(f"Rakip analizi: '{idea['title']}' seçildi ({len(rows[k][3])}/10 büyük rakip). Açı: {r['angle'][:160]}")
        notify(f"🔎 Konu seçildi: {idea['title']}{tr_note(idea['title'])}\nYouTube'da bu konuda {len(rows[k][3])} büyük video var. "
               f"Bizi farklı kılacak açı: {r['angle'][:300]}")
        return idea
    except Exception as e:
        log(f"Rakip analizi değerlendirilemedi: {str(e)[:120]}")
        return chosen


# ---------------------------------------------------------------- 2. senaryo
SCRIPT_SCHEMA = (
    'Return JSON: {"core_keyword":"the exact 2-5 word phrase a viewer types into search for this video",'
    '"title":"<=70 chars; core_keyword must appear within the first 40 characters",'
    '"description":"the first 120 characters (before \'show more\') contain core_keyword AND the video\'s '
    'central question; total 150-300 words; LAST line = 10-15 hashtags in the video\'s language, most important '
    'first (YouTube shows the top 3 above the title): core_keyword joined, the specific names/places/events/terms of '
    'this video, then 3-4 niche tags people follow; every one directly about this video, no generic spam '
    '(#viral #fyp #trending), no spaces inside a hashtag",'
    '"tags":["8-15 tags: core_keyword, close variants, COMMON MISSPELLINGS and alternate spellings of the '
    'names/terms in the video, and related entities (people, places, organizations, objects). No generic words"],'
    '"pinned_comment":"a VOTE in the video\'s language: line 1 = one pointed A-or-B question about the topic that '
    'splits viewers into two camps (specific, respectful, no insults, no politics); line 2 = \'1) \' + option A; '
    'line 3 = \'2) \' + option B; line 4 = a short ask to reply with just 1 or 2 and one word why",'
    '"vote_options":["option A in 1-3 words","option B in 1-3 words"],'
    '"thumbnail_concepts":[{"image_prompt":"IN ENGLISH: ONE striking subject, extreme close-up or dramatic '
    'angle, big and on the RIGHT half, dark uncluttered space on the LEFT, cinematic rim light, high contrast, '
    'something surprising/wrong/extreme visible (cutaway, cracked, glowing, giant scale). NO text in image",'
    '"text":"1-3 words, max 18 characters, must NOT repeat the title: adds the missing piece that creates '
    'curiosity (a shocking number, a contradiction, a question)"}],'
    '"short_title":"<=60 chars hook title for the Shorts cut",'
    '"shorts":[{"start_scene":0,"end_scene":2,"title":"<=60 chars curiosity hook title",'
    '"hook":"why this clip works on its own",'
    '"ig_caption":"Instagram caption for THIS clip (see IG rules)"}],'
    '"ig_caption":"Instagram caption for the first clip (see IG rules)",'
    '"ig_hashtags_note":"IG rules: first line = hook that contains the exact words people would search on '
    'Instagram for this topic (Instagram search reads captions); then 1-2 short lines that add a surprising fact '
    'NOT in the clip; then a question that invites comments; then a CTA: the full video is on YouTube, link in bio; '
    'last line EXACTLY 5 hashtags (Instagram allows max 5): 2 BIG, popular discovery tags that people browse on '
    'Instagram for this kind of content and that fit THIS clip (choose from the channel\'s discovery list), 2 popular '
    'tags specific to this clip\'s subject (the famous name/concept people search, e.g. #deccal, #jinn), 1 brand tag '
    'given in the brief. Every tag must be relevant to the clip. No spaces inside a tag, no punctuation, written in '
    'the video language",'
    '"scenes":[{"narration":"2-3 sentences","visual":"footage or diagram",'
    '"broll_query":"2-4 word English stock footage query",'
    '"image_prompt":"IN ENGLISH: illustration of exactly what this scene explains",'
    '"chapter":"ONLY if a new chapter starts here: a 2-6 word SEARCHABLE phrase a viewer would type into '
    'Google or YouTube about this part (e.g. How X works, Why X fails, What happens when X), else empty",'
    '"callout":"on-screen label, max 5 words: the key number, term or comparison of this scene, or empty"}]}\n'
    "Split the video into 4-7 chapters; scene 0 must start the first chapter. Chapter titles are searchable "
    "phrases, never 'Intro' or 'Part 1'. The narration of scene 0 or 1 must SAY core_keyword out loud "
    "(spoken in the first 20 seconds). Scenes 0 and 1 must be visual=diagram and their image_prompt must "
    "show EXACTLY what their narration says (the viewer sees the proof of the hook, never a generic image). Give a callout to roughly every second scene (numbers, units, key terms), "
    "never a full sentence.\n"
    "Give exactly 3 thumbnail_concepts with clearly DIFFERENT ideas (e.g. the object up close / the extreme "
    "number or scale / the failure or contradiction). " + (CFG.get("thumbnail_rules", "") + " ") + "EVERY scene needs BOTH broll_query and image_prompt. "
    "Use visual=diagram whenever a mechanism, inside view, process, comparison or scale must be SHOWN; "
    "use footage for real-world context. At least {ratio}% of scenes must be diagram. "
    "Write narration for the ear: spell out numbers and units in words (\"about forty percent\"), no symbols, "
    "no abbreviations, no parentheses. "
    "PLAIN LANGUAGE (most important rule): write so that ANYONE understands every sentence on first listen, "
    "even a 12-year-old with no science or history background. Short sentences (mostly under 15 words), "
    "everyday words. Never assume the viewer knows a term, unit, place, person or concept: the first time it "
    "appears, explain it in the same sentence in simple words (e.g. \"refractory bricks, special bricks that "
    "don't melt even in fire\"), or replace it with a simple word. Avoid jargon that isn't needed at all. "
    "Every big number, temperature, size or speed gets an everyday comparison the viewer can picture "
    "(\"hot enough to melt a car in seconds\", \"as tall as a ten-storey building\"). {units} "
    "Each scene: what it is → why it matters → a picture-in-your-head comparison. "
    "Every image_prompt and broll_query must show the CONCRETE thing named in that scene's narration "
    "(the real object, place or action), never a generic or mood-only picture.")


def _schema():
    return (SCRIPT_SCHEMA.replace("{ratio}", str(int(CFG["min_diagram_ratio"] * 100)))
            .replace("{units}", CFG.get("units_rule", "")))


def write_script(idea):
    return llm_json(
        f"You write scripts for a faceless educational YouTube channel called {CFG['channel_name']}. "
        f"{CFG['channel_brief']}\nWrite in {CFG['language']}. Voice: {CFG['tone']}. Rules: {CFG['script_rules']}"
        f"\nSEO and packaging rules: {CFG.get('seo_rules', '')}",
        f"Title idea: {idea['title']}\nAngle: {idea['angle']}\n"
        + (f"Competing videos already on YouTube (do NOT copy their structure; deliver our unique angle in the first "
           f"30 seconds and keep paying it off): {'; '.join(idea['competitors'])}\n" if idea.get("competitors") else "")
        +
        f"This is a LONG-FORM video: write AT LEAST {CFG['target_words']} words of narration in about "
        f"{max(20, CFG['target_words'] // 50)} scenes (2-3 sentences each). Go deep: mechanisms, concrete "
        f"examples, comparisons, history and surprising details. The FIRST 3 scenes must work on their own "
        f"as a ~50 second Short (strong hook + payoff). Also plan {CFG.get('shorts_per_video', 3)} Shorts in "
        f"'shorts': each a range of consecutive scenes (0-based, inclusive) that is fully understandable WITHOUT "
        f"the rest of the video, 30-55 seconds of narration (about 75-135 words), starting with a surprising line, "
        f"ranges must not overlap, the first one is scenes 0-2. The LAST scene ends by asking the pinned_comment "
        f"question aloud and telling viewers to vote in the comments by writing just 1 or 2.\n"
        + poll_context() + f"{_schema()}",
        required=("title", "description", "tags", "thumbnail_concepts", "scenes"))


def word_count(script):
    return sum(len(s.get("narration", "").split()) for s in script.get("scenes", []))


def ensure_length(script):
    """Senaryo hedefin belirgin altında kalırsa (ör. zayıf model yazdıysa) genişletir."""
    target = CFG["target_words"]
    for attempt in range(CFG.get("max_expand_rounds", 2)):
        n = word_count(script)
        if n >= target * CFG.get("min_length_ratio", 0.85):
            return script
        log(f"Senaryo kısa ({n}/{target} kelime) → genişletiliyor ({attempt+1})")
        longer = revise_script(script, (
            f"- The script is TOO SHORT: {n} words. Expand it to about {target} words "
            f"(about {max(20, target // 50)} scenes). Keep the strong existing scenes and the hook. "
            "Add new scenes that go deeper: how each part works step by step, concrete real-world examples, "
            "comparisons, scale, history and surprising details. Every new scene needs narration, visual, "
            "broll_query and image_prompt. Keep the 'shorts' plan valid for the new scene numbers."))
        if word_count(longer) > n:
            script = longer
    log(f"Senaryo uzunluğu: {word_count(script)} kelime")
    return script


def revise_script(script, notes):
    return llm_json(
        f"You revise YouTube scripts. Language: {CFG['language']}. Rules: {CFG['script_rules']}\n"
        f"SEO and packaging rules: {CFG.get('seo_rules', '')}",
        f"Problems to fix:\n{notes}\n\nCurrent script JSON:\n{json.dumps(script, ensure_ascii=False)}\n\n"
        "Fix every problem. Replace wrong facts with the correct ones given; remove or soften anything "
        f"uncertain. Keep the same structure and length. {_schema()}",
        required=("title", "description", "tags", "thumbnail_concepts", "scenes"))


PLAIN_EDITOR = (
    "You are the final 'make it crystal clear' editor of a YouTube channel for the WIDEST possible audience: "
    "people with no science or history background, tired, half-listening, maybe with weak reading skills. "
    "If any sentence makes them think 'what does that mean?', they leave. Rewrite the narration so that "
    "EVERYONE understands EVERY sentence the first time they hear it.\n"
    "RULES:\n"
    "1. Every term, unit, name, place, material or concept is explained the FIRST time it appears, in the same "
    "or the next sentence, with everyday words (\"the hearth, the very bottom of the furnace where the melted "
    "iron collects\"). If a term is not needed, replace it with a simple word.\n"
    "2. Short sentences, mostly 8-15 words. One idea per sentence. Concrete, everyday words. No abstract or "
    "academic phrasing, no passive voice where active works.\n"
    "3. Every number, temperature, size, weight, speed or time gets a comparison anyone can picture "
    "(\"about three thousand degrees, hotter than lava from a volcano\"). {units}\n"
    "4. STORY FLOW: the whole narration must read like ONE continuous story told by a friend, not separate "
    "facts. Each scene must connect to the previous one with a clear link (so, because, but, that's why, "
    "which means, and here is the problem). The viewer must always know WHY they are hearing this now. "
    "Before a new chapter, say in one simple sentence what question we answer next.\n"
    "5. Visual match: each scene's image_prompt and broll_query must show EXACTLY the concrete thing the "
    "narration of that scene talks about. If you change what a scene says, update its visuals to match.\n"
    "6. Keep all facts, numbers and claims the same (do not add new facts or make anything up), keep the "
    "same number of scenes in the same order, keep the hook strong and the length about the same "
    "(explanations can add a few words). Keep title, description, tags, chapters, shorts and other fields "
    "unless a field contradicts the new narration."
)


def plain_pass(script, extra=""):
    """Anlatımı herkesin anlayacağı dile çevirir: terim açıklaması, kısa cümle, günlük karşılaştırma,
    sahneler arası bağ, görsel-anlatım uyumu. Başarısız olursa senaryo aynen kalır."""
    try:
        r = llm_json(
            PLAIN_EDITOR.replace("{units}", CFG.get("units_rule", "")) + "\n" + CFG.get("plain_rules", "")
            + f"\nWrite in {CFG['language']}.",
            (f"Specific problems an editor found (fix all of them):\n{extra}\n\n" if extra else "")
            + f"Script JSON:\n{json.dumps(script, ensure_ascii=False)}\n\n{_schema()}",
            required=("scenes",))
        new = r.get("scenes") if isinstance(r, dict) else None
        if not isinstance(new, list) or len(new) != len(script["scenes"]) or \
                not all(isinstance(s, dict) and str(s.get("narration", "")).strip() for s in new):
            log("Sadeleştirme sahne yapısını bozdu → eski metin kalıyor")
            return script
        # yalnız anlatım ve görsel tarifleri alınır; başlık, Shorts, küçük resim vb. yapı aynen korunur
        out = json.loads(json.dumps(script))
        for old_s, new_s in zip(out["scenes"], new):
            old_s["narration"] = str(new_s["narration"]).strip()
            for k in ("image_prompt", "broll_query"):
                if isinstance(new_s.get(k), str) and new_s[k].strip():
                    old_s[k] = new_s[k].strip()
        r = out
        log(f"Anlatım sadeleştirildi ({word_count(script)} → {word_count(r)} kelime)")
        return r
    except Exception as e:
        log(f"Sadeleştirme yapılamadı: {str(e)[:150]}")
        return script


# ---------------------------------------------------------------- 3. bilgi doğrulama (Google aramalı)
WIKI_HEADERS = {"User-Agent": "OtoYT/1.0 (educational fact-checking; https://cutaw4y.github.io)"}


def _wiki(lang):
    return f"https://{lang}.wikipedia.org/w/api.php"


def wikipedia_sources(script, max_pages=4, chars=6000):
    """Konuyla ilgili Wikipedia makalelerini çeker (ücretsiz, kotasız). Kanalın dillerinde arar:
    ör. Türkçe kanal için önce tr.wikipedia (başlıkla), sonra en.wikipedia (İngilizce anahtar kelimelerle)."""
    langs = CFG.get("wiki_langs", ["en"])
    en_q = [s.get("broll_query", "") for s in script["scenes"][:3]]
    found = []  # (dil, başlık)
    for lang in langs:
        queries = ([script["title"]] + ([] if lang != "en" else en_q)) if lang == langs[0] else \
                  ([script.get("search_en") or script["title"]] + en_q if lang == "en" else [script["title"]])
        for q in queries:
            if not q or len(found) >= max_pages:
                continue
            try:
                r = requests.get(_wiki(lang), headers=WIKI_HEADERS, timeout=30, params={
                    "action": "query", "list": "search", "srsearch": q[:200], "srlimit": 2, "format": "json"})
                for h in r.json().get("query", {}).get("search", []):
                    if (lang, h["title"]) not in found:
                        found.append((lang, h["title"]))
            except Exception as e:
                log(f"Wikipedia ({lang}) arama hatası: {e}")
    sources = []
    for lang, t in found[:max_pages]:
        try:
            r = requests.get(_wiki(lang), headers=WIKI_HEADERS, timeout=30, params={
                "action": "query", "prop": "extracts", "explaintext": 1, "titles": t,
                "format": "json", "redirects": 1})
            for pg in r.json().get("query", {}).get("pages", {}).values():
                txt = (pg.get("extract") or "").strip()
                if txt:
                    sources.append(f"### [{lang}] {pg.get('title')}\n{txt[:chars]}")
        except Exception as e:
            log(f"Wikipedia okuma hatası: {e}")
    return sources


def _claims_prompt(script, how):
    text = "\n".join(s["narration"] for s in script["scenes"])
    return ("You are a rigorous fact-checker for an educational YouTube video. Extract every specific, checkable "
            "factual claim (numbers, dates, how-something-works statements, superlatives) from the script below, "
            f"max 15. {how} {CFG.get('factcheck_note', '')} Be strict.\n\n"
            f"Title: {script['title']}\nScript:\n{text}\n\n"
            'Return ONLY JSON, no other text: {"claims":[{"claim":"...","verdict":"correct|wrong|uncertain",'
            '"correction":"correct version if wrong/uncertain"}]}')


_search_ok = True


def _claims(data):
    """Model bazen {"claims":[...]} yerine doğrudan liste döndürüyor: ikisini de kabul et."""
    if isinstance(data, list):
        return [c for c in data if isinstance(c, dict)]
    c = data.get("claims", []) if isinstance(data, dict) else []
    return [x for x in c if isinstance(x, dict)] if isinstance(c, list) else []


def fact_check(script):
    """Önce Google aramalı Gemini; kota/izin yoksa Wikipedia kaynaklarıyla doğrulama."""
    global _search_ok
    if CFG.get("use_google_search", True) and _search_ok:
        try:
            out = ai.gemini(_claims_prompt(script, "Verify EACH one with Google Search against reliable sources."),
                            models=CFG["factcheck_models"], search=True, temperature=0.1, tries=2)
            log("Doğrulama kaynağı: Google araması")
            return _claims(ai.extract_json(out))
        except Exception as e:
            _search_ok = False  # bu çalışmada tekrar deneyip zaman kaybetme
            log(f"Google aramalı doğrulama kullanılamadı ({str(e)[:120]}) → Wikipedia'ya geçiliyor")
    sources = wikipedia_sources(script)
    log(f"Doğrulama kaynağı: Wikipedia ({len(sources)} makale)")
    how = ("Verify EACH claim against the SOURCES below (Wikipedia extracts). A claim is 'correct' if the sources "
           "support it OR it is basic, universally accepted science/engineering knowledge. Mark 'wrong' if it "
           "contradicts the sources or established science. Mark 'uncertain' if it is a specific number, record or "
           "date the sources don't confirm.\n\nSOURCES:\n" + ("\n\n".join(sources) or "(no sources found)"))
    data = ai.extract_json(ai.gemini(_claims_prompt(script, how), models=CFG["judge_models"],
                                     json_mode=True, temperature=0.1))
    return _claims(data)


def verify_loop(script):
    for rnd in range(CFG["max_factcheck_rounds"] + 1):
        try:
            claims = fact_check(script)
        except Exception as e:
            if CFG.get("require_web_factcheck", True):
                raise
            log(f"Doğrulama atlandı: {e}")
            return script, True
        wrong = [c for c in claims if c.get("verdict") == "wrong"]
        bad = [c for c in claims if c.get("verdict") != "correct"]
        log(f"Doğrulama turu {rnd+1}: {len(claims)} iddia, {len(wrong)} yanlış, {len(bad) - len(wrong)} belirsiz")
        if not bad:
            return script, True
        if rnd == CFG["max_factcheck_rounds"]:
            # son turda yalnızca kesin yanlış kaldıysa iptal; belirsizler önceki turlarda yumuşatıldı
            return script, not wrong
        notes = "\n".join(f"- [{c.get('verdict')}] {c.get('claim')} → "
                          f"{c.get('correction') or 'remove it or say it approximately'}" for c in bad)
        script = revise_script(script, notes)
    return script, False


# ---------------------------------------------------------------- 4. editör puanı
def quality_gate(script):
    text = "\n".join(s["narration"] for s in script["scenes"])
    r = llm_json(
        "You are a strict senior YouTube editor. Score honestly; average AI scripts deserve 5-6.",
        f"Audience: {CFG['audience']}\nTitle: {script['title']}\nScript:\n{text}\n\n"
        "Score 1-10: originality, hook (first 15 seconds), clarity (would a curious non-expert truly "
        "understand?), simplicity (could a 12-year-old with no background follow EVERY sentence on first "
        "listen? any term, unit, name or concept used without a plain explanation, any long or abstract "
        "sentence, any big number without an everyday comparison lowers it hard), flow (does it feel like ONE "
        "connected story where each scene clearly follows from the last, or like separate facts glued together?), "
        "value_density, audience_fit. "
        "In 'problems', list EVERY unexplained term or hard sentence by quoting it, with the simpler fix. "
        "Also write 'problems_tr': the same problems summarized in one or two short Turkish sentences. "
        "Return JSON: {\"originality\":0,\"hook\":0,\"clarity\":0,\"simplicity\":0,\"flow\":0,"
        "\"value_density\":0,\"audience_fit\":0,\"problems\":[\"...\"],\"problems_tr\":\"...\"}",
        required=("problems", "simplicity", "flow", "clarity"), judge=True)
    keys = ("originality", "hook", "clarity", "simplicity", "flow", "value_density", "audience_fit")
    scores = [float(r.get(k, 0)) for k in keys]
    plain_ok = float(r.get("simplicity", 0)) >= CFG.get("min_plain_score", 7) and \
        float(r.get("flow", 0)) >= CFG.get("min_plain_score", 7)
    return min(scores) >= CFG["min_quality_score"] and plain_ok, r


# ---------------------------------------------------------------- 5. ses (Kokoro, ücretsiz)
def _tts(scenes, workdir):
    """Kanalın ses sağlayıcısı: kokoro (ücretsiz, İngilizce) veya gemini (Türkçe dahil, doğal)."""
    v = dict(CFG["voice"])
    if v.get("provider") == "gemini" and ai.vertex_on():  # Cloud kredisiyle aynı Gemini sesi (Cloud TTS)
        v["models"] = ["vertex:gemini-2.5-flash-tts"] + list(v.get("models") or [])
        ai.TTS_LANG = "tr-TR" if CFG.get("language_code") == "tr" else "en-US"
    if v.get("provider") == "gemini" and v.get("fallback"):
        try:
            return voice.narrate_gemini(scenes, workdir, voice=v.get("name", "Charon"), style=v.get("style"),
                                        models=v.get("models"), scene_gap=v.get("scene_gap", 0.45),
                                        sentence_gap=v.get("sentence_gap", 0.0),
                                        scenes_per_request=v.get("scenes_per_request", 6),
                                        max_chars=v.get("max_chars", 1800), lang=CFG.get("language_code", "en"))
        except Exception as e:  # Gemini sesi alınamazsa video yine çıksın: yedek ses
            fb = v["fallback"]
            log(f"Gemini sesi alınamadı ({str(e)[:120]}) → yedek ses ({fb.get('name')})")
            extra = {"synth": voice.synth_edge} if fb.get("provider") == "edge" else {}
            return voice.narrate(scenes, workdir, voice=fb["name"], lang=fb.get("lang", "a"), speed=fb.get("speed", 1.0),
                                 sentence_gap=fb.get("sentence_gap", 0.28), scene_gap=fb.get("scene_gap", 0.45),
                                 align_lang=CFG.get("language_code", "en"), **extra)
    if v.get("provider") == "gemini":
        return voice.narrate_gemini(scenes, workdir, voice=v.get("name", "Charon"), style=v.get("style"),
                                    models=v.get("models"), scene_gap=v.get("scene_gap", 0.45),
                                    sentence_gap=v.get("sentence_gap", 0.0),
                                    scenes_per_request=v.get("scenes_per_request", 6),
                                        max_chars=v.get("max_chars", 1800), lang=CFG.get("language_code", "en"))
    return voice.narrate(scenes, workdir, voice=v["name"], lang=v["lang"], speed=v.get("speed", 1.0),
                         sentence_gap=v.get("sentence_gap", 0.28), scene_gap=v.get("scene_gap", 0.45),
                         align_lang=CFG.get("language_code", "en"))


def narrate(script, workdir):
    """Her sahne için kare-hizalı ses dosyası + sahneye göre kelime zamanları üretir."""
    files, durs, words = _tts(script["scenes"], workdir)
    off = 0.0
    for i, (sc, f, d) in enumerate(zip(script["scenes"], files, durs)):
        frames = int(math.ceil(d * media.FPS))
        sc["frames"] = frames
        sc["duration"] = frames / media.FPS
        sc["voice"] = media.to_wav(f, workdir / f"vo_al_{i:02d}.wav", frames=frames)
        sc["words"] = [{"word": w["word"], "start": w["start"] - off, "end": w["end"] - off}
                       for w in words if off - 0.005 <= w["start"] < off + d - 0.005]
        off += d
    return script


def narrate_line(text, workdir, tag):
    """Tek bir cümleyi (ör. kapanış cümlesi) seslendirir: (dosya, süre, kelimeler)."""
    d = Path(workdir) / tag
    d.mkdir(exist_ok=True)
    files, durs, words = _tts([{"narration": text}], d)
    return files[0], durs[0], words


# ---------------------------------------------------------------- 6. görseller
def _download(url, out):
    with requests.get(url, stream=True, timeout=180) as d:
        d.raise_for_status()
        with open(out, "wb") as fh:
            for c in d.iter_content(1 << 20):
                fh.write(c)
    return out


_pixabay_cache = {}


def fetch_pixabay(query, out, ledger):
    key = os.getenv("PIXABAY_API_KEY")
    if not key or not query:
        return None
    if query not in _pixabay_cache:  # aynı aramayı tekrar yapma (Pixabay kuralı: sonuçları önbellekle)
        try:
            r = requests.get("https://pixabay.com/api/videos/",
                             params={"key": key, "q": query[:100], "video_type": "film", "min_width": 1280,
                                     "safesearch": "true", "per_page": 50}, timeout=30)
            r.raise_for_status()
            _pixabay_cache[query] = r.json().get("hits", [])
        except Exception as e:
            log(f"Pixabay hatası ({query}): {e}")
            return None
    hits = [h for h in _pixabay_cache[query] if not ledger.used(f"x{h['id']}")]
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
_gem_imgs = 0


def gen_image(prompt, out, fallback_query=None, ledger=None):
    """AI diyagram (Cloudflare). Günlük ücretsiz kota biterse Pexels fotoğrafına düşer."""
    global _quota_hit
    out = Path(out).with_suffix(".jpg")
    global _gem_imgs
    if CFG.get("scene_images") == "imagen" and ai.vertex_on() and _gem_imgs < CFG.get("max_gemini_scene_images", 45):
        try:  # fiyat/performans: Imagen 4 Fast (~0,02 $)
            got = ai.imagen_image(prompt + ". Cinematic, photographic quality, no text, no letters, no watermark.", out)
            _gem_imgs += 1
            return got
        except Exception as e:
            log(f"Imagen sahne görseli üretilemedi, Gemini/Cloudflare denenecek: {str(e)[:150]}")
    if (CFG.get("scene_images") in ("gemini", "imagen") and ai.vertex_on()
            and _gem_imgs < CFG.get("max_gemini_scene_images", 45)):
        # yüksek kaliteli sahne görseli (Google Cloud kredisinden, ~0,04 $/görsel); olmazsa Cloudflare'e düşer
        try:
            got = ai.gemini_image(prompt + ". Cinematic, photographic quality, wide 16:9 composition, no text, "
                                  "no letters, no watermark.", out, models=(), quick=True)
            _gem_imgs += 1
            return got
        except Exception as e:
            log(f"Gemini sahne görseli üretilemedi, Cloudflare denenecek: {str(e)[:150]}")
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
    if ai.vertex_on():  # son çare: sınırı aşsa da Gemini (video yarıda kalmasın)
        try:
            return ai.gemini_image(prompt + ". Cinematic, wide 16:9, no text.", out, models=())
        except Exception as e:
            log(f"Son çare Gemini görseli de olmadı: {str(e)[:120]}")
    raise RuntimeError("Ne AI görsel ne yedek fotoğraf bulunabildi")


def _clip_start(clip, frames):
    try:
        cd = media.duration(clip)
        room = cd - frames / media.FPS - 0.3
        return random.uniform(0.2, room) if room > 0.4 else 0.0
    except Exception:
        return 0.0


_CLIP_CHECKS = 0
VIS_STATS = {"rejected": 0, "planned": 0, "scenes": 0, "real": 0}   # bildirim için
REAL_CREDITS = []      # kullanılan gerçek fotoğrafların yazar/lisans satırları (açıklamaya eklenir)
_REAL_USED = set()
_OK_LIC = ("public domain", "pd", "cc0", "cc by", "cc-by", "attribution")


def fetch_real_photo(subject, out, context=""):
    """Anlatılan gerçek yerin/nesnenin GERÇEK fotoğrafı: Wikimedia Commons (serbest lisanslı; telifli web
    görselleri asla). Lisansı kullanıma uygun, yeterince büyük ve yapay zekâ kontrolünde konuyla ilgili olanı seçer."""
    if not subject or not CFG.get("real_photos", True):
        return None
    pages, seen = [], set()
    for q in (f"{subject} filetype:bitmap", f"{subject} photograph filetype:bitmap"):
        try:
            r = requests.get("https://commons.wikimedia.org/w/api.php", headers=WIKI_HEADERS, timeout=30, params={
                "action": "query", "format": "json", "generator": "search", "gsrsearch": q,
                "gsrnamespace": 6, "gsrlimit": 25, "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata",
                "iiurlwidth": 1920})
            for pg in sorted((r.json().get("query") or {}).get("pages", {}).values(), key=lambda x: x.get("index", 99)):
                if pg.get("title") not in seen:
                    seen.add(pg.get("title"))
                    pages.append(pg)
        except Exception as e:
            log(f"Gerçek fotoğraf aranamadı ({subject}): {str(e)[:100]}")
    skip = ("map", "harita", "model", "diagram", "logo", "flag", "coat of arms", "page", "volume", "cover",
            "chart", "plan", "scheme", "icon", "signature", "draft", "svg")
    pages = [pg for pg in pages if not any(s in pg.get("title", "").lower() for s in skip)]
    checks = 0
    import html as _h
    for pg in pages:
        ii = (pg.get("imageinfo") or [{}])[0]
        title = pg.get("title", "")
        if title in _REAL_USED or ii.get("mime") not in ("image/jpeg", "image/png") or ii.get("width", 0) < 1000:
            continue
        md = ii.get("extmetadata") or {}
        lic = (md.get("LicenseShortName", {}).get("value") or md.get("License", {}).get("value") or "").lower()
        if not any(k in lic for k in _OK_LIC) or "nc" in lic.split() or "-nc" in lic or "nd" in lic.split() or "-nd" in lic:
            continue
        try:  # Wikimedia, kimliksiz (User-Agent'sız) indirmeyi 403 ile reddediyor
            with requests.get(ii.get("thumburl") or ii["url"], headers=WIKI_HEADERS, stream=True, timeout=120) as d:
                d.raise_for_status()
                with open(out, "wb") as fh:
                    for c in d.iter_content(1 << 20):
                        fh.write(c)
        except Exception as e:
            if os.getenv("DEBUG_REAL"):
                print(f"::notice title=indirme::{title[:50]} {str(e)[:120]}", flush=True)
            continue
        checks += 1
        ok_ = image_matches(out, f"{subject}. {context}")
        if os.getenv("DEBUG_REAL"):
            print(f"::notice title=aday {checks}::{title[:60]} {ii.get('width')} {lic} → {ok_}", flush=True)
        if not ok_:
            if checks >= 4:
                return None
            continue
        artist = re.sub(r"<[^>]+>", "", _h.unescape(md.get("Artist", {}).get("value", "") or "")).strip()[:60] or "Wikimedia Commons"
        _REAL_USED.add(title)
        REAL_CREDITS.append(f"{title.replace('File:', '')[:70]} — {artist}, {md.get('LicenseShortName', {}).get('value', '')} (Wikimedia Commons)")
        VIS_STATS["real"] += 1
        return out
    return fetch_openverse(subject, out, context) if checks < 4 else None


def fetch_openverse(subject, out, context=""):
    """Commons'ta yoksa: Openverse (Flickr, müzeler, arşivler; 800+ milyon serbest lisanslı görsel).
    Yalnız ticari kullanıma ve düzenlemeye izin veren lisanslar (para kazanan kanal, yakınlaştırma/kırpma)."""
    try:
        r = requests.get("https://api.openverse.org/v1/images/", headers=WIKI_HEADERS, timeout=(10, 25), params={
            "q": subject, "license_type": "commercial,modification", "page_size": 20, "mature": "false"})
        res = r.json().get("results", []) if r.ok else []
    except Exception as e:
        log(f"Openverse aranamadı ({subject}): {str(e)[:100]}")
        return None
    checks = 0
    for it in res:
        url = it.get("url") or ""
        if url in _REAL_USED or (it.get("width") or 0) < 1000 or any(
                s in (it.get("title") or "").lower() for s in ("map", "logo", "diagram", "poster", "model")):
            continue
        try:
            with requests.get(url, headers=WIKI_HEADERS, stream=True, timeout=120) as d:
                d.raise_for_status()
                with open(out, "wb") as fh:
                    for c in d.iter_content(1 << 20):
                        fh.write(c)
            from PIL import Image
            Image.open(out).convert("RGB").save(out, "JPEG", quality=92)
        except Exception:
            continue
        checks += 1
        if not image_matches(out, f"{subject}. {context}"):
            if checks >= 3:
                return None
            continue
        _REAL_USED.add(url)
        lic = f"{(it.get('license') or '').upper()} {it.get('license_version') or ''}".strip()
        REAL_CREDITS.append(f"{(it.get('title') or 'Photo')[:60]} — {(it.get('creator') or 'unknown')[:40]}, "
                            f"CC {lic} ({it.get('source') or 'Openverse'})")
        VIS_STATS["real"] += 1
        return out
    return None


_VIS_T0 = None
_LAST_IMG = None


def vis_hurry():
    """Görsel aşaması zaman bütçesini aştı mı? (Katı kontroller üretimi saatlerce uzatmasın; yayın kaçmasın.)"""
    if _VIS_T0 is None:
        return False
    late = (time.time() - _VIS_T0) / 60 > CFG.get("visual_time_budget_min", 45)
    if late and not getattr(vis_hurry, "said", False):
        vis_hurry.said = True
        log("Görsel aşaması zaman bütçesini aştı: kalan sahnelerde yalnız cümleye göre çizilen görseller (hızlı mod)")
    return late


def visual_ok(path, sentence):
    """Üretilen görsel bu cümleyi gerçekten gösteriyor mu? Kontrol yapılamazsa kabul (görsel zaten cümleye göre çizildi)."""
    try:
        from PIL import Image
        sm = Path(path).with_name(Path(path).stem + "_vchk.jpg")
        Image.open(path).convert("RGB").resize((480, 270)).save(sm, quality=80)
        lite = [m for m in CFG["judge_models"] if "lite" in m] or CFG["judge_models"][:1]
        r = ai.gemini_json("You check images for a documentary. A viewer must instantly connect the image to the "
                           "sentence being spoken. Be strict.",
                           f"Spoken sentence: {sentence}\nDoes this image clearly show what the sentence talks about "
                           "(its subject, place, object or scene)? Unrelated, random, generic or confusing = no. Text or "
                           'letters in the image = no. JSON: {"match": true or false, "seen": "5 words"}',
                           images=[sm], required=("match",), models=lite, temperature=0.1)
        if not r.get("match"):
            VIS_STATS["rejected"] += 1
            log(f"   alakasız görsel yeniden çizilecek: {r.get('seen', '')}")
        return bool(r.get("match"))
    except Exception:
        return True


def image_matches(path, what):
    """Gerçek fotoğraf anlatılan şeyi gösteriyor mu? (Flash-Lite, hak dolarsa kontrol atlanır.)"""
    global _CLIP_CHECKS
    if _CLIP_CHECKS >= CFG.get("max_footage_checks", 70):
        return not CFG.get("strict_visuals")  # katı modda kontrol edilemeyen gerçek fotoğraf kullanılmaz
    _CLIP_CHECKS += 1
    try:
        from PIL import Image
        sm = Path(path).with_name(Path(path).stem + "_chk.jpg")
        Image.open(path).convert("RGB").resize((480, 270)).save(sm, quality=80)
        lite = [m for m in CFG["judge_models"] if "lite" in m] or CFG["judge_models"][:1]
        r = ai.gemini_json("You check photos for a documentary. Be strict.",
                           f"Narration: {what}\nDoes this photo clearly show this exact real place/object/event (or a "
                           "very close real view of it)? Maps, logos, tiny thumbnails, unrelated people = no. "
                           'JSON: {"match": true or false}', images=[sm], required=("match",), models=lite,
                           temperature=0.1)
        return bool(r.get("match"))
    except Exception:
        return not CFG.get("strict_visuals")


def clip_matches(clip, sc, workdir):
    """Stok videonun ortasından bir kare alıp yapay zekâya sorar: anlatılanla gerçekten ilgili mi?
    İlgisiz görüntü izleyiciyi kaçırır; emin olunamazsa (hata) kabul edilir."""
    global _CLIP_CHECKS
    if not CFG.get("check_footage", True) or _CLIP_CHECKS >= CFG.get("max_footage_checks", 160):
        return not CFG.get("strict_visuals")  # katı modda kontrol edilemeyen video kullanılmaz
    _CLIP_CHECKS += 1
    try:
        fr = Path(workdir) / (Path(clip).stem + "_chk.jpg")
        d = media.duration(clip)
        media.sh(["ffmpeg", "-y", "-ss", f"{d / 2:.2f}", "-i", clip, "-frames:v", "1", "-vf", "scale=480:-2", fr])
        lite = [m for m in CFG["judge_models"] if "lite" in m] or CFG["judge_models"][:1]
        r = ai.gemini_json("You check stock footage for an explainer video. Be strict: generic or unrelated = no.",
                     f"Narration of this scene: {sc['narration']}\nSearch query used: {sc.get('broll_query', '')}\n"
                     "Would a viewer immediately see that this frame shows the thing being talked about "
                     "(same object, place, process or a very close real-world example)? "
                     'Return JSON: {"match": true or false, "seen": "what the frame shows, 5 words"}',
                     images=[fr], required=("match",), models=lite, temperature=0.2)
        ok = bool(r.get("match"))
        if not ok:
            VIS_STATS["rejected"] += 1
            log(f"   ilgisiz görüntü elendi: {r.get('seen', '')}")
        return ok
    except Exception as e:
        if "kota" in str(e) or "429" in str(e):  # ücretsiz hak bitti: kalan sahnelerde kontrolü bırak,
            _CLIP_CHECKS = 10 ** 6                 # çeviri ve küçük resim hakemine hak kalsın
            log("Görüntü kontrolü durduruldu (Gemini hakkı dolu)")
        return not CFG.get("strict_visuals")


def plan_beats(script):
    """Her cümle için ayrı görsel planı: o cümlede adı geçen somut şey (nesne, yer, belge, olay).
    Görüntü, cümle söylenirken değişir; böylece "Babil kil tabletleri" denince ekranda kil tablet olur."""
    scenes = script["scenes"]
    for i in range(0, len(scenes), 8):
        chunk = list(range(i, min(i + 8, len(scenes))))
        lines = []
        for si in chunk:
            for k, sent in enumerate(voice.split_sentences(scenes[si]["narration"])):
                lines.append(f"{si}.{k}: {sent}")
        try:
            r = llm_json(
                "You are a documentary picture editor. For EVERY numbered sentence pick the visual shown while it "
                "is spoken.",
                f"Video: {script['title']}\nSentences (scene.sentence: text):\n" + "\n".join(lines) + "\n\n"
                "For each sentence return: query = 2-4 word ENGLISH stock-footage search for the CONCRETE thing named "
                "in that sentence (the object, artifact, place, document, animal, action; e.g. 'babylonian clay "
                "tablet', 'ancient cuneiform', 'midwife newborn baby'); image = ENGLISH illustration prompt showing "
                "exactly that thing. If the sentence names nothing concrete, show the most concrete thing it refers "
                "to from the surrounding sentences, never a generic mood shot. Never show faces of prophets or holy "
                "figures, no gore. real = if the sentence is about a SPECIFIC real, named place, structure, object, "
                "document, ship, machine or event that real photos of exist, its exact name for a Wikimedia Commons "
                "search (e.g. 'Dyatlov Pass', 'Göbekli Tepe', 'Hoover Dam spillway', 'Tacoma Narrows Bridge collapse'), "
                "otherwise empty string. "
                'JSON: {"beats":[{"id":"3.2","query":"...","image":"...","real":"..."}]}', required=("beats",))
        except Exception as e:
            log(f"Cümle görselleri planlanamadı (sahne {i + 1}-{chunk[-1] + 1}): {str(e)[:150]}")
            continue
        got = {str(b.get("id", "")).strip(): b for b in r.get("beats", []) if isinstance(b, dict)}
        for si in chunk:
            sents = voice.split_sentences(scenes[si]["narration"])
            bs = [got.get(f"{si}.{k}") for k in range(len(sents))]
            if all(b and b.get("query") and b.get("image") for b in bs):
                scenes[si]["beat_plan"] = [{"text": t, "query": b["query"], "image": b["image"],
                                            "real": str(b.get("real") or "").strip()}
                                           for t, b in zip(sents, bs)]
    n = sum(1 for sc in scenes if sc.get("beat_plan"))
    log(f"Cümle cümle görsel planı: {n}/{len(scenes)} sahne")
    VIS_STATS["planned"], VIS_STATS["scenes"] = n, len(scenes)


def beat_shots(sc, min_sec=2.0, max_sec=6.5):
    """Sahneyi cümle sınırlarından çekimlere böler. Dönüş: [(kare_sayısı, cümle_no)] (toplam = sahne karesi)
    ya da None (kelime sayısı tutmazsa eski yönteme dönülür)."""
    plan, ws, total = sc.get("beat_plan"), sc.get("words") or [], sc["frames"]
    if not plan:
        return None
    counts = [len(b["text"].split()) for b in plan]
    if sum(counts) != len(ws):
        return None
    fps, starts, k = media.FPS, [], 0
    for c in counts:
        starts.append(max(0.0, ws[k]["start"] - 0.12))  # görüntü kelimeden bir an önce gelsin
        k += c
    cuts = [0] + [min(total, int(round(t * fps))) for t in starts[1:]] + [total]
    segs = [(cuts[j], cuts[j + 1], j) for j in range(len(plan)) if cuts[j + 1] > cuts[j]]
    merged = []  # çok kısa cümle: bir önceki görüntü devam etsin (göz yorulmasın)
    for a, b, j in segs:
        if merged and (b - a) < min_sec * fps:
            pa, _, pj = merged[-1]
            merged[-1] = (pa, b, pj)
        else:
            merged.append((a, b, j))
    if len(merged) > 1 and (merged[0][1] - merged[0][0]) < min_sec * fps:
        a0, _, j0 = merged.pop(0)
        merged[0] = (a0, merged[0][1], merged[0][2])
    out = []
    for a, b, j in merged:  # uzun cümle: aynı konunun iki-üç farklı görüntüsü
        n = max(1, int(math.ceil((b - a) / (max_sec * fps))))
        base = (b - a) // n
        parts = [base] * n
        parts[-1] += (b - a) - base * n
        out += [(p, j) for p in parts]
    return out


def build_scene_videos(script, workdir, ledger):
    """Her sahneyi ~4-5 sn'lik çekimlere böler (profesyonel tempo), sahne videosunu döndürür."""
    style = CFG["visual_style"]
    scenes = script["scenes"]
    need = int(len(scenes) * CFG["min_diagram_ratio"] + 0.999)
    have = sum(1 for s in scenes if s.get("visual") == "diagram")
    for s in sorted(scenes, key=lambda s: -s.get("duration", 0)):
        if have >= need:
            break
        if s.get("visual") != "diagram":
            s["visual"], have = "diagram", have + 1

    hook_n = CFG.get("hook_scenes", 2)
    for s in scenes[:hook_n]:
        s["visual"] = "diagram"  # ilk saniyeler: söylenenin birebir görseli
    shot_sec = CFG.get("shot_seconds", 4.5)
    global _VIS_T0
    _VIS_T0 = time.time()
    if CFG.get("visual_per_sentence", True):
        plan_beats(script)
    img_budget = [CFG.get("max_sentence_images", 60)]
    for i, sc in enumerate(scenes):
        bshots = beat_shots(sc)
        if bshots:
            build_beat_scene(sc, i, bshots, workdir, ledger, style, i < hook_n, img_budget)
            continue
        shots = media.split_frames(sc["frames"], shot_sec)
        log(f"Sahne {i+1}/{len(scenes)} ({sc.get('visual')}, {sc['duration']:.1f} sn, {len(shots)} çekim)")
        q = sc.get("broll_query") or CFG["fallback_broll"]
        img = None
        if sc.get("visual") == "diagram" or CFG.get("strict_visuals"):  # katı mod: yedek görsel hazır olsun
            img = gen_image(f"{sc.get('image_prompt') or sc['narration']}. Style: {style}",
                            workdir / f"img_{i:02d}.jpg", q, ledger)
            if CFG.get("strict_visuals") and not visual_ok(img, sc["narration"][:400]):
                img = gen_image(f"Show literally what this narration describes: \"{sc['narration'][:300]}\". "
                                f"Style: {style}", workdir / f"img_{i:02d}.jpg", q, ledger)
        files = []
        for k, fr in enumerate(shots):
            out = workdir / f"shot_{i:02d}_{k:02d}.mp4"
            # diyagram sahnesi: diyagram (geniş) → stok → diyagram (detay) → stok ...
            use_still = img is not None and (k % 2 == 0 or i < hook_n)
            clip = None
            if not use_still:
                # konuyla ilgili stok video: 3 aday dene; genel "yedek" görüntü kullanma, yoksa AI görsel
                for t in range(3):
                    clip = fetch_stock(q, workdir / f"raw_{i:02d}_{k:02d}_{t}.mp4", ledger)
                    if not clip or clip_matches(clip, sc, workdir):
                        break
                    clip = None
            if clip:
                media.render_footage(clip, fr, out, start=_clip_start(clip, fr))
            else:
                if img is None:
                    img = gen_image(f"{sc.get('image_prompt') or sc['narration']}. Style: {style}",
                                    workdir / f"img_{i:02d}.jpg", q, ledger)
                if k == 0:
                    move = random.choice(["in", "out"])
                else:
                    move = random.choice(["detail", "detail", "left", "right"])
                media.render_still(img, fr, out, move=move,
                                   focus=(random.uniform(0.3, 0.7), random.uniform(0.3, 0.7)))
            files.append(out)
        sc["video"] = media.concat(files, workdir / f"scene_{i:02d}.mp4", workdir, f"scene_{i:02d}")
    return script


def build_beat_scene(sc, i, bshots, workdir, ledger, style, hook, img_budget):
    """Cümleye bağlı çekimler: her görüntü o an söylenen şeyi gösterir."""
    plan = sc["beat_plan"]
    log(f"Sahne {i+1} ({sc.get('visual')}, {sc['duration']:.1f} sn, {len(bshots)} çekim, cümleye bağlı)")
    imgs, files, prev_j, part = {}, [], None, 0

    def beat_image(j):
        if j in imgs:
            return imgs[j]
        b = plan[j]
        if b.get("real"):  # gerçeklik önce: anlatılan gerçek yerin/nesnenin serbest lisanslı gerçek fotoğrafı
            got = fetch_real_photo(b["real"], workdir / f"img_{i:02d}_b{j:02d}.jpg", b["text"])
            if got:
                imgs[j] = got
                return got
        strict = CFG.get("strict_visuals")
        if strict and not vis_hurry():  # gerçek stok fotoğraf, ancak cümleyi gerçekten gösteriyorsa
            p = workdir / f"img_{i:02d}_b{j:02d}_s.jpg"
            if fetch_stock_photo(b["query"], p, ledger) and image_matches(p, b["text"]):
                imgs[j] = p
                return p
        if img_budget[0] > 0:
            img_budget[0] -= 1
            p = workdir / f"img_{i:02d}_b{j:02d}.jpg"
            try:
                imgs[j] = gen_image(f"{b['image']}. Style: {style}", p, None if strict else b["query"], ledger)
            except Exception:
                prev = [v for v in imgs.values() if v]
                if prev:
                    imgs[j] = prev[-1]
                elif _LAST_IMG and Path(_LAST_IMG).exists():
                    imgs[j] = _LAST_IMG
                else:
                    raise
                return imgs[j]
            if strict and not vis_hurry() and not visual_ok(imgs[j], b["text"]):  # alakasız çıktıysa cümleyi birebir çizdirerek bir kez daha
                try:
                    imgs[j] = gen_image(f"Show literally and only what this sentence describes: \"{b['text']}\". "
                                        f"{b['image']}. Style: {style}", p, None, ledger)
                except Exception:
                    pass
        elif strict:  # bütçe bitti: alakasız stok yerine bu sahnenin önceki (doğrulanmış) görseli farklı açıyla
            prev = [v for v in imgs.values() if v]
            imgs[j] = prev[-1] if prev else gen_image(f"{b['image']}. Style: {style}",
                                                     workdir / f"img_{i:02d}_b{j:02d}.jpg", None, ledger)
        else:  # AI görsel bütçesi bitti: aynı konunun stok fotoğrafı
            p = workdir / f"img_{i:02d}_b{j:02d}.jpg"
            imgs[j] = fetch_stock_photo(b["query"], p, ledger) or gen_image(
                f"{b['image']}. Style: {style}", p, b["query"], ledger)
        return imgs[j]

    for k, (fr, j) in enumerate(bshots):
        part = part + 1 if j == prev_j else 0
        prev_j = j
        b = plan[j]
        out = workdir / f"shot_{i:02d}_{k:02d}.mp4"
        # diyagram sahnesi: önce cümlenin görseli, uzun cümlede ikinci çekim stok; stok sahne: tersi
        want_still = hook or (sc.get("visual") == "diagram") == (part % 2 == 0)
        if b.get("real") and part == 0:
            want_still = True  # cümle gerçek bir yerden/nesneden bahsediyorsa önce onun gerçek fotoğrafı
        if CFG.get("strict_visuals"):
            # katı mod (Furkan: gerçek video/fotoğraf önce): cümle gerçek bir yerden bahsediyorsa önce onun gerçek
            # fotoğrafı; değilse önce konuyla birebir ilgili gerçek video (yapay zekâ kontrolünden geçerse)
            want_still = bool(b.get("real") and part == 0) or vis_hurry()
        clip = None
        if not want_still:
            for t in range(1 if CFG.get("strict_visuals") else 3):
                clip = fetch_stock(b["query"], workdir / f"raw_{i:02d}_{k:02d}_{t}.mp4", ledger)
                if not clip or clip_matches(clip, {"narration": b["text"], "broll_query": b["query"]}, workdir):
                    break
                Path(clip).unlink(missing_ok=True)  # elenen video diski doldurmasın
                clip = None
        host = CFG.get("host_image")
        if host and k == 0 and (i == 0 or sc.get("chapter")) and (ASSETS / host).exists():
            # anlatıcı "Gölge": açılışta ve her bölüm başında, anlatım sürerken ekranda (gerçek hareketli Veo klipleri)
            clips = sorted((ASSETS / host).parent.glob("clips/[wlp]*.mp4"))
            if clips:
                hc = clips[(i // 2) % len(clips)]
                media.render_footage(hc, fr, out, start=random.uniform(0.0, max(0.0, media.duration(hc) - fr / media.FPS - 0.2)))
            else:
                media.host_shot(ASSETS / host, fr, out, seed=i)
        elif clip:
            media.render_footage(clip, fr, out, start=_clip_start(clip, fr))
        else:
            img = beat_image(j)
            move = random.choice(["in", "out"]) if part == 0 else random.choice(["detail", "left", "right"])
            media.render_still(img, fr, out, move=move,
                               focus=(random.uniform(0.3, 0.7), random.uniform(0.3, 0.7)))
        files.append(out)
    sc["video"] = media.concat(files, workdir / f"scene_{i:02d}.mp4", workdir, f"scene_{i:02d}")
    for f in list(files) + list(Path(workdir).glob(f"raw_{i:02d}_*.mp4")):  # disk: ara dosyaları sil
        if Path(f) != Path(sc["video"]):
            Path(f).unlink(missing_ok=True)
    global _LAST_IMG
    good = [v for v in imgs.values() if v and Path(v).exists()]
    if good:
        _LAST_IMG = good[-1]


def _plain(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").lower().translate(str.maketrans("çğıöşüâîû", "cgiosuaiu")))


def veo_title_card(topic, w):
    """Gölge'nin eli günün konusunu tüy kalemle deftere yazar (Veo, sesli). Yazı yapay zekâ ile okunup
    doğrulanır; yanlış yazılmışsa bir kez daha denenir, olmazsa None (çizim açılış kartı kullanılır)."""
    prompt = ("Extreme close-up, top-down view of an aged blank parchment page of an open old book on a dark wooden "
              "desk. A hand in a dark leather glove with a black robe sleeve, holding a black feather quill, writes the "
              f"words \"{topic}\" in large, elegant, clearly legible calligraphy across the middle of the page, letter by "
              "letter from left to right; the arm and hand move with each stroke and the ink appears exactly under the "
              "quill tip. Spell it exactly, nothing else is written on the page. Warm candlelight, deep shadows, "
              "faint fog. Sound: quill scratching on paper, soft candle crackle. Realistic, cinematic. No music.")
    for attempt in range(2):
        out = w / f"title_veo_{attempt}.mp4"
        try:
            ai.veo_video(prompt, out)
            fr = w / f"title_veo_{attempt}.jpg"
            media.sh(["ffmpeg", "-y", "-ss", "7.5", "-i", out, "-frames:v", "1", "-vf", "scale=960:-2", fr])
            lite = CFG["judge_models"]
            r = ai.gemini_json("You read handwriting exactly.", "What words are written on the page? "
                               'JSON: {"text": "..."}', images=[fr], required=("text",), models=lite, temperature=0)
            if _plain(r.get("text")) == _plain(topic):
                log(f"Açılış: Gölge'nin eli '{topic}' yazdı (Veo) ✅")
                return out
            log(f"Açılış yazısı yanlış okundu ('{r.get('text')}'), tekrar deneniyor")
        except Exception as e:
            log(f"Veo açılışı yapılamadı: {str(e)[:150]}")
            return None
    return None


def assemble(script, workdir, music):
    """Hook (1. sahne) → marka intro → içerik → kapanış cümlesi + kapanış kartı. Kare hassasiyetinde."""
    w = Path(workdir)
    A = ASSETS
    parts = []  # (video, ses, kelimeler)
    sc = script["scenes"]
    owners = []  # her parçanın hangi sahneye ait olduğu (intro/outro: None)
    if CFG.get("title_card"):  # açılış: defter açılır, gölge kalem günün konusunu yazar
        try:
            topic = (script["title"].split(":")[0] if ":" in script["title"] else
                     (script.get("core_keyword") or script["title"])).strip()
            tc = None
            cdir = A / "host" / "clips"
            if CFG.get("title_card") == "clips" and (cdir / "intro_write.mp4").exists() and (cdir / "page_blank.mp4").exists():
                # 1) Gölge masada yazıp anlatmaya hazırlanır (tek seferlik Veo klibi, kalem sesiyle)
                # 2) yumuşak çözülmeyle defterin yazılmış hali (aynı ışık tonunda): günün konusu sayfada
                pg = media.page_title(cdir / "page_blank.mp4", topic[:40], w / "page_title.mp4",
                                      logo=A / CFG.get("logo_file", "cutaway_logo.png"))
                try:
                    tc = media.opening(cdir / "intro_write.mp4", pg, w / "title_card.mp4")
                except Exception as e:
                    log(f"Açılış geçişi yapılamadı, düz eklenecek: {str(e)[:120]}")
                    iw = w / "intro_write_v.mp4"
                    media.sh(["ffmpeg", "-y", "-i", cdir / "intro_write.mp4", "-an", "-vf", f"scale={media.W}:{media.H},fps={media.FPS}",
                              "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", iw])
                    fr = int(round(media.duration(iw) * media.FPS))
                    parts.append((iw, media.to_wav(cdir / "intro_write.mp4", w / "intro_write_a.wav", frames=fr), []))
                    owners.append(None)
                    tc = pg
            elif CFG.get("title_card") == "veo":
                tc = veo_title_card(topic[:40], w)
            if tc is None:
                tc = media.title_card(topic[:40], w / "title_card.mp4",
                                      accent=tuple(CFG.get("thumbnail_accent", (232, 158, 68))),
                                      logo=A / CFG.get("logo_file", "cutaway_logo.png"))
            fr = int(round(media.duration(tc) * media.FPS))
            parts.append((tc, media.to_wav(tc, w / "title_card_a.wav", frames=fr), []))
            owners.append(None)
        except Exception as e:
            log(f"Açılış kartı yapılamadı: {str(e)[:150]}")
    owners.append(sc[0])
    parts.append((sc[0]["video"], sc[0]["voice"], sc[0]["words"]))
    intro = A / "intro.mp4"
    if CFG.get("use_intro", True) and intro.exists():
        fr = int(round(media.duration(intro) * media.FPS))
        iv = w / "intro_v.mp4"
        media.sh(["ffmpeg", "-y", "-i", intro, "-an", "-frames:v", fr, "-c:v", "libx264", "-preset", "veryfast",
                  "-crf", "18", "-pix_fmt", "yuv420p", "-r", media.FPS, iv])
        ia = media.to_wav(intro, w / "intro_a.wav", frames=fr)
        parts.append((iv, ia, []))
        owners.append(None)
    for s in sc[1:]:
        parts.append((s["video"], s["voice"], s["words"]))
        owners.append(s)
    # kapanış: her videoda aynı imza cümlesi + kapanış kartı
    line = CFG.get("outro_line")
    card = A / "outro.png"
    if line and card.exists():
        f, d, ws = narrate_line(line, w, "outro_vo")
        fr = int(math.ceil((d + CFG.get("outro_hold_seconds", 8)) * media.FPS))
        ov = media.render_still(card, fr, w / "outro_v.mp4", move="in")
        oa = media.to_wav(f, w / "outro_a.wav", frames=fr)
        parts.append((ov, oa, ws))
        owners.append(None)
    # birleştir
    video = media.concat([p[0] for p in parts], w / "master_v.mp4", w, "master_v")
    lst = w / "master_a.txt"
    lst.write_text("".join(f"file '{Path(p[1]).resolve()}'\n" for p in parts))
    nar = w / "master_a.wav"
    media.sh(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", nar])
    words, t = [], 0.0
    for (v, a, ws), own in zip(parts, owners):
        if own is not None:
            own["t0"] = t  # sahnenin ana videodaki başlangıç anı (Short altyazıları için)
        words += [{"word": x["word"], "start": x["start"] + t, "end": x["end"] + t} for x in ws]
        t += media.duration(a)
    script["_chapters"] = build_chapters(script, t)
    chap_ev = [(ct, title, k + 1) for k, (ct, title) in enumerate(script["_chapters"]) if k > 0]
    chap_starts = {round(c[0], 2) for c in chap_ev}
    callouts = [(s["t0"] + 0.5, s["callout"].strip()) for s in sc
                if s.get("callout") and s.get("callout").strip() and round(s.get("t0", -1), 2) not in chap_starts
                and len(s["callout"].split()) <= 6]
    ass = media.write_ass_long(words if CFG.get("burn_captions", True) else [], w / "captions.ass",
                               chapters=chap_ev, callouts=callouts,
                               per_line=CFG.get("caption_words_per_line", 5),
                               highlight=media.THEME.get("chap_num")  # kanalın vurgu rengi
                               if CFG.get("caption_word_highlight", True) else None)
    whoosh = ASSETS / "whoosh.wav"
    sfx = (whoosh, [ct for ct, _, _ in chap_ev]) if whoosh.exists() and chap_ev else None
    final = media.mux_master(video, nar, w / "final.mp4", ass=ass, music=music, sfx=sfx,
                             music_db=CFG.get("music_db", -21.0), j_cut=CFG.get("j_cut_seconds", 0.0))
    return final, words


# ---------------------------------------------------------------- 6a. altyazı çevirisi
def translate_lines(lines, lang_name, chunk=30):
    """Altyazı satırlarını sırası ve sayısı bozulmadan çevirir. Satırlar numaralı gider/gelir (sayı kaymasın).
    Bir parça 3 denemede de olmazsa o parça orijinal kalır; parçaların çoğu başarısızsa None."""
    out, failed = [], 0
    for i in range(0, len(lines), chunk):
        part = lines[i:i + chunk]
        src = {str(k + 1): t for k, t in enumerate(part)}
        got = None
        for _ in range(3):
            try:
                data = llm_json(
                    f"You translate video subtitles into natural, fluent, SIMPLE {lang_name} that anyone understands "
                    "on first read (everyday words, no jargon, no academic or archaic words; if the original "
                    "explains a term, keep the explanation). Keep each line short and readable; keep facts "
                    "accurate; convert nothing that changes meaning. Every numbered line gets its own translation."
                    + (f" {(CFG.get('subtitle_translation') or {}).get('notes', '')}"
                       if lang_name == (CFG.get("subtitle_translation") or {}).get("name") else ""),
                    f"Translate every value into {lang_name}. Keep exactly the same keys 1..{len(part)}.\n"
                    + json.dumps({"lines": src}, ensure_ascii=False) + '\nReturn JSON: {"lines":{"1":"...",...}}',
                    required=("lines",), judge=True)
                d = data["lines"]
                if isinstance(d, list) and len(d) == len(part):
                    d = {str(k + 1): t for k, t in enumerate(d)}
                if isinstance(d, dict) and all(str(k + 1) in d and str(d[str(k + 1)]).strip()
                                               for k in range(len(part))):
                    got = [str(d[str(k + 1)]).strip() for k in range(len(part))]
                    break
                log(f"Çeviri parçası eksik geldi ({i}-{i + len(part)}), tekrar deneniyor")
            except Exception as e:
                log(f"Çeviri hatası: {str(e)[:150]}")
        if got is None:
            failed += 1
            got = part
        out += got
    total = (len(lines) + chunk - 1) // chunk
    if failed and failed * 4 > total:
        log(f"Çeviri başarısız: {failed}/{total} parça")
        return None
    return out


def translate_srt(src, dst, lang_name):
    """Hazır bir SRT'yi (zamanlamaları aynen koruyarak) başka dile çevirir; olmazsa None."""
    blocks = media.read_srt_blocks(src)
    if not blocks:
        return None
    tr = translate_lines([t for _, _, t in blocks], lang_name)
    if not tr:
        return None
    return media.write_srt_blocks([(a, b, t) for (a, b, _), t in zip(blocks, tr)], dst)


def local_meta(script, shorts, lang_name):
    """Başlık/açıklamanın yerel dil sürümü (ör. Türk izleyici Türkçe başlık görür)."""
    try:
        data = llm_json(
            f"You localize YouTube metadata into natural {lang_name}. Keep curiosity and meaning; no clickbait.",
            json.dumps({"title": script["title"], "description": script["description"],
                        "short_titles": [s["title"] for s in shorts],
                        "chapters": [c[1] for c in script.get("_chapters", [])]}, ensure_ascii=False)
            + '\nReturn JSON: {"title":"<=90 chars","description":"...","short_titles":["..."],'
              '"chapters":["... same count and order"]}',
            required=("title", "description"), judge=True)
        return data
    except Exception as e:
        log(f"Başlık çevirisi yapılamadı: {e}")
        return None


def shift_blocks(blocks, start, length):
    """Uzun videonun altyazı bloklarından bir Short'un aralığını alıp sıfıra kaydırır."""
    res = []
    for a, b, t in blocks:
        if b <= start or a >= start + length:
            continue
        res.append((max(a - start, 0), min(b - start, length), t))
    return res


def build_chapters(script, total_len, min_len=12.0):
    """Sahnelerdeki 'chapter' alanlarından YouTube bölümleri: [(saniye, başlık)].
    Kurallar: ilk bölüm 0:00, en az 3 bölüm, her bölüm ≥10 sn (güvenlik için 12)."""
    sc = script["scenes"]
    raw = []
    for i, s in enumerate(sc):
        t = (s.get("chapter") or "").strip()
        if i == 0:
            raw.append((0.0, t or CFG.get("intro_chapter", "Introduction")))
        elif t:
            raw.append((s.get("t0", 0.0), t))
    out = []
    for t, title in raw:
        if out and t - out[-1][0] < min_len:
            continue  # çok kısa bölümü bir öncekine kat
        out.append((t, title[:60]))
    if out and total_len - out[-1][0] < min_len:
        out.pop()
    return out if len(out) >= 3 else []


def fmt_ts(t):
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def watch_next_line(pillar):
    """Açıklamaya 'sıradaki video' bağlantısı: aynı konudaki en son video (yoksa en son video).
    İzleyiciyi kanalda tutar (oturum süresi YouTube için önemli)."""
    hist = [h for h in load_json(HISTORY, []) if h.get("type") == "long" and h.get("video_id")]
    if not hist:
        return ""
    same = [h for h in hist if h.get("pillar") == pillar]
    h = (same or hist)[-1]
    return f"\n\n{CFG.get('watch_next_text', '▶ Watch next:')} {h['title']}\nhttps://youtu.be/{h['video_id']}"


def chapters_text(chapters, titles=None, header="Chapters"):
    if not chapters:
        return ""
    titles = titles if titles and len(titles) == len(chapters) else [c[1] for c in chapters]
    return f"\n\n{header}:\n" + "\n".join(f"{fmt_ts(t)} {ti}" for (t, _), ti in zip(chapters, titles))


ERROR_GUIDE = [
    ("ses kotası doldu", "Google'ın günlük ücretsiz SES (seslendirme) hakkı bitmiş. Hak her gün öğlen yenileniyor; "
                         "sistem ertesi gün aynı saatte kendisi tekrar deneyecek, senden bir şey gerekmiyor."),
    ("TTS ses üretemedi", "Seslendirme yapılamadı (Google'ın ücretsiz ses hakkı). Sistem ertesi gün kendisi "
                          "tekrar deneyecek, senden bir şey gerekmiyor."),
    ("invalid_grant", "YouTube izni geçersiz ya da süresi dolmuş. Çözüm: get_token.bat'ı çalıştır, "
                      "çıkan YT_REFRESH_TOKEN'ı GitHub Secrets'ta güncelle."),
    ("API_KEY_INVALID", "Gemini API anahtarı geçersiz. Çözüm: aistudio.google.com'dan yeni anahtar al, "
                        "GitHub'da GEMINI_API_KEY'i güncelle."),
    ("API key not valid", "Gemini API anahtarı geçersiz. Çözüm: GEMINI_API_KEY'i yenile."),
    ("günlük ücretsiz kota doldu", "Gemini'nin günlük ücretsiz hakkı bitti; yarın sıfırlanır. Kalıcı çözüm: "
                                   "ücretli Gemini anahtarı (GEMINI_PAID_API_KEY)."),
    ("YANLIŞ KANAL", "İzin anahtarı yanlış YouTube kanalına bağlı. get_token.bat'ı çalıştırıp kanal seçme "
                     "ekranında doğru kanalı seç, anahtarı GitHub Secrets'ta güncelle."),
    ("quotaExceeded", "YouTube günlük API kotası doldu. Yarın kendiliğinden devam eder."),
    ("uploadLimitExceeded", "YouTube günlük yükleme sınırı doldu. Yarın kendiliğinden devam eder."),
    ("Authentication error", "Cloudflare anahtarı geçersiz. Görseller yedek kaynaktan alınır; "
                             "istersen CLOUDFLARE_API_TOKEN'ı yenile."),
    ("No space left", "Sunucu diski doldu (geçici). Bir sonraki çalışmada düzelir."),
]


def explain_error(text):
    for key, msg in ERROR_GUIDE:
        if key.lower() in text.lower():
            return msg
    return None


def tr_note(*texts):
    """İngilizce kanalda bildirime Türkçe karşılık (Furkan her konuyu Türkçe de görsün). Hata olursa boş."""
    if CFG.get("language_code") == "tr" or not texts:
        return ""
    try:
        d = llm_json("You translate short YouTube titles/labels into natural Turkish. Use established Turkish "
                     "Islamic terms (cin, Kaf Dağı, karîn, Hz. Süleyman).",
                     json.dumps({"items": list(texts)}, ensure_ascii=False) + '\nReturn JSON: {"items":["..."]} same order',
                     required=("items",), judge=False)
        return "\n🇹🇷 " + " · ".join(str(x) for x in d["items"][:len(texts)])
    except Exception:
        return ""


def tr_title(tr_meta, en_title):
    if CFG.get("language_code") == "tr":  # Türkçe kanal: ana başlık zaten Türkçe
        return en_title
    t = (tr_meta or {}).get("title")
    return f"{t}\n({en_title})" if t else en_title


# ---------------------------------------------------------------- 6b. shorts planı
def plan_shorts(script):
    """Senaryodaki short planını doğrular; bozuksa ilk sahnelerden tek short'a düşer."""
    n = len(script["scenes"])
    want = max(1, int(CFG.get("shorts_per_video", 3)))
    plans, used = [], set()
    for s in script.get("shorts") or []:
        try:
            a, b = int(s["start_scene"]), int(s["end_scene"])
        except (KeyError, TypeError, ValueError):
            continue
        a, b = max(0, a), min(n - 1, b)
        rng = set(range(a, b + 1))
        if a > b or rng & used:
            continue
        used |= rng
        plans.append({"start": a, "end": b, "title": (s.get("title") or script.get("short_title")
                                                       or script["title"]).strip(),
                      "ig_caption": (s.get("ig_caption") or "").strip()})
        if len(plans) >= want:
            break
    if not plans:
        plans = [{"start": 0, "end": min(2, n - 1), "title": script.get("short_title") or script["title"]}]
    return plans


# ---------------------------------------------------------------- 7. thumbnail
def make_thumbnails(script, workdir, ledger):
    """3 farklı küçük resim adayı → yapay zekâ hakemi telefon boyutunda da bakıp sıralar.
    Dönüş: (en iyi, yedek) — yedek, video beklenenden az tıklanırsa sonradan değiştirmek için saklanır."""
    cands = []
    for i, c in enumerate(script["thumbnail_concepts"][:3]):
        prompt = (f"{c['image_prompt']}. {CFG['thumbnail_style']}. Main subject large on the right half, "
                  "dark empty space on the left third. No text, no letters, no logos, no watermark.")
        raw = None
        if CFG.get("paid_tier") or ai.vertex_on():  # ücretli anahtar / Cloud kredisi: en iyi kalite küçük resim (Gemini)
            try:
                raw = ai.gemini_image(prompt, workdir / f"thumb_raw_{i}.jpg")
            except Exception as e:
                log(f"Küçük resim Gemini ile üretilemedi: {str(e)[:150]}")
        if raw is None:
            raw = gen_image(prompt, workdir / f"thumb_raw_{i}.jpg", script["scenes"][0].get("broll_query"), ledger)
        cands.append(media.compose_thumbnail(raw, c["text"], workdir / f"thumb_{i}.jpg",
                                             logo=ASSETS / CFG.get("logo_file", "cutaway_logo.png"),
                                             accent=tuple(CFG.get("thumbnail_accent", (255, 168, 60)))))
    sheet = media.contact_sheet(cands, workdir / "thumbs_sheet.jpg")
    try:
        pick = llm_json(
            "You are a top YouTube thumbnail designer. You judge like a viewer scrolling a phone feed in 0.5 s.",
            f"Video title: {script['title']}\nThe attached image shows 3 thumbnail candidates (#1, #2, #3), "
            "large on top and at REAL PHONE SIZE below. Rank them by expected click-through rate. Criteria: "
            "readable at phone size, one clear focal subject, strong contrast and color pop, emotion or "
            "tension, text + image + title together create a curiosity gap (title and text must not say the "
            "same thing), looks premium not cheap, no clutter, nothing misleading. "
            'Return JSON: {"ranking":[1,2,3],"reason":"..."}',
            images=[sheet], required=("ranking",), judge=True)
        order = [min(max(int(x) - 1, 0), len(cands) - 1) for x in pick["ranking"]]
        order = list(dict.fromkeys(order + list(range(len(cands)))))
        log(f"Thumbnail sıralaması: {[o + 1 for o in order]} - {pick.get('reason', '')}")
    except Exception as e:
        log(f"Thumbnail hakemi çalışmadı, ilki seçildi: {e}")
        order = list(range(len(cands)))
    return cands[order[0]], (cands[order[1]] if len(order) > 1 else None)


THUMB_ALT = DATA / "thumb_alt"


def maybe_swap_thumbnails(history):
    """Yayından 3-10 gün sonra günlük izlenmesi kanal ortalamasının yarısından düşük kalan videonun
    küçük resmini yedek adayla bir kez değiştirir (YouTube tıklama oranını API'ye vermiyor; izlenme hızı vekil)."""
    if DRY_RUN or not os.getenv("YT_REFRESH_TOKEN") or not THUMB_ALT.exists():
        return
    now = datetime.now()
    longs = [h for h in history if h.get("type") == "long" and h.get("video_id")]
    age = {h["video_id"]: (now - datetime.fromisoformat(h["date"])).days for h in longs}
    for f in THUMB_ALT.glob("*.jpg"):  # süresi geçen yedekleri temizle
        if age.get(f.stem, 99) > 14:
            f.unlink()
    if len(longs) < 4:
        return
    stats = publish.video_stats([h["video_id"] for h in longs])
    vpd = {v: stats[v]["views"] / max(a, 1) for v, a in age.items() if v in stats and a >= 3}
    for h in longs:
        v, alt = h["video_id"], THUMB_ALT / f"{h['video_id']}.jpg"
        if h.get("thumb_swapped") or not alt.exists() or not (3 <= age[v] <= 10) or v not in vpd:
            continue
        others = sorted(x for k, x in vpd.items() if k != v)
        if len(others) < 3:
            continue
        median = others[len(others) // 2]
        if vpd[v] < 0.5 * median:
            try:
                publish.set_thumbnail(v, alt)
                h["thumb_swapped"] = True
                alt.unlink()
                log(f"Küçük resim değiştirildi: {v} (günlük {vpd[v]:.0f} izlenme, kanal ortası {median:.0f})")
                notify(f"🖼 Az izlenen videonun küçük resmi yedek tasarımla değiştirildi:\nhttps://youtu.be/{v}")
            except Exception as e:
                log(f"Küçük resim değiştirilemedi: {str(e)[:150]}")
    save_json(HISTORY, history)


# ---------------------------------------------------------------- senaryo üretim döngüsü
QKEYS = ("originality", "hook", "clarity", "simplicity", "flow", "value_density", "audience_fit")


def _score(review):
    vals = [float(review.get(k, 0) or 0) for k in QKEYS]
    return (min(vals), sum(vals) / len(vals))


TR_ASCII = str.maketrans("çğıöşüÇĞİÖŞÜâîûÂÎÛ", "cgiosuCGIOSUaiuAIU")


def _norm(t):
    return (t or "").translate(TR_ASCII).lower()


def seo_problems(script):
    """Anahtar kelime üçlemesi: başlığın ilk 40 karakteri, açıklamanın ilk 120 karakteri, ilk 20 sn anlatım."""
    kw = (script.get("core_keyword") or "").strip()
    if not kw:
        return ["- Add core_keyword (the 2-5 word phrase viewers search) and follow the core_keyword rules."]
    k, out = _norm(kw), []
    if k not in _norm(script.get("title", ""))[:max(40, len(kw) + 5)]:
        out.append(f"- The title must contain the core keyword '{kw}' within its first 40 characters.")
    opening = " ".join(sc.get("narration", "") for sc in script.get("scenes", [])[:2])
    if k not in _norm(opening):
        out.append(f"- Say the core keyword '{kw}' out loud in the narration of scene 0 or 1 "
                   "(within the first 20 seconds), naturally.")
    return out


def search_demand(phrase, n=12):
    """YouTube arama önerileri: insanların bu konuda en çok ne aradığı (ücretsiz, kota harcamaz)."""
    import requests
    hl = CFG.get("language_code", "en")
    gl = "TR" if hl == "tr" else "US"
    out = []
    for q in dict.fromkeys([phrase, " ".join(phrase.split()[:2]), phrase.split()[0] if phrase else ""]):
        if not q:
            continue
        try:
            r = requests.get("https://suggestqueries.google.com/complete/search",
                             params={"client": "firefox", "ds": "yt", "q": q, "hl": hl, "gl": gl}, timeout=15)
            for x in r.json()[1]:
                if x not in out:
                    out.append(x)
        except Exception:
            continue
    return out[:n]


def title_pass(script):
    """Tıklatan başlık: 10 aday yazdırır, hakem telefonda akış içinde en çok tıklanacak olanı seçer.
    Anahtar kelime kuralı ve uzunluk kodla kontrol edilir; olmazsa eski başlık kalır."""
    text = " ".join(s["narration"] for s in script["scenes"][:6])[:2500]
    demand = search_demand((script.get("core_keyword") or script["title"].split(":")[0]).strip())
    if demand:
        log("En çok arananlar: " + " | ".join(demand[:8]))
    try:
        r = llm_json(
            f"You are the best YouTube title writer for a {CFG['language']} {CFG.get('channel_name')} channel. "
            "You write titles that people cannot scroll past, but that the video fully delivers (no lies).",
            f"Audience: {CFG['audience']}\nCore search phrase: {script.get('core_keyword', '')}\n"
            f"Current title: {script['title']}\nOpening of the video:\n{text}\n\n"
            f"Write 10 DIFFERENT titles in {CFG['language']}. Rules: 45-65 characters; the core search phrase (or "
            "its natural form) in the first 40 characters; ONE strong curiosity gap (an unanswered question, a "
            "shocking specific detail, a contradiction, a hidden truth); concrete details beat vague words (a number, "
            "a name, a strange object); emotional power words used naturally (e.g. for Turkish: korkunç, gizli, "
            "kimsenin bilmediği, hâlâ çözülemedi, o gece; for English: the real reason, nobody noticed, still); "
            "natural native phrasing, never translated-sounding; no ALL CAPS words, no emoji, no clickbait the "
            "video can't pay off. "
            + (f"WHAT PEOPLE ACTUALLY SEARCH on YouTube about this topic (autocomplete, most searched first): "
               f"{' | '.join(demand)}. Build the title around the most searched phrase that the video truly answers, "
               "in plain words a 12-year-old instantly understands (no rare or academic words), and make it feel "
               "eerie and mysterious, like something unexplained or supernatural happened (only as far as the video "
               "really supports). " if demand else "")
            + "NEVER build the title around a name or term the average viewer doesn't know (an obscure narrator, "
              "scholar, place or Arabic term); lead with the famous, instantly understood thing (e.g. Deccal, cinler, "
              "kıyamet, a famous place) and the eerie event itself. Bad: 'Temim ed-Dari Hadisi: ...'. Good: 'Deccal "
              "Bir Adada Zincirli mi? 30 Denizcinin Gördüğü Şey'. "
            + (f"The channel owner LOVES this style, follow its pattern (topic: concrete people/number + what they "
               f"did + the unanswered question): {CFG['title_examples']} " if CFG.get("title_examples") else "")
            + "Then pick the one with the highest expected click-through rate on a phone feed. "
            'JSON: {"titles":["..."],"best_index":0,"why":"..."}',
            required=("titles", "best_index"), judge=False)
        ts = [t.strip().strip('"') for t in r["titles"] if isinstance(t, str) and 25 <= len(t.strip()) <= 70]
        best = r["titles"][int(r["best_index"])].strip().strip('"') if 0 <= int(r["best_index"]) < len(r["titles"]) else None
        ck = _norm(script.get("core_keyword", ""))
        good = [t for t in ([best] if best else []) + ts if t and (not ck or ck.split()[0] in _norm(t)[:50])]
        if good:
            log(f"Başlık seçildi: {good[0]}  (önceki: {script['title']}) — {str(r.get('why', ''))[:150]}")
            script["title"] = good[0]
    except Exception as e:
        log(f"Başlık seçimi yapılamadı: {str(e)[:150]}")
    return script


def seo_fix(script):
    """Eksikse bir kez düzelttirir; açıklamanın ilk satırını ve etiketleri kodla garanti eder."""
    probs = seo_problems(script)
    if probs:
        log("SEO düzeltmesi: " + " | ".join(p[2:80] for p in probs))
        try:
            fixed = revise_script(script, "\n".join(probs) + "\n- Change ONLY what these points need.")
            if len(fixed.get("scenes", [])) >= len(script["scenes"]) * 0.8:
                for key in ("_chapters",):
                    fixed.pop(key, None)
                script = fixed
        except Exception as e:
            log(f"SEO düzeltmesi yapılamadı: {e}")
    kw = (script.get("core_keyword") or "").strip()
    desc = script.get("description", "")
    if kw and _norm(kw) not in _norm(desc[:120]):
        script["description"] = f"{kw[:1].upper() + kw[1:]}: {desc}"
    tidy_hashtags(script)
    script["tags"] = build_tags(script)
    return script


def build_tags(script, budget=470):
    """Etiketler: anahtar kelime + modelin yazım hatası/varlık etiketleri + Türkçe karaktersiz yazılışlar.
    YouTube toplam etiket sınırı 500 karakter."""
    raw = [script.get("core_keyword", "")] + list(script.get("tags", []))
    out, seen = [], set()
    for t in raw:
        t = re.sub(r"[<>#,]", "", str(t)).strip()
        variants = [t]
        if CFG.get("ascii_tag_variants") and t.translate(TR_ASCII) != t:
            variants.append(t.translate(TR_ASCII))  # Türkçe karaktersiz arama: "gercek hikayesi"
        for v in variants:
            key = v.lower()
            if len(v) < 2 or key in seen:
                continue
            cost = len(v) + (2 if " " in v else 0) + 1
            if sum(len(x) + (2 if " " in x else 0) + 1 for x in out) + cost > budget:
                return out
            seen.add(key)
            out.append(v)
    return out


def slug(text, n=60):
    s_ = re.sub(r"[^a-z0-9]+", "-", _norm(text)).strip("-")
    return (s_[:n].rstrip("-") or "video")


# ---------------------------------------------------------------- yayın saatleri
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def next_slot(after, slots):
    """slots: {"tz": "Europe/Istanbul", "times": {"mon": "19:00", ..., "default": "19:00"}} →
    'after' anından sonraki ilk yayın anı (UTC)."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(slots.get("tz", "UTC"))
    loc = after.astimezone(tz)
    for add in range(0, 9):
        day = (loc + timedelta(days=add)).date()
        hhmm = slots["times"].get(DAYS[day.weekday()], slots["times"].get("default"))
        if not hhmm:
            continue
        h, m = map(int, hhmm.split(":"))
        cand = datetime(day.year, day.month, day.day, h, m, tzinfo=tz)
        if cand >= after:
            return cand.astimezone(timezone.utc)
    return after


def plan_times(now, n_shorts):
    """Ana video + Shorts yayın anları (UTC). Config'te yayın saatleri yoksa eski gecikme kuralı."""
    if CFG.get("publish_slots"):
        early = now + timedelta(hours=CFG.get("min_lead_hours", 1.5))
        main = next_slot(early, CFG["publish_slots"])
        # bugünün yayın saati kaçtıysa yarına atma: aynı gece, mümkün olan ilk anda yayınla (gece yarısından önce)
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(CFG["publish_slots"].get("tz", "UTC"))
        e_loc, m_loc = early.astimezone(tz), main.astimezone(tz)
        if m_loc.date() > e_loc.date() and e_loc.hour < CFG.get("late_publish_until", 23):
            main = early.replace(second=0, microsecond=0)
            log(f"Bugünün yayın saati kaçtı; video aynı gün {e_loc:%H:%M} (yerel) yayınlanacak")
    else:
        main = now + timedelta(hours=CFG["publish_delay_hours"])
    shorts, prev = [], main + timedelta(hours=CFG.get("shorts_delay_hours", 20))
    for k in range(n_shorts):
        if CFG.get("shorts_slots"):
            t = next_slot(prev, CFG["shorts_slots"])
        else:
            t = now + timedelta(hours=CFG["shorts_delay_hours"] + k * CFG.get("shorts_gap_hours", 16))
        shorts.append(t)
        prev = t + timedelta(hours=CFG.get("shorts_gap_hours", 20))
    return main, shorts


def fmt_local(t):
    from zoneinfo import ZoneInfo
    return t.astimezone(ZoneInfo("Europe/Istanbul")).strftime("%d.%m %H:%M")


# ---------------------------------------------------------------- sabit yorum (yayından sonra)
PENDING_COMMENTS = DATA / "pending_comments.json"


def queue_comment(video_id, text):
    if not text or not CFG.get("post_comment", True):
        return
    q = load_json(PENDING_COMMENTS, []) if PENDING_COMMENTS.exists() else []
    q.append({"video_id": video_id, "text": text.strip(), "added": datetime.now().isoformat(timespec="minutes")})
    save_json(PENDING_COMMENTS, q)


PLAYLISTS = DATA / "playlists.json"
VOTES = DATA / "votes.json"


def record_vote(video_id, script):
    """Videonun sonundaki 1/2 oylamasını kaydeder; birkaç gün sonra yorumlardan sayılır."""
    opts = [str(o).strip() for o in (script.get("vote_options") or [])][:2]
    if len(opts) < 2:  # revizyonda alan düştüyse sabit yorumdaki "1) … / 2) …" satırlarından al
        ls = [l.strip() for l in (script.get("pinned_comment") or "").splitlines()]
        opts = [l[2:].strip() for p_ in ("1)", "2)") for l in ls if l.startswith(p_)][:2]
    if not video_id or len(opts) < 2:
        return
    v = load_json(VOTES, []) if VOTES.exists() else []
    v.append({"video_id": video_id, "title": script.get("title", ""), "question": (script.get("pinned_comment") or "").split("\n")[0],
              "options": opts, "added": datetime.now().isoformat(timespec="minutes")})
    save_json(VOTES, v[-60:])


def _vote_of(text, opts):
    t = (text or "").strip().lower()
    if not t:
        return None
    first = t.lstrip("*#(").replace("️⃣", "")[:2]
    if first[:1] in "12" and (len(first) == 1 or not first[1].isdigit()):
        return int(first[0]) - 1
    hits = [i for i, o in enumerate(opts) if o and o.lower() in t]
    return hits[0] if len(hits) == 1 else None


def tally_votes(min_days=3):
    """3 gün dolan oylamaları sayar: sonucu Telegram'a bildirir, sabit yorumun altına sonucu yazar,
    bir sonraki videonun senaryosunda gerçek sayılarla tek cümleyle anılması için saklar."""
    if DRY_RUN or not VOTES.exists() or CFG.get("uploader", "youtube") != "youtube":
        return
    votes = load_json(VOTES, [])
    now = datetime.now()
    changed = False
    for v in votes:
        if v.get("counted") or (now - datetime.fromisoformat(v["added"])).days < min_days:
            continue
        try:
            yt = publish._yt()
            counts, own_thread, page, seen = [0, 0], None, None, 0
            for _ in range(5):  # en fazla 500 yorum (5 birim kota)
                r = yt.commentThreads().list(part="snippet", videoId=v["video_id"], maxResults=100,
                                             textFormat="plainText", pageToken=page).execute()
                for it in r.get("items", []):
                    c = it["snippet"]["topLevelComment"]["snippet"]
                    if CHANNEL_ID and c.get("authorChannelId", {}).get("value") == CHANNEL_ID:
                        if v["question"][:40] and c.get("textOriginal", "").startswith(v["question"][:40]):
                            own_thread = it["id"]
                        continue
                    seen += 1
                    k = _vote_of(c.get("textOriginal"), v["options"])
                    if k is not None:
                        counts[k] += 1
                page = r.get("nextPageToken")
                if not page:
                    break
        except Exception as e:
            log(f"Oylama sayılamadı ({v['video_id']}): {str(e)[:120]}")
            continue
        n = sum(counts)
        v.update(counted=now.isoformat(timespec="minutes"), counts=counts, comments=seen)
        changed = True
        if n == 0:
            notify(f"🗳️ Oylama sonucu: \"{v['title']}\" videosuna 3 günde {seen} yorum geldi ama oy veren olmadı.")
            continue
        pa = round(100 * counts[0] / n)
        tr = CFG.get("language_code", "en").startswith("tr")
        res = (f"{v['options'][0]} %{pa} · {v['options'][1]} %{100 - pa} ({n} oy)" if tr else
               f"{v['options'][0]} {pa}% · {v['options'][1]} {100 - pa}% ({n} votes)")
        notify(f"🗳️ Oylama sonucu: \"{v['title']}\"\n{v['question']}\n{res}\n"
               + ("Sonucu sabit yorumun altına yazdım; bir sonraki videoda da kısaca anılacak." if n >= 5 else
                  "Oy az olduğu için videoda anılmayacak."))
        if n >= 5:
            v["use_next"] = True
            if own_thread:
                try:
                    txt = (f"🗳️ Sonuç: {res}. Oy veren herkese teşekkürler!" if tr else
                           f"🗳️ Results: {res}. Thanks to everyone who voted!")
                    yt.comments().insert(part="snippet", body={"snippet": {"parentId": own_thread,
                                                                            "textOriginal": txt}}).execute()
                except Exception as e:
                    log(f"Sonuç yanıtı yazılamadı: {str(e)[:120]}")
    if changed:
        save_json(VOTES, votes)


def poll_context():
    """Önceki videonun oylama sonucu (yeterli oy varsa) → yeni senaryoda tek cümleyle, gerçek sayılarla anılır."""
    if not VOTES.exists():
        return ""
    for v in reversed(load_json(VOTES, [])):
        if v.get("use_next") and not v.get("used"):
            n = sum(v["counts"])
            pa = round(100 * v["counts"][0] / n)
            poll_context.pending = v["video_id"]
            return (f"Viewer poll result from our previous video \"{v['title']}\" — question: {v['question']} — "
                    f"{v['options'][0]}: {pa}%, {v['options'][1]}: {100 - pa}% ({n} votes). In the outro (before the new "
                    "vote question) mention this result in ONE short, warm sentence with these exact numbers and "
                    "thank the voters. Do not invent or round differently.\n")
    return ""


def mark_poll_used():
    vid = getattr(poll_context, "pending", None)
    if not vid or not VOTES.exists():
        return
    votes = load_json(VOTES, [])
    for v in votes:
        if v["video_id"] == vid:
            v["used"] = True
    save_json(VOTES, votes)


def add_to_pillar_playlist(video_id, pillar):
    """Her konu sütunu bir oynatma listesi: izleyici bir videodan sonrakine akar (izlenme süresi artar)."""
    names = CFG.get("playlists") or {}
    title = names.get(pillar)
    if not title or CFG.get("uploader", "youtube") != "youtube":
        return
    cache = load_json(PLAYLISTS, {}) if PLAYLISTS.exists() else {}
    try:
        pid = publish.ensure_playlist(title, f"{CFG['channel_name']}: {pillar}", CFG["language_code"], cache)
        save_json(PLAYLISTS, cache)
        publish.add_to_playlist(pid, video_id)
        log(f"Oynatma listesine eklendi: {title}")
    except Exception as e:
        log(f"Oynatma listesine eklenemedi: {str(e)[:150]}")


def post_due_comments():
    """Video herkese açık olunca soruyu kanal adına yorum olarak yazar (gizli videoya yorum yazılamaz)."""
    if DRY_RUN or not PENDING_COMMENTS.exists() or not os.getenv("YT_REFRESH_TOKEN"):
        return
    q, keep = load_json(PENDING_COMMENTS, []), []
    for item in q:
        try:
            age = (datetime.now() - datetime.fromisoformat(item["added"])).days
            if age > 45:
                continue
            if publish.video_privacy(item["video_id"]) != "public":
                keep.append(item)
                continue
            publish.post_comment(item["video_id"], item["text"])
            log(f"Yorum yazıldı: {item['video_id']}")
            notify(f"💬 Videoya tartışma sorusu yorum olarak yazıldı. İstersen Studio'dan sabitle:\n"
                   f"https://youtu.be/{item['video_id']}\n«{item['text'][:200]}»")
        except Exception as e:
            log(f"Yorum yazılamadı ({item.get('video_id')}): {str(e)[:150]}")
            keep.append(item)
    save_json(PENDING_COMMENTS, keep)


def make_manual_package(script, idea, shorts, thumb, main_srt, sub_srt, history, ledger):
    """Elle yükleme paketi: anlamlı adlı dosyalar + Türkçe yükleme rehberi (başlık, açıklama, etiketler...)."""
    for f in OUT.glob("*"):
        f.unlink()
    lc = CFG.get("language_code", "en")
    sub = CFG.get("subtitle_translation") or {}
    base = slug(script.get("core_keyword") or script["title"])
    shutil.copy(script["_final"], OUT / f"1-VIDEO-{base}.mp4")
    if thumb:
        shutil.copy(thumb, OUT / "1-KUCUK-RESIM.jpg")
    if main_srt:
        shutil.copy(main_srt, OUT / f"1-altyazi-{lc}.srt")
    if sub_srt:
        shutil.copy(sub_srt, OUT / f"1-altyazi-{sub.get('code', 'tr')}.srt")
    for k, sh_ in enumerate(shorts):
        shutil.copy(sh_["file"], OUT / f"{k + 2}-SHORT-{slug(sh_['title'], 40)}.mp4")
    chs = script.get("_chapters") or []
    sub_link = (f"\n\n{CFG.get('subscribe_text', '🔔 Subscribe:')} "
                f"https://www.youtube.com/channel/{CHANNEL_ID}?sub_confirmation=1") if CHANNEL_ID else ""
    desc = (script["description"] + chapters_text(chs, header=CFG.get("chapters_heading", "Chapters"))
            + watch_next_line(idea["pillar"]) + sub_link + _footer())
    desc = tags_last(desc)
    hashtags = short_hashtags(script)
    pl = (CFG.get("playlists") or {}).get(idea["pillar"], "")
    main_t, short_ts = plan_times(datetime.now(timezone.utc), len(shorts))
    lines = [
        f"{CFG['channel_name'].upper()} – ELLE YÜKLEME PAKETİ",
        "=" * 60,
        "YouTube Studio → Oluştur → Video yükle. Her video için aşağıdaki bilgileri kopyala-yapıştır.",
        "Her videoda: Çocuklara özel değil · Ayrıntılar → 'Değiştirilmiş veya yapay içerik' = EVET.",
        f"Önerilen yayın saati (TR): {fmt_local(main_t)}  (Görünürlük → Planla)",
        "",
        "-" * 60, "1) UZUN VİDEO", "-" * 60,
        f"Dosya: 1-VIDEO-{base}.mp4   ·   Küçük resim: 1-KUCUK-RESIM.jpg",
        "", "BAŞLIK:", script["title"],
        "", "AÇIKLAMA:", desc,
        "", "ETİKETLER (Daha fazla göster → Etiketler kutusuna yapıştır):", ", ".join(script.get("tags", [])),
        "", f"OYNATMA LİSTESİ: {pl}" if pl else "",
        f"KATEGORİ: {'Eğitim' if str(CFG.get('category_id')) == '27' else CFG.get('category_id')}   ·   "
        f"VİDEO DİLİ: {CFG.get('language')}",
        f"ALTYAZILAR (Altyazılar → Dosya yükle → Zamanlamalı): 1-altyazi-{lc}.srt"
        + (f" ve 1-altyazi-{sub.get('code')}.srt ({sub.get('label')})" if sub_srt else ""),
        "", "YAYINDAN SONRA YAZILACAK VE SABİTLENECEK YORUM:", script.get("pinned_comment", ""),
    ]
    for k, sh_ in enumerate(shorts):
        lines += ["", "-" * 60, f"{k + 2}) SHORT", "-" * 60,
                  f"Dosya: {k + 2}-SHORT-{slug(sh_['title'], 40)}.mp4",
                  f"Önerilen yayın (TR): {fmt_local(short_ts[k])}",
                  "BAŞLIK:", sh_["title"][:90] + " #Shorts",
                  "AÇIKLAMA:", f"{sh_['title']}\n\n{CFG.get('full_video_text', 'Watch the full video:')} "
                               f"[uzun videonun bağlantısını buraya yapıştır]\n\n{hashtags} #Shorts",
                  "İLGİLİ VİDEO: Yükleme ekranında 'İlgili video' olarak uzun videoyu seç."]
    (OUT / "0-YUKLEME-BILGILERI.txt").write_text("\n".join(lines), encoding="utf-8")
    # tamamlayıcı için kayıt: sahibi yalnızca dosyayı + başlığı yükler, gerisini sistem ekler
    key = datetime.now().strftime("%Y%m%d-%H%M") + "-" + base[:30]
    keep = MANUAL_DIR / key
    keep.mkdir(parents=True, exist_ok=True)
    caps = []
    if thumb:
        shutil.copy(thumb, keep / "thumb.jpg")
    if main_srt:
        shutil.copy(main_srt, keep / f"cap_{lc}.srt")
        caps.append([f"cap_{lc}.srt", lc, CFG.get("caption_label", "English")])
    if sub_srt:
        shutil.copy(sub_srt, keep / f"cap_{sub.get('code')}.srt")
        caps.append([f"cap_{sub.get('code')}.srt", sub.get("code"), sub.get("label")])
    iso_ = lambda t: t.strftime("%Y-%m-%dT%H:%M:%SZ")
    items = [{"kind": "long", "title": script["title"][:100], "marker": f"1-VIDEO-{base}",
              "description": desc, "tags": script.get("tags", []), "publish_at": iso_(main_t),
              "thumb": "thumb.jpg" if thumb else None, "captions": caps, "playlist": pl,
              "comment": script.get("pinned_comment", "")}]
    for k, sh_ in enumerate(shorts):
        items.append({"kind": "short", "title": (sh_["title"][:90].rstrip() + " #Shorts"),
                      "marker": f"{k + 2}-SHORT-{slug(sh_['title'], 40)}",
                      "description": f"{sh_['title']}\n\n{CFG.get('full_video_text', 'Watch the full video:')} "
                                     f"{{LONG_URL}}\n\n{hashtags} #Shorts",
                      "tags": script.get("tags", []), "publish_at": iso_(short_ts[k])})
    entry = {"key": key, "created": datetime.now().isoformat(timespec="minutes"), "items": items}
    drive_link = ""
    if drive.enabled() and not DRY_RUN:
        try:  # telefondan yükleyebilsin diye videoları Google Drive'a da koy
            vids = sorted(OUT.glob("*.mp4"))
            entry["drive_folder"], drive_link = drive.upload_package(
                vids, CFG["channel_name"], f"{datetime.now():%d.%m} {script['title'][:60]}")
            log(f"Paket Google Drive'a yüklendi: {drive_link}")
        except Exception as e:
            log(f"Google Drive'a yüklenemedi: {str(e)[:200]}")
    rel_link = ""
    if release.enabled() and not DRY_RUN:
        tag = f"paket-{key}"
        for attempt in range(2):
            try:  # telefondan zip'siz, dosya dosya indirilebilsin
                entry["release_id"], rel_link = release.create(
                    tag, f"{CFG['channel_name']} · {datetime.now():%d.%m} · {script['title'][:70]}",
                    (OUT / "0-YUKLEME-BILGILERI.txt").read_text(encoding="utf-8"),
                    sorted(OUT.glob("*.mp4")) + sorted(OUT.glob("*.jpg")))
                entry["release_tag"] = tag
                log(f"Paket yayında (Releases): {rel_link}")
                break
            except Exception as e:
                log(f"Paket Releases'a konamadı ({attempt + 1}): {str(e)[:200]}")
                if entry.get("release_id") is None:
                    tag = f"paket-{key}-{attempt + 2}"
    if not rel_link and not drive_link:
        (OUT / "ARTIFACT_NEEDED").write_text("1")  # iş akışı yedek olarak artifact yüklesin
    pend = load_json(MANUAL_PENDING, []) if MANUAL_PENDING.exists() else []
    pend.append(entry)
    save_json(MANUAL_PENDING, pend)
    history.append({"date": datetime.now().isoformat(timespec="minutes"), "type": "long",
                    "title": script["title"], "pillar": idea["pillar"], "video_id": None, "manual": True,
                    "series_id": idea.get("series_id")})
    save_json(HISTORY, history)
    ledger.save()
    run = os.getenv("GITHUB_RUN_ID")
    link = f"https://github.com/{os.getenv('GITHUB_REPOSITORY', 'Cutaw4y/otoyt')}/actions/runs/{run}" if run else ""
    titles = "\n".join(f"{i + 1}) {it['title']}" for i, it in enumerate(items))
    if drive_link:
        where = f"📱 Google Drive: {drive_link}\n"
    elif rel_link:
        where = (f"📥 İndir: {rel_link}\n"
                 "Sayfanın altındaki 'Assets' bölümünde her video ayrı dosya: dokun, iner.\n")
    else:
        where = f"İndir: {link}\nSayfanın en altındaki 'Artifacts' → paketi indir.\n"
    notify(f"📦 Yeni video paketi hazır ({len(items)} video).\n{where}\n"
           "YAPMAN GEREKEN: her videoyu YouTube'a yükle, BAŞLIĞA aşağıdakini yapıştır, "
           "'çocuklara özel değil', görünürlük = Özel/Gizli.\n"
           "Açıklama, etiketler, küçük resim, altyazılar, oynatma listesi, yayın saati ve yorumu sistem ekler.\n"
           "Önce uzun videoyu (1) yükle. Aşağıda her video için ayrı mesaj var: dosya adı + başlığı "
           "(başlığa dokununca kopyalanır).\n\n"
           + titles)
    for i, it in enumerate(items):  # her başlık ayrı mesaj: telefonda tek dokunuşla kopyalansın
        notify(f"{i + 1}) {'UZUN VİDEO' if it['kind'] == 'long' else 'SHORT'}\n"
               f"Dosya: {it['marker']}.mp4\nBaşlık (dokun, kopyalanır):", code=it["title"])
    log(f"Elle yükleme paketi hazır: {sorted(p.name for p in OUT.glob('*'))}")


TG_MAX = 49 * 1024 * 1024  # Telegram botları en fazla 50 MB dosya gönderebiliyor


def tg_send_video(path, text, code):
    """Videoyu Telegram'a (sıkıştırmadan) gönderir; açıklamada dokununca kopyalanan başlık. Başarılıysa True."""
    import html as _h
    tok, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (tok and chat):
        return False
    cap = (f"{CFG['notify_prefix']} " if CFG.get("notify_prefix") else "") + text
    try:
        with open(path, "rb") as fh:
            r = requests.post(f"https://api.telegram.org/bot{tok}/sendVideo", timeout=600,
                              data={"chat_id": chat, "parse_mode": "HTML", "supports_streaming": "true",
                                    "width": 1080, "height": 1920,
                                    "caption": f"{_h.escape(cap)}\n<code>{_h.escape(code)}</code>"[:1000]},
                              files={"video": (Path(path).name, fh, "video/mp4")})
        if r.ok:
            return True
        log(f"Telegram video gönderilemedi: {r.status_code} {r.text[:150]}")
    except Exception as e:
        log(f"Telegram video gönderilemedi: {str(e)[:150]}")
    return False


def tg_send_file(path, caption=""):
    """Dosyayı Telegram'a orijinal kalitede (belge olarak) gönderir; telefondan tek dokunuşla indirilir.
    50 MB'tan büyükse gönderemez, bunu mesajla bildirir."""
    tok, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    path = Path(path)
    if not (tok and chat and path.exists()):
        return
    size = path.stat().st_size
    if size > TG_MAX:
        notify(f"ℹ️ {path.name} {size / 1048576:.0f} MB, Telegram sınırı 50 MB: bu dosyayı paketin GitHub "
               "linkinden (Artifacts) indir.")
        return
    try:
        with open(path, "rb") as fh:
            r = requests.post(f"https://api.telegram.org/bot{tok}/sendDocument",
                              data={"chat_id": chat, "caption": caption[:1000]},
                              files={"document": (path.name, fh)}, timeout=600)
        if not r.ok:
            log(f"Telegram dosya gönderilemedi: {r.status_code} {r.text[:150]}")
    except Exception as e:
        log(f"Telegram dosya gönderilemedi: {str(e)[:150]}")


def fix_hashtags(caption, brand=None, limit=5, discovery=None):
    """Instagram en fazla 5 etiket kabul ediyor. Düzen: keşfet etiketi + 1 büyük konu etiketi (kanal listesinden)
    + 2 konuya özel etiket + marka etiketi. Fazlası silinir, eksikse kanal listesinden tamamlanır."""
    tags, seen = [], set()
    for t in re.findall(r"#[\wçğıöşüÇĞİÖŞÜ]+", caption):
        if t.lower() not in seen:
            seen.add(t.lower())
            tags.append(t)
    body = re.sub(r"\s*#[\wçğıöşüÇĞİÖŞÜ]+", "", caption).rstrip()
    if discovery:
        pool = [d.lower() for d in discovery]
        broad = [t for t in tags if t.lower() in pool and t.lower() != discovery[0].lower()]
        specific = [t for t in tags if t.lower() not in pool and (not brand or t.lower() != brand.lower())]
        broad = (broad + [d for d in discovery[1:] if d.lower() not in seen])[:1]
        tags = [discovery[0]] + broad + specific[:limit - 2 - (1 if brand else 0)]
        if len(tags) < limit - (1 if brand else 0):  # konuya özel etiket azsa listeden tamamla
            tags += [d for d in discovery[2:] if d.lower() not in {t.lower() for t in tags}][:limit - 1 - len(tags)]
    if brand and brand.lower() not in {t.lower() for t in tags}:
        tags = tags[:limit - 1] + [brand]
    return (body + "\n\n" + " ".join(tags[:limit])).strip()


YT_HASHTAG_MAX = 15   # YouTube 60'tan fazlasında hepsini yok sayar; 15 güvenli ve alakalı kalır


def _footer(cfg=None):
    """Açıklama sonu: görsel kaynakları (lisans gereği) + isteğe bağlı not."""
    t = ((cfg or CFG).get("desc_footer") or "").strip()
    out = "\n\n" + t if t else ""
    if REAL_CREDITS:
        head = "Görsel kaynakları:" if (cfg or CFG).get("language_code") == "tr" else "Image credits:"
        out += "\n\n" + head + "\n" + "\n".join(f"• {c}" for c in REAL_CREDITS[:25])
    return out


def tags_last(desc):
    """Hashtag satırlarını açıklamanın en sonuna taşır (bölümler ve abone bağlantısı önce okunur)."""
    pat = r"\s*(#[\wçğıöşüÇĞİÖŞÜ]+\s*)+"
    lines = desc.split("\n")
    tags = [ln.strip() for ln in lines if re.fullmatch(pat, ln)]
    if not tags:
        return desc
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(ln for ln in lines if not re.fullmatch(pat, ln))).strip()
    uniq = list(dict.fromkeys(t for ln in tags for t in ln.split()))  # tekrar eden etiket olmasın
    return body + "\n\n" + " ".join(uniq)


def tidy_hashtags(script):
    """Açıklamadaki hashtag'leri toplar, tekrarı siler, kanal etiketini ekler ve en fazla 15'ini son satıra koyar."""
    desc = script.get("description", "")
    pat = r"#[\wçğıöşüÇĞİÖŞÜ]+"
    tags, seen = [], set()
    for t in re.findall(pat, desc):
        if len(t) > 2 and t.lower() not in seen and t.lower() != "#shorts":
            seen.add(t.lower())
            tags.append(t)
    brand = CFG.get("yt_brand_tag") or CFG.get("ig_brand_tag")
    if brand and brand.lower() not in seen:
        tags = tags[:YT_HASHTAG_MAX - 1] + [brand]
    lines = [ln for ln in desc.split("\n") if not re.fullmatch(r"\s*(" + pat + r"\s*)+", ln)]  # salt etiket satırları
    lines = [re.sub(r"(\s+" + pat + r")+\s*$", "", ln) for ln in lines]  # satır sonundaki etiketler
    body = re.sub(r"#([\wçğıöşüÇĞİÖŞÜ]+)", r"\1", "\n".join(lines)).rstrip()  # cümle içindekiler düz kelime kalsın
    body = re.sub(r"\n{3,}", "\n\n", body)
    script["description"] = (body + "\n\n" + " ".join(tags[:YT_HASHTAG_MAX])).strip() if tags else body
    return tags[:YT_HASHTAG_MAX]


def short_hashtags(script, n=6):
    tags = re.findall(r"#[\wçğıöşüÇĞİÖŞÜ]+", script.get("description", ""))
    if not tags:
        tags = ["#" + re.sub(r"\W", "", script.get("core_keyword", "") or script["tags"][0])]
    tags = [t for t in dict.fromkeys(tags) if t.lower() != "#shorts"]
    brand = CFG.get("yt_brand_tag") or CFG.get("ig_brand_tag")
    if brand and brand.lower() not in {t.lower() for t in tags[:n]}:
        tags = [t for t in tags if t.lower() != brand.lower()][:n - 1] + [brand]
    return " ".join(tags[:n])


def clean_footers():
    """Eski açıklama sonu notunu ("Stok görüntüler: ... yapay zekâ sesi ...") kanaldaki videolardan siler."""
    yt = publish._yt()
    n = 0
    for v in publish.recent_uploads(50):
        sn = v["snippet"]
        old = sn.get("description", "")
        lines = [ln for ln in old.split("\n") if not re.match(r"\s*(Stok görüntüler|Stock footage)\s*:", ln)]
        new = tags_last(re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip())
        if new == old.strip():
            continue
        sn["description"] = new
        body = {"id": v["id"], "snippet": {k: sn[k] for k in ("title", "description", "tags", "categoryId",
                                                            "defaultLanguage", "defaultAudioLanguage") if k in sn}}
        try:
            yt.videos().update(part="snippet", body=body).execute()
            n += 1
            print(f"::notice title=açıklama temizlendi::{v['id']} {sn['title'][:60]}")
        except Exception as e:
            print(f"::error title=açıklama temizlenemedi::{v['id']} {str(e)[:200]}")
    log(f"Açıklama notu silinen video: {n}")
    print(f"::notice title=toplam::{n} video temizlendi")


def refresh_videos(ids):
    """Yayındaki/yüklenmiş uzun videolara yeni tıklatan başlık + 3 adaylı yeni küçük resim (hakem seçer)."""
    yt = publish._yt()
    items = yt.videos().list(part="snippet", id=",".join(ids)).execute().get("items", [])
    ledger = media.AssetLedger(DATA / "assets_used.json")
    done = []
    for v in items:
        sn = v["snippet"]
        if CHANNEL_ID and sn.get("channelId") != CHANNEL_ID:
            log(f"{v['id']}: bu kanalın videosu değil, atlandı")
            continue
        if "#shorts" in sn["title"].lower():
            continue  # Shorts'ta küçük resim akışta görünmez; başlık zaten kısa kanca
        desc = sn.get("description", "")
        fake = {"title": sn["title"], "core_keyword": " ".join(sn["title"].split(":")[0].split()[:4]),
                "scenes": [{"narration": desc[:2500], "broll_query": CFG.get("fallback_broll", "")}]}
        old_title = sn["title"]
        title_pass(fake)
        try:
            c = llm_json(
                "You are a top YouTube thumbnail designer.",
                f"Video title: {fake['title']}\nDescription:\n{desc[:2000]}\n\nChannel look: {CFG.get('thumbnail_rules', '')}\n"
                "Give 3 clearly DIFFERENT thumbnail concepts. image_prompt IN ENGLISH: ONE striking subject, dramatic "
                "angle, big on the RIGHT half, dark empty space on the LEFT, strong rim light, high contrast, something "
                f"surprising visible, NO text. text: 1-3 words in {CFG['language']}, max 18 characters, must NOT repeat the "
                'title, adds the missing curiosity piece. JSON: {"thumbnail_concepts":[{"image_prompt":"...","text":"..."}]}',
                required=("thumbnail_concepts",))
            fake["thumbnail_concepts"] = c["thumbnail_concepts"][:3]
            with tempfile.TemporaryDirectory() as tmp:
                best, _alt = make_thumbnails(fake, Path(tmp), ledger)
                publish.set_thumbnail(v["id"], best)
                keep = THUMB_ALT / f"{v['id']}.jpg"
                if _alt:
                    keep.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy(_alt, keep)
                tg_send_file(best, f"Yeni küçük resim: {fake['title']}")
        except Exception as e:
            log(f"{v['id']}: küçük resim yenilenemedi: {str(e)[:200]}")
            print(f"::error title=refresh {v['id']}::küçük resim: {str(e)[:250]}", flush=True)
        if fake["title"] != old_title:
            body = {"id": v["id"], "snippet": {k: sn[k] for k in ("title", "description", "tags", "categoryId",
                                                                 "defaultLanguage", "defaultAudioLanguage") if k in sn}}
            body["snippet"]["title"] = fake["title"][:100]
            try:
                yt.videos().update(part="snippet", body=body).execute()
            except Exception as e:
                log(f"{v['id']}: başlık güncellenemedi: {str(e)[:200]}")
                fake["title"] = old_title
        done.append(v["id"])
        print(f"::notice title=refresh {v['id']}::{old_title} → {fake['title']}", flush=True)
        notify(f"🎨 Video yenilendi: https://youtu.be/{v['id']}\nEski başlık: {old_title}\nYeni başlık: {fake['title']}\n"
               "Yeni küçük resim yukarıda.")
    ledger.save()
    return done


def retag_videos(ids):
    """Yüklenmiş videoların açıklamasındaki hashtag'leri yeni kurala göre yeniler (uzun: 10-15, Short: 6 + #Shorts)."""
    print(f"::notice title=retag::başladı ({len(ids)} video)", flush=True)
    yt = publish._yt()
    items = yt.videos().list(part="snippet", id=",".join(ids)).execute().get("items", [])
    print(f"::notice title=retag::{len(items)} video bulundu", flush=True)
    found = {v["id"] for v in items}
    for vid in ids:
        if vid not in found:
            log(f"{vid}: bu kanalda değil ya da okunamadı")
    for v in items:
        sn = v["snippet"]
        if CHANNEL_ID and sn.get("channelId") != CHANNEL_ID:  # öbür kanalın videosu: dokunma
            log(f"{v['id']}: bu kanalın videosu değil, atlandı")
            continue
        is_short = "#shorts" in sn["title"].lower()
        try:
            r = llm_json("You write YouTube hashtags.",
                         f"Video title: {sn['title']}\nDescription:\n{sn.get('description', '')[:3000]}\n\n"
                         f"Language: {CFG.get('language', 'English')}. Return 12-15 hashtags written ONLY in "
                         f"{CFG.get('language', 'English')} (foreign words only for proper names), most "
                         "important first (top 3 show above the title): the core search phrase joined, the specific "
                         "names/places/events/terms of THIS video, then 3-4 niche tags people follow. Every one directly "
                         "about this video; no generic spam (#viral #fyp #trending #shorts); no spaces inside a hashtag. "
                         'JSON: {"hashtags":["#..."]}', required=("hashtags",))
            new = [("#" + re.sub(r"[^\wçğıöşüÇĞİÖŞÜ]", "", h)) for h in r["hashtags"] if isinstance(h, str)]
            other = {"#shorts", "#cutaway", "#gölgedefter", "#golgedefter"}  # kanal etiketini sistem ekler
            new = [h for h in new if len(h) > 2 and h.lower() not in other]
            old = sn.get("description", "")
            lines = [ln for ln in old.split("\n") if not re.fullmatch(r"\s*(#[\wçğıöşüÇĞİÖŞÜ]+\s*)+", ln)]
            old = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).rstrip()  # eski etiket satırlarını at
            tmp = {"description": old + "\n\n" + " ".join(new)}
            tags = tidy_hashtags(tmp)
            mine = (CFG.get("yt_brand_tag") or CFG.get("ig_brand_tag") or "").lower()
            if any(t.lower() in other and t.lower() != mine for t in tags):  # öbür kanalın etiketi kalmasın
                tags = [t for t in tags if t.lower() not in other or t.lower() == mine]
                tmp["description"] = tmp["description"].rsplit("\n\n", 1)[0] + "\n\n" + " ".join(tags)
            body = tmp["description"].rsplit("\n\n", 1)[0] if tags else tmp["description"]
            line = (short_hashtags({"description": " ".join(tags)}) + " #Shorts") if is_short else " ".join(tags)
            sn["description"] = (body.rstrip() + "\n\n" + line)[:4900]
            body_ = {"id": v["id"], "snippet": {k: sn[k] for k in ("title", "description", "tags", "categoryId",
                                                                 "defaultLanguage", "defaultAudioLanguage") if k in sn}}
            yt.videos().update(part="snippet", body=body_).execute()
            log(f"{v['id']}: hashtag'ler yenilendi: {line}")
            print(f"::notice title=retag {v['id']}::{line}")
            notify(f"🏷 Hashtag'ler eklendi: {sn['title']}\nhttps://youtu.be/{v['id']}\n{line}")
        except Exception as e:
            log(f"{v['id']}: hashtag'ler yenilenemedi: {str(e)[:200]}")
            print(f"::error title=retag {v['id']}::{str(e)[:300]}")
            notify(f"ℹ️ Hashtag'ler eklenemedi: https://youtu.be/{v['id']} (ayrıntı kayıtta).")


def _reel_caption(script, sh_, first):
    tr = CFG.get("language_code") == "tr"
    title = sh_.get("title") or script.get("short_title", script["title"])
    cap = sh_.get("ig_caption") or (script.get("ig_caption") if first else None)
    if not cap:
        tags = [re.sub(r"\W", "", t) for t in script.get("tags", [])]
        tags = [t for t in tags if 3 <= len(t) <= 22][:4]
        cap = (f"{title}\n\n"
               + ("Videonun tamamı YouTube'da, link profilde 👆" if tr
                  else "Full video on YouTube, link in bio 👆") + "\n\n"
               + " ".join("#" + t for t in tags))
    return publish.ig_link_cta(fix_hashtags(cap, CFG.get("ig_brand_tag"), discovery=CFG.get("ig_discovery_tags")), tr)


IG_QUEUE = DATA / "ig_queue.json"
IG_RELEASE = CFG.get("ig_release", "ig-queue")   # kanal başına ayrı; sıradaki Reels videoları bu GitHub Release'te bekler (repoyu şişirmez)


def _gh(*args):
    import subprocess
    return subprocess.run(["gh", *args], capture_output=True, text=True, timeout=600)


def post_reels(script, idea, shorts, history, parent=None):
    """İlk Short'u hemen Instagram Reels olarak yayınlar; kalanları günde bir paylaşılmak üzere sıraya koyar
    (ig_queue.py her gün sıradakini paylaşır). YouTube manuel moddayken de çalışır."""
    if not shorts or not CFG.get("instagram_enabled") or CFG["publish_mode"] == "private" or DRY_RUN:
        return
    if not (os.getenv("IG_ACCESS_TOKEN") and os.getenv("IG_USER_ID")):
        log("Instagram anahtarları yok; Reel atlandı.")
        return
    try:
        days = publish.instagram_refresh()
        if days is not None and days < 10:
            notify(f"🆘 CLAUDE'A YAZ — Instagram anahtarının süresi {days} gün sonra doluyor, yenilenmesi gerekiyor.")
    except Exception as e:
        log(f"Instagram token yenilenemedi: {str(e)[:200]}")
    n = int(CFG.get("instagram_reels_per_video", 1))
    # 1) ÖNCE hepsini kalıcı sıraya koy (iş zaman aşımına uğrasa da Reels kaybolmasın)
    queued = []
    for k, sh_ in enumerate(shorts[:n]):
        title = sh_.get("title") or script.get("short_title", script["title"])
        cap = _reel_caption(script, sh_, k == 0)
        try:
            asset = f"{datetime.now(timezone.utc):%Y%m%d%H%M}-{slug(title, 40)}-{k + 1}.mp4"
            tmp = Path(tempfile.gettempdir()) / asset
            shutil.copy(sh_["file"], tmp)
            _gh("release", "view", IG_RELEASE).returncode == 0 or _gh(
                "release", "create", IG_RELEASE, "--title", "Instagram sırası",
                "--notes", "Otomatik: sırada bekleyen Reels videoları. Elle silme.")
            r = _gh("release", "upload", IG_RELEASE, str(tmp), "--clobber")
            if r.returncode != 0:
                raise RuntimeError(r.stderr[:200])
            item = {"asset": asset, "caption": cap, "title": title, "pillar": idea["pillar"], "parent": parent}
            q = load_json(IG_QUEUE, []) if IG_QUEUE.exists() else []
            q.insert(len(queued), item) if k == 0 or queued else q.append(item)  # bu videonunkiler sıranın başına
            save_json(IG_QUEUE, q)
            queued.append((item, sh_))
            log(f"Instagram sırasına eklendi: {title}")
        except Exception as e:
            log(f"Instagram sırasına eklenemedi: {str(e)[:200]}")
            if k == 0:
                queued.append(({"asset": None, "caption": cap, "title": title}, sh_))
    push_now(IG_QUEUE, "Instagram sırası")
    # 2) ilkini hemen paylaşmayı dene; olursa sıradan çıkar
    if not queued:
        return
    item, sh_ = queued[0]
    try:
        mid, link = publish.instagram_reel(sh_["file"], item["caption"], ai_label=CFG.get("instagram_ai_label", True))
        history.append({"date": datetime.now().isoformat(timespec="minutes"), "type": "reel",
                        "title": item["title"], "pillar": idea["pillar"], "video_id": mid, "parent": parent})
        save_json(HISTORY, history)
        if item.get("asset"):
            q = [x for x in load_json(IG_QUEUE, []) if x.get("asset") != item["asset"]]
            save_json(IG_QUEUE, q)
            push_now(IG_QUEUE, "Instagram sırası")
        notify(f"📸 Instagram Reel yayında: {link or mid}")
    except Exception as e:
        log(f"Instagram Reel hatası: {str(e)[:500]}")
        notify("ℹ️ Instagram'a Reel şimdi yüklenemedi (YouTube tarafı etkilenmedi). Sırada bekliyor, günlük "
               "Instagram paylaşımında otomatik tekrar denenecek.")


def _rescue_ig_assets(tag):
    """Paket Release'i silinmeden önce, Instagram sırasında o pakete bağlı Short varsa videosunu
    ig-queue Release'ine kopyalar. Böylece paket silinince sıradaki Reel kaybolmaz.
    Başarılıysa (ya da kurtarılacak bir şey yoksa) True döner."""
    q = load_json(IG_QUEUE, [])
    items = [it for it in q if it.get("release") == tag]
    if not items:
        return True
    ok = True
    for it in items:
        try:
            tmpdir = Path(tempfile.mkdtemp())
            r = _gh("release", "download", tag, "-p", it["asset"], "-D", str(tmpdir))
            src = tmpdir / it["asset"]
            if r.returncode != 0 or not src.exists():
                raise RuntimeError(r.stderr[:200] or "dosya yok")
            _gh("release", "view", IG_RELEASE).returncode == 0 or _gh(
                "release", "create", IG_RELEASE, "--title", "Instagram sırası",
                "--notes", "Otomatik: sırada bekleyen Reels videoları. Elle silme.")
            new = f"{datetime.now(timezone.utc):%Y%m%d%H%M}-{slug(it['title'], 40)}-kurtarilan.mp4"
            dst = tmpdir / new
            src.rename(dst)
            r = _gh("release", "upload", IG_RELEASE, str(dst), "--clobber")
            if r.returncode != 0:
                raise RuntimeError(r.stderr[:200])
            it["asset"] = new
            it.pop("release", None)
            log(f"Instagram sırasındaki video paketten kurtarıldı: {it['title']}")
        except Exception as e:
            ok = False
            log(f"Instagram sırasındaki video paketten kurtarılamadı ({it['title']}): {str(e)[:200]}")
    save_json(IG_QUEUE, q)
    return ok


MANUAL_DIR = DATA / "manual"
MANUAL_PENDING = DATA / "manual_pending.json"


def _tnorm(t):
    """Başlık karşılaştırması: büyük/küçük harf, boşluk ve noktalama farkı önemsiz ("Nedir ?" = "Nedir?")."""
    return re.sub(r"[\W_]+", "", (t or "").lower())


def finish_manual_uploads():
    """Sahibinin elle yüklediği videoları başlığından bulur; açıklama, etiket, küçük resim, altyazı, oynatma listesi,
    yayın zamanı ve yorumu ekler. Shorts'un açıklamasına uzun videonun bağlantısını koyar."""
    if DRY_RUN or not MANUAL_PENDING.exists() or not os.getenv("YT_REFRESH_TOKEN"):
        return
    pend = load_json(MANUAL_PENDING, [])
    if not pend:
        return
    try:
        ups = publish.recent_uploads()
    except Exception as e:
        log(f"Kanal videoları okunamadı: {str(e)[:150]}")
        return
    used = set()
    for pk in pend:
        folder = MANUAL_DIR / pk["key"]
        long_item = next((it for it in pk["items"] if it["kind"] == "long"), None)
        for it in pk["items"]:
            if it.get("done"):
                continue
            if it["kind"] == "short" and not (long_item and long_item.get("video_id")):
                continue  # önce uzun video bulunmalı (Short açıklamasına bağlantısı girecek)
            v = next((u for u in ups if u["id"] not in used and
                      _tnorm(u["title"]) in (_tnorm(it["title"]), _tnorm(it["marker"]),
                                             _tnorm(it["title"].replace(" #Shorts", "")))), None)
            if not v:
                if it["kind"] == "long":
                    log(f"Elle yüklenen uzun video bulunamadı: '{it['title']}'. Kanaldaki son yüklemeler: "
                        + " | ".join(f"{u['title']} ({u['status'].get('privacyStatus')})" for u in ups[:6]))
                continue
            used.add(v["id"])
            desc = it["description"].replace("{LONG_URL}", f"https://youtu.be/{long_item['video_id']}"
                                             if long_item and long_item.get("video_id") else "")
            pub = it.get("publish_at")
            now_ = datetime.now(timezone.utc)
            if pub and datetime.fromisoformat(pub.replace("Z", "+00:00")) < now_ + timedelta(minutes=20):
                # planlanan saat geçtiyse bir sonraki uygun yayın saatine kaydır
                slots = CFG.get("shorts_slots" if it["kind"] == "short" else "publish_slots")
                nxt = next_slot(now_ + timedelta(minutes=30), slots) if slots else now_ + timedelta(hours=1)
                pub = nxt.strftime("%Y-%m-%dT%H:%M:%SZ")
            m = meta(it["title"], desc, it["tags"], pub, is_short=it["kind"] == "short")
            thumb = folder / it["thumb"] if it.get("thumb") and (folder / it["thumb"]).exists() else None
            try:
                notes = publish.finish_video(v, m, thumb)
            except Exception as e:
                log(f"Elle yüklenen video tamamlanamadı ({v['id']}): {str(e)[:200]}")
                continue
            caps = list(it.get("captions", []))
            sub = CFG.get("subtitle_translation") or {}
            if caps and sub.get("code") and not any(c[1] == sub["code"] for c in caps):
                # paket çevirisiz kaldıysa ana altyazıdan şimdi çevir
                try:
                    tfile = translate_srt(folder / caps[0][0], folder / f"cap_{sub['code']}.srt", sub["name"])
                    if tfile:
                        caps.append([f"cap_{sub['code']}.srt", sub["code"], sub.get("label", sub["name"])])
                except Exception as e:
                    log(f"Eksik altyazı çevrilemedi: {str(e)[:150]}")
            for fn, code, name in caps:
                if (folder / fn).exists():
                    try:
                        publish.upload_caption(v["id"], folder / fn, code, name)
                    except Exception as e:
                        log(f"altyazı {code} eklenemedi: {str(e)[:150]}")
                        notes.append(f"{name} altyazısı eklenemedi (Claude'a söylersen ekler)")
            if it.get("playlist"):
                add_to_pillar_playlist_by_title(v["id"], it["playlist"])
            if it.get("comment"):
                queue_comment(v["id"], it["comment"])
            it["done"], it["video_id"] = True, v["id"]
            log(f"Elle yüklenen video tamamlandı: {v['id']} {it['title']} {notes}")
            when = ""
            try:
                when = f"\n🕒 Yayına gireceği zaman (TR): {fmt_local(datetime.fromisoformat(m['publish_at'].replace('Z', '+00:00')))}" \
                    if m.get("publish_at") else ""
            except Exception:
                pass
            notify(f"✅ Yüklediğin video tamamlandı: {it['title']}\nhttps://youtu.be/{v['id']}\n"
                   "Açıklama, etiketler, küçük resim, altyazılar, oynatma listesi ve yayın saati eklendi. "
                   "Senden bir şey gerekmiyor." + when
                   + (f"\nℹ️ {'; '.join(notes)}" if notes else ""))
            hist = load_json(HISTORY, [])
            for h in reversed(hist):
                if h.get("manual") and not h.get("video_id") and it["kind"] == "long" and \
                        _tnorm(h["title"])[:60] == _tnorm(it["title"])[:60]:
                    h["video_id"] = v["id"]
                    break
            else:
                if it["kind"] == "short":
                    hist.append({"date": datetime.now().isoformat(timespec="minutes"), "type": "short",
                                 "title": it["title"], "video_id": v["id"], "manual": True})
            save_json(HISTORY, hist)
    keep = []
    for pk in pend:
        age = (datetime.now() - datetime.fromisoformat(pk["created"])).days
        if all(it.get("done") for it in pk["items"]) or age > 21:
            if pk.get("release_tag") and not _rescue_ig_assets(pk["release_tag"]):
                keep.append(pk)  # Instagram sırasındaki video henüz kurtarılamadı: paketi silme, sonra tekrar dene
                continue
            shutil.rmtree(MANUAL_DIR / pk["key"], ignore_errors=True)
            if pk.get("release_id") and release.enabled():
                release.remove(pk["release_id"], pk.get("release_tag"))  # geçici paket kopyası silinir
            if pk.get("drive_folder") and drive.enabled():
                drive.trash(pk["drive_folder"])  # Drive dolmasın: tamamlanan paket çöpe
        else:
            keep.append(pk)
    save_json(MANUAL_PENDING, keep)
    sweep_releases({pk.get("release_tag") for pk in keep})


PLAYLIST_COVERS = DATA / "playlist_covers.json"


def ensure_playlist_covers():
    """Her oynatma listesine markalı kare kapak koyar (bir kez; tasarım sürümü değişirse yeniler)."""
    if DRY_RUN or not os.getenv("YT_REFRESH_TOKEN") or not PLAYLISTS.exists():
        return
    ver = 1
    done = load_json(PLAYLIST_COVERS, {}) if PLAYLIST_COVERS.exists() else {}
    for title, pid in load_json(PLAYLISTS, {}).items():
        if done.get(pid, {}).get("v") == ver or done.get(pid, {}).get("fail", 0) >= 3:
            continue
        out = Path(tempfile.mkdtemp()) / "cover.jpg"
        try:
            media.playlist_cover(title, out, logo=ASSETS / CFG.get("logo_file", "cutaway_logo.png"),
                                 accent=tuple(CFG.get("thumbnail_accent", (255, 168, 60))),
                                 bg_color=tuple(CFG.get("cover_bg", (14, 17, 24))))
            wide = Path(out).with_name("cover_wide.jpg")
            from PIL import Image as _I
            _I.open(out).crop((0, 280, 1280, 1000)).save(wide, quality=92)  # içerik zaten ortadaki 16:9 bantta
            publish.set_playlist_image(pid, out, wide)
            done[pid] = {"v": ver, "title": title}
            log(f"Oynatma listesi kapağı kondu: {title}")
        except Exception as e:
            if "quota" in str(e).lower():  # günlük YouTube hakkı bitti: deneme sayılmaz, yarın tekrar
                log("Oynatma listesi kapağı: YouTube günlük hakkı dolu, sonra denenecek")
                break
            done[pid] = {"fail": done.get(pid, {}).get("fail", 0) + 1, "err": str(e)[:900]}
            log(f"Oynatma listesi kapağı konamadı ({title}): {str(e)[:200]}")
    save_json(PLAYLIST_COVERS, done)


def sweep_releases(keep_tags):
    """Yarım kalmış/sahipsiz paket release'lerini temizler (bu kanalın, 2 günden eski, beklemede olmayan)."""
    if not release.enabled() or DRY_RUN:
        return
    try:
        import requests as rq
        rels = rq.get(f"{release.API}/repos/{release._repo()}/releases?per_page=50", headers=release._h(),
                      timeout=60).json()
        prefix = CFG["channel_name"]
        for r in rels:
            tag = r.get("tag_name", "")
            if not tag.startswith("paket-") or tag in keep_tags or not (r.get("name") or "").startswith(prefix):
                continue
            age = datetime.now(timezone.utc) - datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
            if age.days >= 2:
                release.remove(r["id"], tag)
                log(f"Sahipsiz paket temizlendi: {tag}")
    except Exception as e:
        log(f"Release temizliği yapılamadı: {str(e)[:120]}")


def add_to_pillar_playlist_by_title(video_id, title):
    cache = load_json(PLAYLISTS, {}) if PLAYLISTS.exists() else {}
    try:
        pid = publish.ensure_playlist(title, f"{CFG['channel_name']}: {title}", CFG["language_code"], cache)
        save_json(PLAYLISTS, cache)
        publish.add_to_playlist(pid, video_id)
    except Exception as e:
        log(f"Oynatma listesine eklenemedi: {str(e)[:150]}")


def produce_script(history, report):
    """Kaliteli senaryo bulana kadar dener: aynı konuda revizyon, olmazsa yeni konu.
    Hiçbiri barajı geçmezse, bilgi hatası olmayan en iyi senaryo alt sınırın üstündeyse kullanılır."""
    best = None  # (skor, fikir, senaryo)
    last_tr = ""
    tried = []
    for topic_try in range(CFG.get("max_topic_attempts", 3)):
        idea = pick_topic(history + [{"title": t, "type": "long"} for t in tried], report)
        tried.append(idea["title"])
        log(f"Konu ({topic_try+1}): {idea['title']}  [{idea['pillar']}]")
        script = ensure_length(write_script(idea))
        log(f"Senaryo yazıldı: {len(script['scenes'])} sahne, {word_count(script)} kelime")
        script = plain_pass(script)
        script, facts_ok = verify_loop(script)
        if not facts_ok:
            log("Bilgi doğrulamadan geçemedi → yeni konu")
            continue
        script = title_pass(script)
        script = seo_fix(script)
        for attempt in range(CFG["max_rewrites"] + 1):
            ok, review = quality_gate(script)
            sc = _score(review)
            log(f"Editör (min {sc[0]:.0f}, ort {sc[1]:.1f}): {review}")
            if best is None or sc > best[0]:
                best = (sc, idea, script)
            last_tr = review.get("problems_tr") or ""
            if ok:
                script["_score"] = sc
                return idea, script
            if attempt == CFG["max_rewrites"]:
                break
            notes = "\n".join(f"- {p}" for p in review.get("problems", []))
            if float(review.get("hook", 10) or 10) < CFG["min_quality_score"]:
                notes += ("\n- Rewrite the FIRST scene: open with one concrete, surprising, specific fact or "
                          "question nobody expects (no clichés, no 'did you know', no 'imagine'). "
                          "The first sentence must make the viewer need the answer.")
            if float(review.get("simplicity", 10) or 10) < 8 or float(review.get("flow", 10) or 10) < 8:
                revised = ensure_length(plain_pass(revise_script(script, notes), notes))
            else:
                revised = ensure_length(revise_script(script, notes))
            revised, facts_ok = verify_loop(revised)
            if not facts_ok:
                log("Revizyon bilgi hatası getirdi → önceki sürümde kalınıyor")
                break
            script = revised
        log(f"Konu kalite barajını geçemedi: {idea['title']}")
        if best and best[0][0] >= CFG["min_quality_score"] - 1:
            break  # bilgisi doğru, puanı 6+ : yeni konuya geçip günlük ücretsiz hakkı tüketme
    floor = CFG.get("min_quality_floor", 0)
    if best and best[0][0] >= floor:
        log(f"Baraj geçilemedi; en iyi senaryo kullanılıyor (min {best[0][0]:.0f}, ort {best[0][1]:.1f})")
        notify(f"ℹ️ Senaryo kontrolü: yapay zekâ editörü senaryoya 10 üzerinden en düşük {best[0][0]:.0f}, "
               f"ortalama {best[0][1]:.1f} puan verdi. Hedef her başlıkta en az 7. "
               "Hedefin biraz altında kaldığı için en iyi deneme kullanılıyor. Video normal şekilde üretiliyor, "
               "senden bir şey gerekmiyor.")
        best[2]["_score"] = best[0]
        return best[1], best[2]
    notify("⛔ Bugün video üretilmedi: hiçbir senaryo kalite alt sınırını geçemedi. "
           f"Bir sonraki çalışmada tekrar denenecek.\nEditör notu: {last_tr}"[:1500])
    log("Hiçbir senaryo alt sınırı geçemedi, video üretilmedi.")
    return None, None


# ---------------------------------------------------------------- ana akış
def meta(title, desc, tags, publish_at, is_short=False):
    title = re.sub(r"[<>]", "", title).strip()[:100]
    desc = re.sub(r"[<>]", "", desc)[:4900]
    return {"title": title, "description": desc, "tags": [t for t in tags if len(t) >= 2][:40],
            "category_id": CFG["category_id"], "lang": CFG["language_code"],
            "made_for_kids": bool(CFG.get("made_for_kids", False)),
            "synthetic": bool(CFG.get("declare_synthetic", True)),
            "privacy": CFG["publish_mode"] if CFG["publish_mode"] != "scheduled" else "private",
            "publish_at": publish_at, "is_short": is_short}


BRAND_DONE = (DATA / "branding_done.json") if CFG.get("data_dir") else (ROOT / "assets" / "branding_done.json")
PENDING_CAPS = DATA / "pending_captions"


def save_pending_caption(video_id, srt_path, lang, name):
    """Yüklenemeyen altyazıyı repoda saklar; izin yenilenince sonraki çalışma yükler."""
    PENDING_CAPS.mkdir(exist_ok=True)
    dst = PENDING_CAPS / f"{video_id}__{lang}.srt"
    shutil.copy(srt_path, dst)
    (PENDING_CAPS / f"{video_id}__{lang}.json").write_text(json.dumps({"name": name}), encoding="utf-8")


def retry_pending_captions():
    if DRY_RUN or not PENDING_CAPS.exists() or CFG.get("uploader", "youtube") != "youtube":
        return
    files = sorted(PENDING_CAPS.glob("*.srt"))
    if not files:
        return
    done = 0
    for f in files:
        vid, lang = f.stem.split("__")
        meta_f = f.with_suffix(".json")
        name = json.loads(meta_f.read_text(encoding="utf-8")).get("name", lang) if meta_f.exists() else lang
        try:
            publish.upload_caption(vid, f, lang, name)
            f.unlink()
            meta_f.unlink(missing_ok=True)
            done += 1
        except Exception as e:
            log(f"Bekleyen altyazı yüklenemedi ({f.name}): {str(e)[:150]}")
            break  # izin hâlâ yoksa diğerlerini de zorlamayalım
    if done:
        log(f"Bekleyen {done} altyazı geriye dönük yüklendi")
        notify(f"📝 Önceki videoların {done} altyazısı yüklendi.")


def ensure_branding():
    """İlk gerçek çalışmada kanal afişini ve açıklamasını bir kez ayarlar."""
    b = CFG.get("branding") or {}
    ver = b.get("version", 1)
    done = load_json(BRAND_DONE, {}) if BRAND_DONE.exists() else {}
    if DRY_RUN or not b or not os.getenv("YT_REFRESH_TOKEN") or done.get("version", 1) >= ver and done:
        return
    try:
        cid = publish.set_branding(banner=ROOT / b["banner"] if b.get("banner") else None,
                                   description=b.get("description"), keywords=b.get("keywords"),
                                   country=b.get("country"), lang=CFG["language_code"])
        save_json(BRAND_DONE, {"date": datetime.now().isoformat(timespec="minutes"), "channel": cid,
                               "version": ver})
        notify("🎨 Kanal afişi ve açıklaması ayarlandı.")
    except Exception as e:  # marka ayarı başarısız olsa da video üretimi devam etsin
        log(f"Kanal markası ayarlanamadı: {e}")
        notify("ℹ️ Kanal afişi/açıklaması güncellenemedi. Video üretimi etkilenmedi, devam ediyor.")


def clear_old_localizations():
    """localize_metadata kapalıysa daha önce eklenen çeviri başlık/açıklamaları bir kez kaldırır."""
    flag = DATA / "localizations_cleared.json"
    if CFG.get("localize_metadata", True) or DRY_RUN or flag.exists() or not os.getenv("YT_REFRESH_TOKEN"):
        return
    try:
        n = publish.clear_localizations()
        save_json(flag, {"date": datetime.now().isoformat(timespec="minutes"), "videos": n})
        log(f"Eski videolardan çeviri başlık/açıklama kaldırıldı: {n} video")
        if n:
            notify(f"🧹 {n} eski videonun Türkçe başlık/açıklaması kaldırıldı; artık her şey İngilizce.")
    except Exception as e:
        log(f"Eski çeviriler kaldırılamadı: {str(e)[:200]}")


CHANNEL_ID = None


def _check_gemini_key():
    """Kanalın Gemini anahtarı çalışmıyorsa (ör. ön ödemeli bakiye bitti/henüz yüklenmedi → 402) öbür anahtara geç."""
    key = os.getenv("GEMINI_API_KEY", "")
    if not key or DRY_RUN and os.getenv("SELFTEST"):
        return
    try:
        r = requests.post(f"{ai.GEMINI}/gemini-flash-lite-latest:generateContent", headers={"x-goog-api-key": key},
                          json={"contents": [{"parts": [{"text": "ok"}]}]}, timeout=60)
    except Exception:
        return
    if r.status_code in (402, 403):
        alt = next((k for k in ai.TTS_KEYS if k and k != key), None)
        if alt:
            os.environ["GEMINI_API_KEY"] = alt
            ai.TTS_KEYS = [alt] + [k for k in ai.TTS_KEYS if k not in (alt, key)]
            CFG["paid_tier"] = False
            log(f"Gemini anahtarı çalışmıyor ({r.status_code}) → öbür kanalın anahtarına geçildi")
            notify("ℹ️ Ücretli Gemini hesabında bakiye görünmüyor (ödeme henüz yansımamış olabilir). "
                   "Video bugün ücretsiz anahtarla üretiliyor, senden bir şey gerekmiyor; Claude'a haber ver.")


CREDIT_LEDGER = ROOT / "credit_ledger.json"   # iki kanalın ortak (tahmini) kredi defteri


def credit_report(stage):
    """Telegram'a kredi bilgisi: yapım başında kalan kredi, sonunda bu videonun tahmini maliyeti ve yeni bakiye.
    Google bakiyeyi API ile vermediği için kullanılan token/ses/görselden hesaplanan TAHMİNDİR."""
    if DRY_RUN:
        return
    led = load_json(CREDIT_LEDGER, {}) if CREDIT_LEDGER.exists() else {}
    led.setdefault("cloud_tl", 14695.0)     # Google Cloud deneme kredisi (Vertex AI), 29 Ocak 2027'ye kadar
    led.setdefault("studio_tl", 249.89)     # AI Studio ön ödemeli Gemini kredisi
    rate = float(led.setdefault("usd_tl", 49.0))
    if stage == "start":
        notify(f"💳 Yapım başlıyor. Tahmini kalan kredi: Google Cloud {led['cloud_tl']:,.0f} TL · "
               f"AI Studio {led['studio_tl']:,.2f} TL")
        return
    v_usd, s_usd = ai.cost_usd()
    v_tl, s_tl = v_usd * rate, s_usd * rate
    led["cloud_tl"] = round(led["cloud_tl"] - v_tl, 2)
    led["studio_tl"] = round(led["studio_tl"] - s_tl, 2)
    led.setdefault("runs", []).append({"date": datetime.now().isoformat(timespec="minutes"),
                                       "channel": CFG["channel_name"], "cloud_tl": round(v_tl, 2),
                                       "studio_tl": round(s_tl, 2)})
    led["runs"] = led["runs"][-200:]
    save_json(CREDIT_LEDGER, led)
    tok = sum(i + o for i, o, _ in ai.USAGE["tokens"].values())
    notify(f"💰 Bu videonun tahmini maliyeti: {v_tl + s_tl:,.1f} TL (Cloud {v_tl:,.1f} · AI Studio {s_tl:,.1f}).\n"
           f"Kalan tahmini kredi: Google Cloud {led['cloud_tl']:,.0f} TL · AI Studio {led['studio_tl']:,.2f} TL\n"
           f"(Kullanım: {tok:,} token, {sum(ai.USAGE['tts_seconds'].values()) / 60:.0f} dk ses, "
           f"{sum(ai.USAGE['images'].values())} görsel. Tahmindir; kesin tutar Google Billing sayfasında.)")


def check_channel():
    """İzin anahtarı bu config'in kanalına mı bağlı? Değilse hiçbir şey yüklemeden dur."""
    if DRY_RUN or CFG.get("uploader", "youtube") != "youtube" or not os.getenv("YT_REFRESH_TOKEN"):
        return
    global CHANNEL_ID
    try:
        title, CHANNEL_ID = publish.my_channel()
    except Exception as e:
        log(f"Kanal adı kontrol edilemedi: {str(e)[:150]}")
        return
    norm = lambda x: (x or "").replace("İ", "i").replace("I", "ı").lower().replace(" ", "")
    ok_names = {norm(n) for n in [CFG["channel_name"]] + list(CFG.get("channel_aliases") or [])}
    if CFG.get("channel_id"):  # kimlik biliniyorsa ad değişse de doğru kanal tanınır
        if CHANNEL_ID != CFG["channel_id"]:
            raise RuntimeError(f"YANLIŞ KANAL: izin anahtarı '{title}' ({CHANNEL_ID}) kanalına bağlı, beklenen "
                               f"'{CFG['channel_name']}'. Hiçbir şey yüklenmedi; get_token.bat ile doğru kanalı seç.")
    elif title and norm(title) not in ok_names:
        raise RuntimeError(f"YANLIŞ KANAL: izin anahtarı '{title}' kanalına bağlı, beklenen "
                           f"'{CFG['channel_name']}'. Hiçbir şey yüklenmedi; get_token.bat ile doğru kanalı seç.")
    log(f"Kanal doğrulandı: {title}")


def push_now(path, msg):
    """Önemli kaydı hemen depoya yazar (iş zaman aşımına uğrarsa da kaybolmasın). Yalnız GitHub'da, hata olursa sessiz."""
    if DRY_RUN or not os.getenv("GITHUB_ACTIONS"):
        return
    try:
        import subprocess
        run = lambda *a: subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, timeout=60)
        run("config", "user.name", "otoyt-bot")
        run("config", "user.email", "otoyt@users.noreply.github.com")
        run("add", str(path))
        if run("commit", "-m", f"otoyt: {msg}").returncode != 0:
            return
        for _ in range(3):
            run("pull", "--rebase", "--autostash", "-X", "theirs", "-q")  # çalışma klasörü kirli olsa da
            if run("push", "-q").returncode == 0:
                return
    except Exception as e:
        log(f"Kayıt hemen yazılamadı: {str(e)[:100]}")


def preflight():
    """Üretimden ÖNCE dış hesapları kontrol et: sorun varsa saatler sonra değil hemen haber ver."""
    if DRY_RUN:
        return
    if CFG.get("instagram_enabled") and os.getenv("IG_ACCESS_TOKEN") and os.getenv("IG_USER_ID"):
        try:
            user = publish.instagram_check()
            log(f"Instagram anahtarı geçerli: @{user}")
        except Exception as e:
            notify("🆘 CLAUDE'A YAZ — Instagram anahtarı çalışmıyor (süresi dolmuş olabilir). Video yine YouTube'a "
                   "yüklenecek, Reels sırada bekleyecek. Anahtar yenilenince otomatik paylaşılır.\n"
                   f"Ayrıntı: {str(e)[:150]}")


def run_once():
    if os.getenv("GITHUB_EVENT_NAME") == "schedule" and not os.getenv("CATCHUP"):
        today = datetime.now().date().isoformat()
        if any(h.get("type", "long") == "long" and str(h.get("date", "")).startswith(today)
               for h in (load_json(HISTORY, []) if HISTORY.exists() else [])):
            log("Bugünün videosu zaten (elle başlatılan çalışmayla) üretildi; zamanlanmış çalışma atlandı.")
            return True
    check_channel()
    preflight()
    if not DRY_RUN:
        notify("🎬 Yeni video yapımı başladı: önce konu seçilecek ve senaryo yazılacak.")
    ensure_branding()
    clear_old_localizations()
    retry_pending_captions()
    history = load_json(HISTORY, [])
    ledger = media.AssetLedger(DATA / "assets_used.json")

    report = publish.performance_report(history)
    log(f"Analitik: {report['n']} video ölçüldü, sütun puanları: {report['pillar_scores']}")
    finish_manual_uploads()
    post_due_comments()
    tally_votes()
    ensure_playlist_covers()
    maybe_swap_thumbnails(history)
    PENDING_SCRIPT = DATA / "pending_script.json"
    pend = load_json(PENDING_SCRIPT, None) if PENDING_SCRIPT.exists() else None
    if pend and pend.get("script") and (datetime.now() - datetime.fromisoformat(pend["date"])).days <= 7:
        # önceki çalışma hata/zaman aşımıyla yarıda kaldı: hazır (kalite ve bilgi kontrolünden geçmiş) senaryo çöpe gitmesin
        idea, script = pend["idea"], pend["script"]
        log(f"Yarıda kalan videonun senaryosu kullanılıyor: {script['title']}")
        notify(f"♻️ Önceki denemede yarıda kalan konu yeniden yapılıyor: {script['title']}{tr_note(script['title'])}")
    else:
        idea, script = produce_script(history, report)
        if script is not None:
            save_json(PENDING_SCRIPT, {"date": datetime.now().isoformat(timespec="minutes"), "idea": idea,
                                       "script": script})
            push_now(PENDING_SCRIPT, "yarım kalırsa diye senaryo kaydı")
    if script is None:
        return False
    kw = (script.get("core_keyword") or "").strip()  # revizyonlardan sonra da garanti
    if kw and _norm(kw) not in _norm(script.get("description", "")[:120]):
        script["description"] = f"{kw[:1].upper() + kw[1:]}: {script['description']}"
    tidy_hashtags(script)
    script["tags"] = build_tags(script)
    sc_ = script.get("_score") or (0, 0)
    est = word_count(script) / CFG.get("words_per_minute", 130)
    notify(f"📌 Konu seçildi: {script['title']}{tr_note(script['title'], idea['pillar'])}\nBölüm: {idea['pillar']}\n"
           f"Senaryo puanı (10 üzerinden): en düşük {sc_[0]:.0f}, ortalama {sc_[1]:.1f} · "
           f"{len(script['scenes'])} sahne, yaklaşık {est:.0f} dakika.\nBilgiler kaynaklarla kontrol edildi. "
           "Şimdi seslendirme ve görsellere geçiliyor.")

    with tempfile.TemporaryDirectory() as tmp:
        w = Path(tmp)
        log(f"Seslendirme başlıyor ({'Gemini ' + CFG['voice'].get('name', '') if CFG['voice'].get('provider') == 'gemini' else 'Kokoro'})...")
        narrate(script, w)
        dur_ = sum(s['duration'] for s in script['scenes'])
        log(f"Seslendirme: {dur_:.0f} sn")
        st_ = voice.ALIGN_STATS
        if st_["total"] and st_["ok"] == st_["total"]:
            cap_ = "Altyazılar kontrol edildi: her kelimenin zamanı sesten ölçüldü, altyazı sesle birebir uyumlu ✅"
        elif st_["ok"]:
            cap_ = (f"Altyazılar kontrol edildi: {st_['total']} cümlenin {st_['ok']} tanesinde kelime zamanı sesten "
                    "ölçüldü, kalanında tahmini zaman kullanıldı (küçük kayma olabilir) ℹ️")
        else:
            cap_ = "ℹ️ Altyazı zamanları sesten ölçülemedi, tahmini zaman kullanıldı. Claude'a 'altyazı' demen yeterli."
        notify(f"🎙 Seslendirme bitti ({dur_ / 60:.0f} dk {dur_ % 60:.0f} sn).\n{cap_}")
        thumb, thumb_alt = make_thumbnails(script, w, ledger)  # önce: ücretsiz görsel hakkı sahnelere gitmeden
        build_scene_videos(script, w, ledger)
        vs_ = VIS_STATS
        notify(f"🖼 Görseller hazırlandı: {vs_['scenes']} sahnenin {vs_['planned']} tanesinde görsel cümle cümle "
               f"seçildi (anlatılan neyse ekranda o)."
               + (f" Konuya uymayan {vs_['rejected']} stok video elendi." if vs_["rejected"] else "")
               + (f" {vs_['real']} yerde anlatılan yerin/nesnenin GERÇEK fotoğrafı kullanıldı (Wikimedia Commons)." if vs_.get("real") else "")
               + (" ℹ️ Bazı sahnelerde eski yöntem kullanıldı." if vs_["planned"] < vs_["scenes"] else " ✅"))
        variants = sorted(ASSETS.glob("music_*.mp3"))
        music = random.choice(variants) if variants else ASSETS / "music.mp3"  # her videoda farklı müzik
        final, words = assemble(script, w, music)
        log(f"Ana video: {media.duration(final):.0f} sn")
        fd_ = media.duration(final)
        notify(f"🎞 Video birleştirildi ({fd_ // 60:.0f}:{fd_ % 60:02.0f}). Şimdi Shorts, küçük resim ve "
               "altyazı çevirisi hazırlanıyor.")

        shorts = []
        if CFG.get("make_shorts", True):
            for k, plan in enumerate(plan_shorts(script)):
                try:
                    rng = script["scenes"][plan["start"]:plan["end"] + 1]
                    f, slen = media.make_short([(s["video"], s["voice"], s["words"]) for s in rng],
                                               w / f"short_{k+1}.mp4", w, music=music, tag=f"short{k+1}")
                    if slen >= 15:
                        shorts.append({**plan, "file": f, "len": slen})
                        log(f"Short {k+1}: sahne {plan['start']}-{plan['end']}, {slen:.0f} sn — {plan['title']}")
                except Exception as e:
                    log(f"Short {k+1} üretilemedi: {e}")
        short = shorts[0]["file"] if shorts else None

        # altyazı izleri: İngilizce (doğru metin) + Türkçe (çeviri)
        cap_blocks = media.srt_blocks(words, per_line=8)
        en_srt = media.write_srt_blocks(cap_blocks, w / f"captions_{CFG.get('language_code', 'en')}.srt")
        tr_srt, tr_blocks, tr_meta = None, None, None
        sub_lang = CFG.get("subtitle_translation") or {}
        if sub_lang.get("code"):
            log(f"Altyazı çevirisi: {sub_lang['name']} ({len(cap_blocks)} satır)")
            tr_text = translate_lines([t for _, _, t in cap_blocks], sub_lang["name"])
            if tr_text:
                tr_blocks = [(a, b, t) for (a, b, _), t in zip(cap_blocks, tr_text)]
                tr_srt = media.write_srt_blocks(tr_blocks, w / f"captions_{sub_lang['code']}.srt")
            tr_meta = local_meta(script, shorts, sub_lang["name"])

        script["_final"] = str(final)
        OUT.mkdir(exist_ok=True)
        for f in [final, thumb, en_srt, tr_srt] + [s["file"] for s in shorts]:
            if f:
                shutil.copy(f, OUT / Path(f).name)
        save_json(OUT / "script.json", script)

        if CFG["publish_mode"] == "manual" and not DRY_RUN:
            # YouTube API onayı gelene kadar: sistem üretir, sahibi Studio'dan elle yükler (kilit yok)
            post_reels(script, idea, shorts, history)  # Instagram'a otomatik (paket dosyaları silinmeden önce)
            make_manual_package(script, idea, shorts, thumb, en_srt, tr_srt, history, ledger)
            return

        if DRY_RUN:
            ledger.save()
            log("DRY_RUN anlatım metni:\n" + "\n".join(f"  [{k + 1}] {s['narration']}" for k, s in enumerate(script["scenes"])))
            if tr_blocks:
                log("DRY_RUN çeviri altyazı örneği:\n" + "\n".join(f"  {t}" for _, _, t in tr_blocks[:25]))
            notify(f"🧪 Sistem testi (senden bir şey gerekmiyor, yüklenmeyecek): {tr_title(tr_meta, script['title'])}")
            log("DRY_RUN: yükleme atlandı, dosyalar out/ klasöründe.")
            return

        now = datetime.now(timezone.utc)
        sched = CFG["publish_mode"] == "scheduled"
        main_t, short_ts = plan_times(now, len(shorts))
        iso = lambda t: t.strftime("%Y-%m-%dT%H:%M:%SZ") if sched else None
        up = CFG.get("uploader", "youtube")

        global UPLOAD_STARTED
        UPLOAD_STARTED = True
        chs = script.get("_chapters") or []
        sub_link = (f"\n\n{CFG.get('subscribe_text', '🔔 Subscribe for more:')} "
                    f"https://www.youtube.com/channel/{CHANNEL_ID}?sub_confirmation=1") if CHANNEL_ID else ""
        desc = (script["description"] + chapters_text(chs, header=CFG.get("chapters_heading", "Chapters"))
                + watch_next_line(idea["pillar"]) + sub_link + _footer())
        desc = tags_last(desc)
        m_long = meta(script["title"], desc, script["tags"], iso(main_t))
        if tr_meta and CFG.get("localize_metadata", True):
            tr_desc = (tr_meta["description"]
                       + chapters_text(chs, tr_meta.get("chapters"), sub_lang.get("chapters_heading", "Bölümler"))
                       + _footer(sub_lang))
            m_long["localizations"] = {sub_lang["code"]: {"title": tr_meta["title"][:100],
                                                          "description": tr_desc[:4900]}}
        named = w / f"{slug(script.get('core_keyword') or script['title'])}.mp4"  # anlamlı dosya adı
        shutil.copy(final, named)
        vid = publish.upload(named, m_long, thumb, up)
        queue_comment(vid, script.get("pinned_comment"))
        record_vote(vid, script)
        (DATA / "pending_script.json").unlink(missing_ok=True)  # yüklendi: yarım kalan senaryo kaydı silinir
        mark_poll_used()
        if thumb_alt:
            THUMB_ALT.mkdir(parents=True, exist_ok=True)
            shutil.copy(thumb_alt, THUMB_ALT / f"{vid}.jpg")
        cap_warned = False
        for srt_path, code, name in ((en_srt, CFG.get("language_code", "en"), CFG.get("caption_label", "English")),
                                     (tr_srt, sub_lang.get("code"), sub_lang.get("label"))):
            if not srt_path or up != "youtube":
                continue
            try:
                publish.upload_caption(vid, srt_path, code, name)
                log(f"Altyazı eklendi: {name}")
            except Exception as e:
                log(f"Altyazı eklenemedi ({name}): {str(e)[:200]}")
                save_pending_caption(vid, srt_path, code, name)
                if not cap_warned and ("insufficient" in str(e).lower() or "403" in str(e)):
                    notify("🆘 CLAUDE'A YAZ — Altyazılar şimdilik yüklenemedi: YouTube izninin yenilenmesi gerekiyor "
                           "(get_token.bat bir kez). Altyazılar saklandı, izin yenilenince otomatik yüklenecek.")
                    cap_warned = True
        add_to_pillar_playlist(vid, idea["pillar"])
        history.append({"date": datetime.now().isoformat(timespec="minutes"), "type": "long",
                        "title": script["title"], "pillar": idea["pillar"], "video_id": vid,
                        "series_id": idea.get("series_id")})
        save_json(HISTORY, history)
        ledger.save()
        if vid in publish.UPLOAD_WARNINGS:
            notify(f"🆘 CLAUDE'A YAZ — Video yüklendi ama YouTube yayın saatini kaydetmedi: https://youtu.be/{vid}\n"
                   f"Studio'dan Görünürlük → Planla → {fmt_local(main_t)} seç (bir kerelik).")
        notify(f"✅ Video yüklendi: {tr_title(tr_meta, script['title'])}\nhttps://youtu.be/{vid}\n"
               + (f"Yayın: {fmt_local(main_t)} (TR saati). " if sched else "Şimdilik gizli (private). ")
               + "Beğenmezsen Studio'dan sil.")

        for k, sh_ in enumerate(shorts):
            try:
                title = sh_["title"][:90].rstrip() + " #Shorts"
                sdesc = (f"{sh_['title']}\n\n{CFG.get('full_video_text', 'Watch the full video:')} https://youtu.be/{vid}\n\n"
                         + short_hashtags(script) + " #Shorts")
                m_short = meta(title, sdesc, script["tags"], iso(short_ts[k]), is_short=True)
                trt = (tr_meta or {}).get("short_titles") or []
                if k < len(trt) and trt[k] and CFG.get("localize_metadata", True):
                    m_short["localizations"] = {sub_lang["code"]: {
                        "title": str(trt[k])[:90] + " #Shorts",
                        "description": f"{trt[k]}\n\n{sub_lang.get('full_video_text', 'Videonun tamamı:')} "
                                       f"https://youtu.be/{vid}\n#Shorts"}}
                sname = w / f"{slug(sh_['title'])}-shorts.mp4"
                shutil.copy(sh_["file"], sname)
                sid = publish.upload(sname, m_short, None, up)
                # telefondan izleyen Short'tan uzun videoya geçebilsin: tıklanabilir bağlantılı kanal yorumu
                # (YouTube API "ilgili video" bağlantısını desteklemiyor; yorum Short yayına girince yazılır)
                queue_comment(sid, ("▶️ Videonun tamamı burada: " if CFG.get("language_code") == "tr"
                                    else "▶️ Watch the full video here: ") + f"https://youtu.be/{vid}")
                if tr_blocks and up == "youtube" and CFG.get("short_captions", False):  # YouTube kotası: Short'ta yazı zaten gömülü
                    off = script["scenes"][sh_["start"]].get("t0", 0.0)
                    sp = media.write_srt_blocks(shift_blocks(tr_blocks, off, sh_["len"]),
                                                w / f"short{k+1}_{sub_lang['code']}.srt")
                    try:
                        if cap_warned:
                            raise RuntimeError("izin yok, sonraya bırakıldı")
                        publish.upload_caption(sid, sp, sub_lang["code"], sub_lang["label"])
                    except Exception as e:
                        log(f"Short {k+1} altyazısı eklenemedi: {str(e)[:200]}")
                        save_pending_caption(sid, sp, sub_lang["code"], sub_lang["label"])
                history.append({"date": datetime.now().isoformat(timespec="minutes"), "type": "short",
                                "title": sh_["title"], "pillar": idea["pillar"], "video_id": sid, "parent": vid})
                save_json(HISTORY, history)
                trs = ((tr_meta or {}).get("short_titles") or [None] * (k + 1))
                if CFG.get("language_code") == "tr":
                    trs = [None] * (k + 1)
                notify(f"📱 Short {k+1}/{len(shorts)} yüklendi: {trs[k] if k < len(trs) and trs[k] else sh_['title']}\n"
                       + (f"(yayın: {fmt_local(short_ts[k])} TR saati) " if sched else "(şimdilik gizli) ")
                       + f"https://youtu.be/{sid}")
            except Exception as e:
                notify(f"ℹ️ {k+1}. Short YouTube'a yüklenemedi. Uzun video etkilenmedi; ayrıntı kayıtta.")
                if "quota" in str(e).lower() or "uploadLimit" in str(e):
                    break  # günlük yükleme hakkı doldu; kalanları zorlamayalım

        post_reels(script, idea, shorts, history, parent=vid)
        for f in OUT.glob("*"):
            f.unlink()
    log(f"Bitti: https://youtu.be/{vid}")


if __name__ == "__main__":
    RUNLOG.parent.mkdir(exist_ok=True)
    if os.getenv("OTOYT_ATTEMPT", "1") == "1":  # 2. denemede 1. denemenin kaydı silinmesin
        RUNLOG.write_text("", encoding="utf-8")
    ai.LOG = log
    log(f"Başladı (test modu: {DRY_RUN}, yayın: {CFG['publish_mode']})")
    _check_gemini_key()
    if not CFG.get("paid_tier") and not ai.vertex_on():  # Cloud kredisi varsa ses Vertex'ten: bekleme gerekmez
        ai.TTS_GAP = 21.0
    else:
        ai.TTS_GAP = 1.0
    if CFG.get("paid_tier") and os.getenv("GEMINI_PAID_API_KEY"):  # ücretli anahtar: dakika sınırları yüksek → beklemeleri kısalt
        ai.MIN_GAP.update({"lite": 1.0, "default": 2.0})
        for k in ("text_models", "judge_models", "factcheck_models"):
            if CFG.get("paid_" + k):
                CFG[k] = CFG["paid_" + k]
        log("Ücretli Gemini anahtarı kullanılıyor")
    if os.getenv("CATCHUP"):  # telafi çalışması: önceki gün ses hakkı yüzünden video çıkmadıysa üret
        mf = DATA / "missed.json"
        if not mf.exists():
            log("Telafi gerekmiyor.")
            sys.exit(0)
        mf.unlink()
        log("Telafi çalışması: dünkü üretim yapılamamıştı, şimdi üretiliyor.")
    if os.getenv("ADD_TRANSLATION"):  # yüklenmiş videoya eksik çeviri altyazısını ekle: video kimlikleri, virgülle
        check_channel()
        sub = CFG.get("subtitle_translation") or {}
        for vid in [x.strip() for x in os.getenv("ADD_TRANSLATION").split(",") if x.strip()]:
            try:
                src = publish.download_caption(vid, CFG.get("language_code", "en"))
            except Exception as e:  # video bu kanalın değil (iki kanal sırayla çalışır)
                log(f"{vid}: bu kanalda değil ya da okunamadı: {str(e)[:120]}")
                continue
            main_lc = CFG.get("language_code", "en")
            try:  # ana dil izini de üste taşı (gömülü yazıyla çakışmasın)
                mp = Path(tempfile.mkdtemp()) / "main.srt"
                mp.write_text(src, encoding="utf-8")
                publish.replace_caption(vid, mp, main_lc, CFG.get("caption_label", "English"))
            except Exception as e:
                log(f"{vid}: ana altyazı üste taşınamadı: {str(e)[:150]}")
            try:
                f = translate_srt(src, Path(tempfile.mkdtemp()) / "t.srt", sub["name"]) if src else None
                if not f:
                    raise RuntimeError("ana altyazı bulunamadı ya da çeviri başarısız")
                publish.replace_caption(vid, f, sub["code"], sub.get("label", sub["name"]))
                log(f"{vid}: {sub['name']} altyazı eklendi")
                notify(f"✅ {sub.get('label', sub['name'])} altyazı eklendi: https://youtu.be/{vid}")
            except Exception as e:
                log(f"{vid}: çeviri altyazısı eklenemedi: {str(e)[:200]}")
                notify(f"ℹ️ Videoya çeviri altyazısı eklenemedi: https://youtu.be/{vid} (ayrıntı kayıtta).")
        sys.exit(0)
    if os.getenv("RETAG") == "footer":  # kanaldaki videoların açıklamasından eski kaynak/yapay zekâ notunu sil
        check_channel()
        clean_footers()
        sys.exit(0)
    if os.getenv("REFRESH"):  # başlık + küçük resim yenile: video kimlikleri, virgülle
        check_channel()
        spec = os.getenv("REFRESH")
        if "SON" in spec:  # "SON" = kanalın en son yüklenen (planlı) uzun videosu (kimliği bilmeden işlem)
            ups = [v for v in publish.recent_uploads(15) if "#shorts" not in v["title"].lower()]
            if ups:
                spec = spec.replace("SON", ups[0]["id"])
                print(f"::notice title=SON::{ups[0]['id']} {ups[0]['title']}", flush=True)
        if spec.startswith("cok:"):  # birden çok işlem sırayla: "cok:yayinla:ID;;ID|Başlık"
            parts = spec[4:].split(";;")
        else:
            parts = [spec]
        for spec in parts[:-1]:
            os.environ["REFRESH"] = spec
            subprocess.run([sys.executable, __file__], env=os.environ.copy())
        spec = parts[-1]
        if spec.startswith("planla:"):  # "planla:id@2026-11-02T19:00:00Z" → planlı yayın saati koy
            vid, when = spec[7:].split("@", 1)
            yt = publish._yt()
            st = yt.videos().list(part="status", id=vid.strip()).execute()["items"][0]["status"]
            body = {"id": vid.strip(), "status": {"privacyStatus": "private", "publishAt": when.strip(),
                                                  "selfDeclaredMadeForKids": st.get("selfDeclaredMadeForKids", False),
                                                  "containsSyntheticMedia": st.get("containsSyntheticMedia", True)}}
            try:
                yt.videos().update(part="status", body=body).execute()
                st2 = yt.videos().list(part="status", id=vid.strip()).execute()["items"][0]["status"]
                print(f"::notice title=planla {vid}::{st2.get('privacyStatus')} {st2.get('publishAt')}", flush=True)
                notify(f"🕒 Yayın saati ayarlandı: https://youtu.be/{vid.strip()} → "
                       f"{fmt_local(datetime.fromisoformat(when.strip().replace('Z', '+00:00')))}")
            except Exception as e:
                print(f"::error title=planla {vid}::{str(e)[:300]}", flush=True)
            sys.exit(0)
        if spec.startswith("gizle:"):  # videoları gizliye al (silmek yerine; geri açılabilir)
            check_channel()
            yt = publish._yt()
            hidden = []
            for vid in [x.strip() for x in spec[6:].split(",") if x.strip()]:
                it = yt.videos().list(part="status,snippet", id=vid).execute().get("items", [])
                if not it or (CHANNEL_ID and it[0]["snippet"].get("channelId") != CHANNEL_ID):
                    print(f"::warning title=gizle {vid}::bu kanalın videosu değil / bulunamadı", flush=True)
                    continue
                st = it[0]["status"]
                body = {"id": vid, "status": {"privacyStatus": "private",
                                              "selfDeclaredMadeForKids": st.get("selfDeclaredMadeForKids", False),
                                              "containsSyntheticMedia": st.get("containsSyntheticMedia", True)}}
                yt.videos().update(part="status", body=body).execute()
                hidden.append(it[0]["snippet"]["title"])
                print(f"::notice title=gizle {vid}::private", flush=True)
            notify(f"🙈 {len(hidden)} eski video gizliye alındı (silinmedi, istenirse geri açılabilir):\n- "
                   + "\n- ".join(hidden))
            sys.exit(0)
        if spec.startswith("yayinla:"):  # planlı videoyu hemen herkese aç
            yt = publish._yt()
            for vid in [x.strip() for x in spec[8:].split(",") if x.strip()]:
                st = yt.videos().list(part="status", id=vid).execute()["items"][0]["status"]
                body = {"id": vid, "status": {"privacyStatus": "public",
                                              "selfDeclaredMadeForKids": st.get("selfDeclaredMadeForKids", False),
                                              "containsSyntheticMedia": st.get("containsSyntheticMedia", True)}}
                if "license" in st:
                    body["status"]["license"] = st["license"]
                if "embeddable" in st:
                    body["status"]["embeddable"] = st["embeddable"]
                yt.videos().update(part="status", body=body).execute()
                now_ = yt.videos().list(part="status", id=vid).execute()["items"][0]["status"]["privacyStatus"]
                print(f"::notice title=yayınla {vid}::{now_}", flush=True)
                notify(f"🚀 Video şimdi yayında: https://youtu.be/{vid}" if now_ == "public" else
                       f"🆘 CLAUDE'A YAZ — Video herkese açılamadı ({now_}). Studio'dan Görünürlük → Herkese açık yapman gerekiyor.")
            sys.exit(0)  # yorum tamamlayıcıdan yazılır (kayıt orada tutuluyor)
        if "|" in spec:  # "videoId|Yeni başlık": yalnız başlığı değiştir
            vid, new_title = spec.split("|", 1)
            yt = publish._yt()
            sn = yt.videos().list(part="snippet", id=vid.strip()).execute()["items"][0]["snippet"]
            old_t = sn["title"]
            body = {"id": vid.strip(), "snippet": {k: sn[k] for k in ("title", "description", "tags", "categoryId",
                                                                     "defaultLanguage", "defaultAudioLanguage") if k in sn}}
            body["snippet"]["title"] = new_title.strip()[:100]
            yt.videos().update(part="snippet", body=body).execute()
            print(f"::notice title=başlık {vid}::{old_t} → {new_title}", flush=True)
            notify(f"✏️ Başlık değişti: https://youtu.be/{vid.strip()}\nEski: {old_t}\nYeni: {new_title.strip()}")
        else:
            refresh_videos([x.strip() for x in spec.split(",") if x.strip()])
        sys.exit(0)
    if os.getenv("RETAG"):  # yüklenmiş videoların hashtag satırını yenile: video kimlikleri, virgülle
        check_channel()
        retag_videos([x.strip() for x in os.getenv("RETAG").split(",") if x.strip()])
        sys.exit(0)
    if os.getenv("FINISH_ONLY"):  # sadece elle yüklenen videoları tamamla + bekleyen yorumlar (hafif, sık çalışır)
        check_channel()
        finish_manual_uploads()
        post_due_comments()
        ensure_playlist_covers()
        sys.exit(0)
    if os.getenv("BRANDING_ONLY"):  # sadece kanal açıklaması/afiş/anahtar kelimeler (video üretmez)
        check_channel()
        ensure_branding()
        clear_old_localizations()
        ensure_playlist_covers()
        log("Kanal ayarları tamam.")
        sys.exit(0)
    missing = [k for k in CFG.get("require_env", []) if not os.getenv(k)]
    if missing and not DRY_RUN:
        # ör. 2. kanalın YouTube izni henüz yoksa video YANLIŞ kanala yüklenmesin
        log(f"Kurulum eksik, çalışma atlandı: {', '.join(missing)}")
        notify(f"⏸ Kurulum tamamlanmadığı için bugün video üretilmedi (eksik: {', '.join(missing)}).")
        sys.exit(0)
    credit_report("start")
    try:
        res = run_once()
        credit_report("end")
        log("Bitti: video üretilmedi." if res is False else "Başarıyla bitti.")
        if res is not False and not DRY_RUN:
            (DATA / "missed.json").unlink(missing_ok=True)
    except Exception:
        err = redact(traceback.format_exc())
        log("HATA:\n" + err)
        print("::error title=HATA ayrıntısı::" + err[-3000:].replace("%", "%25").replace("\r", "").replace("\n", "%0A"))
        last = [l for l in err.strip().splitlines() if l.strip()][-1][:300]
        retry = (not UPLOAD_STARTED) and os.getenv("OTOYT_ATTEMPT", "1") == "1"
        guide = explain_error(err)
        if guide and any(k in guide for k in ("get_token", "anahtar")):
            retry = False  # izin/anahtar sorununu beklemek çözmez
        tts_fail = ("TTS" in err or "ses kotası" in err) and not DRY_RUN
        if tts_fail:
            retry = False  # günlük ses hakkı 10 dk'da yenilenmez → ertesi gün telafi çalışması
            save_json(DATA / "missed.json", {"date": datetime.now().isoformat(timespec="minutes")})
        if retry or tts_fail:  # sistem kendisi toparlıyor: sadece bilgi
            notify("ℹ️ Video üretiminde bir aksaklık oldu, sistem kendisi tekrar deneyecek. Senden bir şey gerekmiyor.\n"
                   + ("🔄 10 dakika sonra otomatik yeniden denenecek." if retry else
                      "📅 Yarın aynı saatte otomatik telafi üretimi yapılacak."))
        else:  # izin/anahtar gibi kendi kendine çözülemeyen durum
            notify("🆘 CLAUDE'A YAZ — Video üretimi durdu ve kendi kendine çözülemiyor."
                   + (f"\n💡 {guide}" if guide else "") + "\nClaude'a 'üretim hatası' yazman yeterli, kayıttan bakar.")
        credit_report("end")
        sys.exit(3 if retry else 4)  # 4 = hata Telegram'a Türkçe bildirildi
