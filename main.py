import os, re, sys, json, time, datetime, random, base64, html as htmllib
import xml.etree.ElementTree as ET
from urllib.parse import quote
import requests

GEMINI_KEY = os.environ["GEMINI_API_KEY"]
YT_KEY = os.environ.get("YOUTUBE_API_KEY", "")
BLOG_ID = os.environ["BLOG_ID"]
CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
REFRESH_TOKEN = os.environ["GOOGLE_REFRESH_TOKEN"]
MODE = os.environ.get("PUBLISH_MODE") or "draft"  # draft ya live
USE_IMAGE = (os.environ.get("USE_IMAGE") or "1") == "1"
ALLOW_NO_SEARCH_LIVE = (os.environ.get("ALLOW_NO_SEARCH_LIVE") or "0") == "1"
HISTORY = "posted.json"
MIN_WORDS = 900
MIN_SCORE = 6
TARGET_POSTS = int(os.environ.get("TARGET_POSTS") or 2)      # roz kitni post public karni hain
MAX_ATTEMPTS = int(os.environ.get("MAX_ATTEMPTS") or 8)      # is se zyada koshish nahi
MAX_DRAFTS = int(os.environ.get("MAX_DRAFTS") or 2)          # ek run me zyada se zyada itne reject draft
PAUSE = int(os.environ.get("PAUSE_SECONDS") or 20)

DEFAULT_MODELS = "gemini-3.1-flash-lite,gemini-3.5-flash-lite,gemini-3-flash-preview"
MODELS = [m.strip() for m in (os.environ.get("GEMINI_MODEL") or DEFAULT_MODELS).split(",") if m.strip()]
WORKING = []
NO_SEARCH_USED = False
SEARCH_OK = True

ALLOWED_LABELS = ["फिटनेस", "डाइट", "योग", "इम्यूनिटी", "मौसमी स्वास्थ्य", "वजन प्रबंधन",
                  "घरेलू उपाय", "सामान्य रोग", "महिला स्वास्थ्य", "बच्चों का स्वास्थ्य", "मानसिक स्वास्थ्य",
                  "हेल्दी ड्रिंक्स", "वर्कआउट", "नींद", "प्रोटीन", "बालों की देखभाल", "त्वचा की देखभाल"]


# ---------------- Gemini ----------------
def gemini(prompt, search=False, fatal=True, image=None):
    global NO_SEARCH_USED, SEARCH_OK
    headers = {"x-goog-api-key": GEMINI_KEY, "Content-Type": "application/json"}
    order = WORKING + [m for m in MODELS if m not in WORKING]
    modes = [True, False] if (search and SEARCH_OK) else [False]
    for use_search in modes:
        for model in order:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            parts_in = [{"text": prompt}]
            if image:
                parts_in.insert(0, {"inline_data": {"mime_type": image[0], "data": image[1]}})
            body = {"contents": [{"parts": parts_in}]}
            if use_search:
                body["tools"] = [{"google_search": {}}]
            for attempt in range(2):
                r = requests.post(url, headers=headers, json=body, timeout=600)
                if r.status_code == 200:
                    cand = r.json()["candidates"][0]
                    parts = cand.get("content", {}).get("parts", [])
                    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
                    chunks = cand.get("groundingMetadata", {}).get("groundingChunks", [])
                    if text.strip():
                        if model not in WORKING:
                            WORKING.insert(0, model)
                        if search and not use_search:
                            NO_SEARCH_USED = True
                            SEARCH_OK = False
                            print("WARNING: search grounding nahi chala", flush=True)
                        print(f"OK model={model} search={use_search}", flush=True)
                        return text.strip(), chunks
                print(f"FAIL model={model} search={use_search} status={r.status_code} {r.text[:300]}", flush=True)
                if r.status_code in (500, 503) and attempt == 0:
                    time.sleep(15)
                    continue
                break
            time.sleep(3)
    if fatal:
        sys.exit("Gemini: koi bhi model nahi chala. Upar ke FAIL lines dekhein.")
    return "", []


def field(head, key):
    m = re.search(rf"^{key}:\s*(.+)$", head, re.M | re.I)
    return m.group(1).strip() if m else ""


# ---------------- Topic discovery ----------------
def rss_titles(url, params=None):
    try:
        r = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        root = ET.fromstring(r.content)
        out = []
        for it in root.iter("item"):
            t = (it.findtext("title") or "").strip()
            if " - " in t:
                t = t.rsplit(" - ", 1)[0].strip()
            if t:
                out.append(t)
        return out
    except Exception as e:
        print("RSS fail", url[:50], repr(e)[:120], flush=True)
        return []


def news_lines():
    out = []
    for q in ["स्वास्थ्य लाइफस्टाइल when:2d", "health tips India when:2d", "घरेलू नुस्खे स्किन हेयर when:2d"]:
        for t in rss_titles("https://news.google.com/rss/search",
                            {"q": q, "hl": "hi", "gl": "IN", "ceid": "IN:hi"})[:12]:
            if t not in out:
                out.append(t)
    for t in rss_titles("https://trends.google.com/trending/rss?geo=IN")[:20]:
        if t not in out:
            out.append(t)
    return out


def iso_secs(d):
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", d or "")
    if not m:
        return 0
    h, mi, se = (int(x or 0) for x in m.groups())
    return h * 3600 + mi * 60 + se


YT_QUERIES = ["health tips hindi", "gharelu nuskhe", "skin care hindi", "weight loss hindi"]


def youtube_lines():
    if not YT_KEY:
        return []
    after = (datetime.datetime.now(datetime.timezone.utc) -
             datetime.timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ids = []
    for q in YT_QUERIES:
        try:
            r = requests.get("https://www.googleapis.com/youtube/v3/search", params={
                "part": "snippet", "q": q, "type": "video", "order": "viewCount",
                "videoDuration": "medium", "publishedAfter": after, "relevanceLanguage": "hi",
                "regionCode": "IN", "maxResults": 10, "key": YT_KEY}, timeout=30)
            if r.status_code != 200:
                print("YouTube search error", r.status_code, r.text[:200], flush=True)
                continue
            for it in r.json().get("items", []):
                vid = it["id"].get("videoId")
                if vid and vid not in ids:
                    ids.append(vid)
        except Exception as e:
            print("YouTube search exception", e, flush=True)
    if not ids:
        return []
    out = []
    try:
        r = requests.get("https://www.googleapis.com/youtube/v3/videos", params={
            "part": "snippet,statistics,contentDetails", "id": ",".join(ids[:50]), "key": YT_KEY}, timeout=30)
        vs = []
        for it in r.json().get("items", []):
            if 240 <= iso_secs(it.get("contentDetails", {}).get("duration", "")) <= 1200:
                vs.append((int(it.get("statistics", {}).get("viewCount", 0)), it["snippet"]["title"]))
        vs.sort(reverse=True)
        out = [f"{t} (YouTube, {v} views)" for v, t in vs[:12]]
    except Exception as e:
        print("YouTube videos exception", e, flush=True)
    return out


PICK_PROMPT = """Below are today's Hindi health/lifestyle news headlines, trending searches in India and popular YouTube video titles:
{list}

Pick ONE topic that many Indians are searching for and that can be written as a practical, helpful guide in simple Hindi. Good topics: body care, skin and hair care, home care, food and diet, exercise, sleep, common symptoms explained, everyday health tips, awareness about common conditions.
Skip topics already covered:
{avoid}

TYPE rules:
HOWTO = practical how-to / home-care / lifestyle topic (e.g. how to clean ears safely, ingrown hair care, dark inner thighs, a diet routine).
AWARENESS = explaining a health condition for awareness only (symptoms, causes, risk factors, prevention, when to see a doctor), e.g. kidney disease symptoms.
AVOID = asks for a cure or treatment of a serious disease, medicines/injections/doses, pregnancy or baby/child treatment, extreme fasting, dangerous procedures, deaths/sensitive celebrity or political news, anything unsafe to self-treat.
Prefer HOWTO or AWARENESS. Choose AVOID only if nothing else fits.

Reply EXACTLY in 3 lines:
TOPIC: <topic in English + Hindi, e.g. ear wax safe removal / कान की मैल कैसे निकालें>
TYPE: HOWTO or AWARENESS or AVOID
SOURCE: <the headline it came from, short>"""


def pick_topic(done, items):
    avoid = "\n".join(done[-60:]) or "(none)"
    if items:
        text, _ = gemini(PICK_PROMPT.replace("{list}", "\n".join(f"- {x}" for x in items[:70]))
                         .replace("{avoid}", avoid), fatal=False)
        topic, typ = field(text, "TOPIC"), field(text, "TYPE").upper()
        if topic:
            typ = "AVOID" if "AVOID" in typ else ("AWARENESS" if "AWARE" in typ else "HOWTO")
            return {"topic": topic, "type": typ, "source": field(text, "SOURCE")}
        print("Topic parse fail:", text[:200], flush=True)
    today = datetime.date.today().strftime("%d %B %Y")
    text, _ = gemini(
        f"Today is {today}. Suggest ONE popular everyday health, body-care or lifestyle topic that Indians "
        "search for (skin, hair, diet, exercise, sleep, seasonal care). Avoid:\n" + avoid +
        "\n\nReply with only the topic in English + Hindi.", search=True)
    return {"topic": text.splitlines()[0].strip(), "type": "HOWTO", "source": ""}


# ---------------- Article ----------------
PROMPT = """You are a careful health writer for an Indian Hindi blog named "FitLife India".
Topic: {topic}
Write a practical, helpful guide article like a good Indian lifestyle/health portal (clear, specific, useful). Use Google Search (when available) to verify facts from reliable sources (WHO, ICMR, NIH, AIIMS, Mayo Clinic). Write original content in your own words and your own structure. Never copy any headline or article.

Language: very simple everyday Hindi in Devanagari, the way people talk. Use common English words people already use (weight loss, diet, skin care, doctor, tips). Avoid heavy/shuddh Hindi words. Short sentences. 1500-2000 words, deep and useful, with Indian examples (dal, roti, sabzi, chai, nariyal tel, haldi, seasons, daily routine). No filler.

Strict rules:
- Medical safety: no cure claims, no guaranteed results, no "detox" or "boost immunity" promises, no medicine/supplement doses (for store-bought products say "follow the label or ask a pharmacist"). Use cautious wording ("मदद कर सकता है", "हर किसी पर एक जैसा असर नहीं होता"). Never discourage seeing a doctor.
- Do NOT invent statistics, percentages, study names, quotes or numbers. If unsure, leave it out. Never say a doctor reviewed the article.
- Structure:
{structure}
- Allowed HTML only: h2, h3, p, ul, li, ol, strong, table, thead, tbody, tr, th, td, blockquote. No <h1>, <html>, <body>, markdown, code fences.

Output EXACTLY in this format (no extra text before it):
TITLE: <Hindi title written like what people search, with the main keyword, max 65 characters. {title_rule} Never use "N दावों का सच" or "सच या झूठ" unless the topic itself is a myth. Must not copy any news headline.>
DESCRIPTION: <Hindi meta description, max 150 characters>
SLUG: <4-6 lowercase English words describing the topic, e.g. ear wax safe removal>
LABELS: <2 or 3 labels that best match this topic, ONLY from this list, comma-separated: """ + ", ".join(ALLOWED_LABELS) + """>
IMAGE_QUERY: <2-4 English words for a stock photo search, topic specific>
IMAGE1_PROMPT: <English prompt for a realistic documentary-style photo that DIRECTLY shows the main subject of the article title, so the topic is obvious at first glance. Examples: for ear wax care: a person gently cleaning the outer ear with a soft cloth in a bathroom; for tai chi walking: a person doing slow tai chi steps in a living room; for ingrown hair care: a person applying moisturiser on a shaved leg. People may appear from the side or back, as hands, or small in frame, but NO close-up faces. No text, no logos, no watermark, no gore, no medical diagrams>
IMAGE2_PROMPT: <English prompt for a DIFFERENT realistic photo for the middle of the article, showing one specific step, food, ingredient or habit from the article. Same rules as IMAGE1_PROMPT>
IMAGE_ALT: <short Hindi alt text for image 1>
===HTML===
<the article HTML>
"""

HOWTO_STRUCTURE = """  1. 2-line hook (relatable situation) and a short direct answer.
  2. <h2> why this happens / background (simple explanation).
  3. <h2> the main practical methods: 4-6 items, each as <h3>तरीका N: ...</h3> then 2-3 short paragraphs (how to do it step by step, why it may help, one caution). Only low-risk, common-sense methods.
  4. <h2>क्या न करें</h2> with a <ul>.
  5. <h2> prevention / right habits with Indian examples, including one HTML <table> with <thead>/<tbody> (क्या करें / क्या न करें).
  6. <h2>कब डॉक्टर के पास जाएं</h2> (clear warning signs).
  7. FAQ: one <h2>, then 5 questions as <h3>, each followed by a <p>.
  8. One short closing paragraph (share/comment), then ONE short <blockquote> medical disclaimer (only one disclaimer in the whole article)."""

AWARE_STRUCTURE = """  1. 2-line hook and a short direct answer: what this condition is and why awareness matters.
  2. <h2>X क्या है</h2> (simple explanation, what the body part does).
  3. <h2>लक्षण</h2> with a <ul>; mention that early stages may show no symptoms; symptoms alone cannot confirm anything.
  4. <h2>कारण और रिस्क फैक्टर</h2>
  5. <h2>जांच कैसे होती है</h2> (only general: the doctor decides which tests are needed).
  6. <h2>बचाव और लाइफस्टाइल</h2> with Indian diet/daily-routine examples, including one HTML <table> with <thead>/<tbody> (क्या करें / क्या न करें).
  7. <h2>इलाज के बारे में सही समझ</h2>: only general ideas (treatment depends on cause and stage and is decided by a doctor). No home-remedy or herbal cure claims, no medicine names or doses, no self-treatment advice.
  8. <h2>कब डॉक्टर से मिलें</h2>
  9. FAQ: one <h2>, then 5 questions as <h3>, each followed by a <p>.
  10. One short closing paragraph (share/comment), then ONE short <blockquote> medical disclaimer (only one disclaimer in the whole article)."""

CHECK_PROMPT = """You are a strict medical-content safety reviewer. Read this Hindi health article HTML.
FAIL if it has any of: claims of curing/treating/reversing a disease, medicine or supplement doses, invented statistics/study names/quotes, guaranteed or absolute results, telling readers to stop medicines or skip a doctor, "detox" or "boost immunity" promises, home remedies or herbs presented as treatment for a serious disease, or unsafe self-treatment advice.
Otherwise PASS.
Also give a quality SCORE from 1 to 10: how useful, specific and original the article is for Indian readers (concrete practical steps, no filler or repetition, simple natural Hindi). 10 = excellent, 5 = generic and thin.
Reply EXACTLY in 3 lines:
VERDICT: PASS or FAIL
SCORE: <number 1-10>
REASON: <one short English sentence>

ARTICLE:
"""

EXPAND_PROMPT = """Below is a Hindi health guide article in HTML. Rewrite it so it is deeper and longer, at least 1300 words, by adding concrete detail inside each existing section: more everyday Indian examples, practical do/don't points and clearer explanation. Keep the same structure, headings, the table, the FAQ and exactly ONE disclaimer at the end. Do NOT add statistics, study names, quotes, medicine/supplement doses or cure claims. Keep simple everyday Hindi. Output only the HTML, no markdown, no code fences.

ARTICLE:
"""


def word_count(h):
    return len(re.sub(r"<[^>]+>", " ", h).split())


def parse(text):
    head, _, body = text.partition("===HTML===")
    body = re.sub(r"^```(?:html)?\s*|\s*```$", "", body.strip())
    labels = [x.strip() for x in field(head, "LABELS").split(",") if x.strip()]
    labels = [x for x in labels if x in ALLOWED_LABELS][:3] or ["फिटनेस"]
    slug = re.sub(r"\s+", " ", re.sub(r"[^a-zA-Z0-9 ]", " ", field(head, "SLUG"))).strip().lower()[:60]
    return {"title": field(head, "TITLE"), "desc": field(head, "DESCRIPTION"), "labels": labels,
            "slug": slug, "image1": field(head, "IMAGE1_PROMPT") or field(head, "IMAGE_PROMPT"),
            "image2": field(head, "IMAGE2_PROMPT"), "alt": field(head, "IMAGE_ALT"),
            "image_query": field(head, "IMAGE_QUERY"), "html": body}


def sources_html(chunks):
    seen, items = set(), []
    for c in chunks:
        w = c.get("web") or {}
        t, u = w.get("title"), w.get("uri")
        if t and u and t not in seen:
            seen.add(t)
            items.append(f'<li><a href="{u}" rel="nofollow noopener" target="_blank">{htmllib.escape(t)}</a></li>')
        if len(items) >= 6:
            break
    return "<h2>संदर्भ (Sources)</h2><ul>" + "".join(items) + "</ul>" if items else ""


def style_html(h):
    h = re.sub(r"<table(\s[^>]*)?>", '<table style="width:100%;border-collapse:collapse;margin:0;font-size:15px">', h)
    h = re.sub(r"<th(\s[^>]*)?>", '<th style="border:1px solid #1d4e38;background:#1d4e38;color:#fff;padding:8px;text-align:left">', h)
    h = re.sub(r"<td(\s[^>]*)?>", '<td style="border:1px solid #cfd8d2;padding:8px;vertical-align:top">', h)
    h = re.sub(r"<blockquote(\s[^>]*)?>", '<blockquote style="background:#f1f8f3;border-left:4px solid #1d4e38;margin:16px 0;padding:12px 14px;font-style:normal">', h)
    h = re.sub(r"(<table.*?</table>)", r'<div style="overflow-x:auto;margin:16px 0">\1</div>', h, flags=re.S)
    return h


def pexels_image(query):
    key = os.environ.get("PEXELS_API_KEY")
    if not key or not query:
        return None
    try:
        r = requests.get("https://api.pexels.com/v1/search",
                         params={"query": query, "per_page": 6, "orientation": "landscape"},
                         headers={"Authorization": key}, timeout=30)
        photos = r.json().get("photos") or [] if r.status_code == 200 else []
        if not photos:
            return None
        p = random.choice(photos[:4])
        return p["src"].get("large2x") or p["src"]["large"], p.get("photographer", ""), p.get("url", "https://www.pexels.com")
    except Exception:
        return None


IMG_STYLE = ", realistic documentary photo, natural light, sharp focus, no text, no watermark, no logo, no close-up face"

JUDGE_PROMPT = """Judge this photo for a Hindi health article.
Article title: {title}
The photo should show: {want}
Ignore a tiny watermark or logo at the very bottom edge.
Reply EXACTLY in 3 lines:
MATCH: <1-10, how clearly and directly the photo shows the intended subject>
PROBLEMS: <none, or a short list: readable text, logo, deformed hands/face/body, gore or medical close-up, nudity, unrelated scene>
OK: YES only if MATCH is 7 or more and there are no problems, else NO"""


def poll_url(prompt, seed):
    return ("https://image.pollinations.ai/prompt/" + quote(prompt + IMG_STYLE) +
            f"?width=1200&height=700&nologo=true&seed={seed}")


def fetch_image(url):
    for _ in range(2):
        try:
            r = requests.get(url, timeout=120)
            ct = r.headers.get("content-type", "")
            if r.status_code == 200 and ct.startswith("image") and len(r.content) > 8000:
                return r.content, ct.split(";")[0]
            print("Image fetch bad:", r.status_code, ct, len(r.content), flush=True)
        except Exception as e:
            print("Image fetch exception:", repr(e)[:120], flush=True)
        time.sleep(5)
    return None, None


def pick_image(prompt, title, tries=3):
    """Pollinations se image banao, Gemini se dekhkar jaanchte hain ki topic se match karti hai ya nahi."""
    best_score, best_url = 0, None
    for i in range(tries):
        url = poll_url(prompt, random.randint(1, 999999))
        data, mime = fetch_image(url)
        if not data:
            continue
        txt, _ = gemini(JUDGE_PROMPT.replace("{title}", title).replace("{want}", prompt),
                        image=(mime, base64.b64encode(data).decode()), fatal=False)
        if not txt:
            print("Image judge nahi chala, bina jaanch ke image li.", flush=True)
            return url
        try:
            score = int(re.search(r"\d+", field(txt, "MATCH")).group())
        except Exception:
            score = 0
        problems = field(txt, "PROBLEMS")
        ok = "YES" in field(txt, "OK").upper() and score >= 7
        print(f"Image try {i+1}: match={score} ok={ok} | {problems[:80]}", flush=True)
        if ok:
            return url
        if problems.lower().startswith("none") and score > best_score:
            best_score, best_url = score, url
    if best_url and best_score >= 6:
        print(f"Image: perfect nahi mili, best (match={best_score}) li.", flush=True)
        return best_url
    print("Image: koi sahi image nahi mili, bina image ke post.", flush=True)
    return None


def img_block(src, alt, crop=True, cap=""):
    alt = htmllib.escape(alt, quote=True)
    if crop:  # neeche ki patti kaat dete hain (watermark wahi hota hai)
        img = (f'<div style="overflow:hidden;border-radius:8px"><img src="{src}" alt="{alt}" '
               'style="width:100%;display:block;margin-bottom:-9%"/></div>')
    else:
        img = f'<img src="{src}" alt="{alt}" style="max-width:100%;height:auto;border-radius:8px"/>'
    return f'<div style="text-align:center;margin:12px 0">{img}{cap}</div>'


def make_image_html(prompt, alt, title, query=None):
    if not USE_IMAGE:
        return ""
    px = pexels_image(query) if query else None
    if px:
        src, name, link = px
        cap = (f'<br/><small>Photo: <a href="{link}" rel="nofollow noopener" target="_blank">'
               f'{htmllib.escape(name)}</a> / Pexels</small>')
        return img_block(src, alt, crop=False, cap=cap)
    if not prompt:
        return ""
    url = pick_image(prompt, title)
    return img_block(url, alt) if url else ""


AUTHOR_BOX = '<blockquote><b>लेखक:</b> FitLife India टीम</blockquote>'


# ---------------- Blogger ----------------
def access_token():
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN, "grant_type": "refresh_token"}, timeout=60)
    if r.status_code != 200:
        sys.exit(f"Token error {r.status_code}: {r.text[:300]}")
    return r.json()["access_token"]


def publish(title, html, labels, mode, slug=""):
    base = f"https://www.googleapis.com/blogger/v3/blogs/{BLOG_ID}/posts/"
    hdr = {"Authorization": "Bearer " + access_token()}
    live = mode == "live"
    # Blogger link title se banta hai. Live post pehle angrezi title se banate hain,
    # phir Hindi title laga dete hain (link nahi badalta).
    first = slug if (live and slug) else title
    r = requests.post(base, params={"isDraft": "false" if live else "true"}, headers=hdr,
                      json={"kind": "blogger#post", "title": first, "content": html, "labels": labels},
                      timeout=120)
    if r.status_code != 200:
        sys.exit(f"Blogger error {r.status_code}: {r.text[:500]}")
    data = r.json()
    if first != title:
        ok = False
        for _ in range(3):
            p = requests.patch(base + data["id"], headers=hdr, json={"title": title}, timeout=60)
            if p.status_code == 200:
                ok = True
                break
            print("Title patch fail", p.status_code, p.text[:200], flush=True)
            time.sleep(5)
        print("Title patch:", "ok" if ok else "FAIL (post par angrezi title reh gaya, haath se badlein)", flush=True)
        if ok:
            print("Link before/after:", data.get("url"), "->", p.json().get("url"), flush=True)
            return p.json().get("url") or data.get("url")
    return data.get("url") or data.get("id")


def save_history(done):
    json.dump(done, open(HISTORY, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def make_post(done, items, save_draft=True):
    pick = pick_topic(done, items)
    topic, typ = pick["topic"], pick["type"]
    print("Topic:", topic, "| type:", typ, "| source:", pick["source"], flush=True)

    if typ == "AVOID":
        print("Topic risky/sensitive, article nahi likha.", flush=True)
        done.append(topic)
        save_history(done)
        return "rejected"

    if typ == "AWARENESS":
        structure = AWARE_STRUCTURE
        title_rule = "Style: '<condition>: लक्षण, कारण और बचाव' or a similar search-friendly title."
    else:
        structure = HOWTO_STRUCTURE
        title_rule = "Style: a how-to question like 'कान की मैल कैसे निकालें? सही और सुरक्षित तरीका'."
    prompt = (PROMPT.replace("{topic}", topic).replace("{structure}", structure)
              .replace("{title_rule}", title_rule))
    text, chunks = gemini(prompt, search=True)
    art = parse(text)
    if not art["title"] or len(art["html"]) < 2000:
        sys.exit("Article adhura/chhota aaya, post nahi ki gayi.\n" + text[:500])

    if word_count(art["html"]) < 1200:
        et, _ = gemini(EXPAND_PROMPT + art["html"][:40000], fatal=False)
        et = re.sub(r"^```(?:html)?\s*|\s*```$", "", et.strip())
        before, after = word_count(art["html"]), word_count(et)
        print(f"Expand: {before} -> {after} shabd", flush=True)
        if after > before * 1.15 and "<h2" in et and "<table" in et:
            art["html"] = et

    check_text, _ = gemini(CHECK_PROMPT + art["html"][:40000])
    verdict = field(check_text, "VERDICT").upper()
    reason = field(check_text, "REASON")
    try:
        score = int(re.search(r"\d+", field(check_text, "SCORE")).group())
    except Exception:
        score = 0
    print("Safety check:", verdict, "| score:", score, "|", reason, flush=True)

    words = word_count(art["html"])
    reasons = []
    if "PASS" not in verdict:
        reasons.append("safety check fail: " + reason)
    if NO_SEARCH_USED and not ALLOW_NO_SEARCH_LIVE:
        reasons.append("Google Search nahi chala")
    if score < MIN_SCORE:
        reasons.append(f"quality score {score}/10")
    if words < MIN_WORDS:
        reasons.append(f"article chhota ({words} shabd)")

    if reasons and not save_draft:
        print("Reject (draft bhi nahi bana):", reasons, flush=True)
        done.append(topic)
        save_history(done)
        return "rejected"

    mode = MODE
    labels = list(art["labels"])
    alt = art["alt"] or art["title"]
    html = make_image_html(art["image1"], alt, art["title"], art["image_query"])
    mid = make_image_html(art["image2"], art["title"], art["title"])
    if art["desc"]:
        html += f"<p><b>{htmllib.escape(art['desc'])}</b></p>"
    if reasons:
        mode = "draft"
        labels.append("तथ्य-जांच बाकी")
        html = ('<p style="background:#fdecea;border:1px solid #d93025;padding:10px;color:#a50e0e">'
                '[DRAFT NOTE: ' + htmllib.escape("; ".join(reasons)) + '. Publish se pehle facts check '
                'karein, aur ye note tatha label "तथ्य-जांच बाकी" hata dein.]</p>') + html
    body = style_html(art["html"])
    if mid:
        pos = [m.start() for m in re.finditer(r"<h2", body)]
        at = pos[2] if len(pos) >= 3 else (pos[-1] if pos else len(body))
        body = body[:at] + mid + body[at:]
    html += body + sources_html(chunks) + AUTHOR_BOX

    print("Words:", words, "| mode:", mode, "| reasons:", reasons, flush=True)
    link = publish(art["title"], html, labels, mode, art["slug"])
    print("Done:", mode, link, flush=True)

    done.append(topic)
    save_history(done)
    return mode


def main():
    done = json.load(open(HISTORY, encoding="utf-8")) if os.path.exists(HISTORY) else []
    done = [x for x in done if not x.startswith("yt:")]
    items = news_lines() + youtube_lines()
    print(f"Topic candidates: {len(items)} | target: {TARGET_POSTS} | mode: {MODE}", flush=True)
    good = drafts = 0
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if good >= TARGET_POSTS:
            break
        print(f"\n===== Attempt {attempt}/{MAX_ATTEMPTS} | done {good}/{TARGET_POSTS} =====", flush=True)
        try:
            mode = make_post(done, items, save_draft=(drafts < MAX_DRAFTS))
        except (Exception, SystemExit) as e:
            print("Attempt fail:", repr(e)[:300], flush=True)
            mode = "error"
        if mode == MODE:
            good += 1
        elif mode == "draft":
            drafts += 1
        print(f"Attempt result: {mode}", flush=True)
        if good < TARGET_POSTS and attempt < MAX_ATTEMPTS:
            time.sleep(PAUSE)
    print(f"\nSUMMARY: {good}/{TARGET_POSTS} {MODE} posts, {drafts} drafts", flush=True)
    if good == 0:
        sys.exit("Aaj ek bhi post nahi bani. Upar ke Attempt logs dekhein.")


if __name__ == "__main__":
    main()
