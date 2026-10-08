import os, re, sys, json, time, datetime, random, html as htmllib
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
MIN_WORDS = 1000

DEFAULT_MODELS = ("gemini-3.1-flash-lite,gemini-3.5-flash-lite,gemini-3-flash-preview,"
                  "gemini-2.5-flash-lite,gemini-2.5-flash")
MODELS = [m.strip() for m in (os.environ.get("GEMINI_MODEL") or DEFAULT_MODELS).split(",") if m.strip()]
WORKING = []
NO_SEARCH_USED = False
SEARCH_OK = True

ALLOWED_LABELS = ["फिटनेस", "डाइट", "योग", "इम्यूनिटी", "मौसमी स्वास्थ्य", "वजन प्रबंधन",
                  "घरेलू उपाय", "सामान्य रोग", "महिला स्वास्थ्य", "बच्चों का स्वास्थ्य", "मानसिक स्वास्थ्य",
                  "हेल्दी ड्रिंक्स", "वर्कआउट", "नींद", "प्रोटीन", "बालों की देखभाल", "त्वचा की देखभाल"]


# ---------------- Gemini ----------------
def gemini(prompt, search=False, video_url=None, fatal=True):
    global NO_SEARCH_USED, SEARCH_OK
    headers = {"x-goog-api-key": GEMINI_KEY, "Content-Type": "application/json"}
    order = WORKING + [m for m in MODELS if m not in WORKING]
    modes = [True, False] if (search and SEARCH_OK) else [False]
    for use_search in modes:
        for model in order:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            parts_in = [{"text": prompt}]
            body = {"contents": [{"parts": parts_in}]}
            if video_url:
                parts_in.insert(0, {"file_data": {"file_uri": video_url}})
                body["generationConfig"] = {"mediaResolution": "MEDIA_RESOLUTION_LOW"}
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
                        print(f"OK model={model} search={use_search} video={bool(video_url)}", flush=True)
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


# ---------------- YouTube ----------------
YT_QUERIES = ["health tips hindi", "fitness hindi", "gharelu nuskhe", "weight loss hindi", "yoga hindi"]


def iso_secs(d):
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", d or "")
    if not m:
        return 0
    h, mi, se = (int(x or 0) for x in m.groups())
    return h * 3600 + mi * 60 + se


def youtube_videos(done):
    if not YT_KEY:
        print("YOUTUBE_API_KEY nahi hai, YouTube step skip.", flush=True)
        return []
    after = (datetime.datetime.now(datetime.timezone.utc) -
             datetime.timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ids = []
    for q in YT_QUERIES:
        try:
            r = requests.get("https://www.googleapis.com/youtube/v3/search", params={
                "part": "snippet", "q": q, "type": "video", "order": "viewCount",
                "videoDuration": "medium",  # 4 se 20 minute
                "publishedAfter": after, "relevanceLanguage": "hi", "regionCode": "IN",
                "maxResults": 10, "key": YT_KEY}, timeout=30)
            if r.status_code != 200:
                print("YouTube search error", r.status_code, r.text[:300], flush=True)
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
            "part": "snippet,statistics,contentDetails", "id": ",".join(ids[:50]), "key": YT_KEY},
            timeout=30)
        for it in r.json().get("items", []):
            if "yt:" + it["id"] in done:
                continue
            secs = iso_secs(it.get("contentDetails", {}).get("duration", ""))
            if not (240 <= secs <= 1200):  # sirf 4 se 20 minute
                continue
            out.append({"id": it["id"], "title": it["snippet"]["title"], "secs": secs,
                        "channel": it["snippet"].get("channelTitle", ""),
                        "views": int(it.get("statistics", {}).get("viewCount", 0))})
    except Exception as e:
        print("YouTube videos exception", e, flush=True)
    out.sort(key=lambda v: v["views"], reverse=True)
    return out[:15]


def pick_topic(done):
    avoid = "\n".join(x for x in done[-60:] if not x.startswith("yt:")) or "(none)"
    vids = youtube_videos(done)
    if vids:
        lst = "\n".join(f"{i+1}. {v['title']} ({v['views']} views, {v['secs']//60} min)"
                        for i, v in enumerate(vids))
        text, _ = gemini(
            "These are the most viewed Hindi health/fitness YouTube videos this week:\n" + lst +
            "\n\nPick ONE video whose topic is a popular health/fitness claim, myth or tip that can be "
            "written about as a fact-check article. Skip topics already covered:\n" + avoid +
            "\n\nRISK rules: RISKY if it is about treating/curing a disease (diabetes, BP, thyroid, cancer, "
            "kidney, liver, uric acid, cholesterol, heart, etc.), medicines, supplement doses, pregnancy, "
            "children, or extreme fasting. SAFE if it is general diet, exercise, sleep, water, weight, "
            "common food myths.\n\nReply EXACTLY in 3 lines:\nINDEX: <number>\n"
            "TOPIC: <short English+Hindi topic, e.g. jeera paani for weight loss>\nRISK: SAFE or RISKY")
        try:
            idx = int(re.search(r"\d+", field(text, "INDEX")).group()) - 1
            v = vids[idx]
            topic = field(text, "TOPIC")
            if topic:
                return {"topic": topic, "risk": field(text, "RISK").upper(), "video": v}
        except Exception as e:
            print("Topic parse fail", e, text[:200], flush=True)
    today = datetime.date.today().strftime("%d %B %Y")
    text, _ = gemini(
        f"Today is {today}. Suggest ONE popular, safe lifestyle health or fitness topic (diet chart, "
        "home workout, sleep, water, seasonal food) for Hindi readers in India. Avoid:\n" + avoid +
        "\n\nReply with only the topic name.", search=True)
    return {"topic": text.splitlines()[0].strip(), "risk": "SAFE", "video": None}


WATCH_PROMPT = """Watch this video carefully. It is a Hindi health/fitness video.
Reply EXACTLY in this format (paraphrase everything in your own English words, never quote sentences):
SUMMARY: <1-2 sentences: what the video is about>
CLAIM1: <one specific health claim or tip the video makes>
CLAIM2: <next claim>
(continue up to CLAIM6; only real claims the video makes, at least 3 if it has them)
RISK: SAFE or RISKY
RISKY means the video promotes treating/curing a disease, medicines, supplement doses, pregnancy, children, or extreme fasting. SAFE means general diet, exercise, sleep, water, weight or common food myths."""


# ---------------- Article ----------------
PROMPT = """You are a careful health writer for an Indian blog named "FitLife India".
Topic: {topic}
{claim}
Write a "सच या झूठ" (fact-check) article. Use Google Search (when available) to verify facts from reliable sources (WHO, ICMR, NIH, AIIMS, Mayo Clinic, peer-reviewed studies). Write original content in your own words and your own structure.

Language: very simple everyday Hindi in Devanagari, the way people talk. Use common English words people already use (weight loss, diet, workout, protein, tips, doctor). Avoid heavy/shuddh Hindi words. Short sentences. 1500-2000 words, deep and useful, with Indian examples (dal, roti, sabzi, chai, local seasons, daily routines). Do not pad with generic filler.

Strict rules:
- Medical safety: no cure claims, no guaranteed results, no "detox" or "boost immunity" promises, no medicine/supplement doses. Use cautious wording ("मदद कर सकता है", "पक्का सबूत नहीं है"). Never discourage seeing a doctor.
- Do NOT invent statistics, percentages, study names, quotes or numbers. If unsure, leave it out. Never say a doctor reviewed the article.
- Structure:
  1. 2-line hook (relatable question) + direct answer.
  2. <h2>सच या झूठ? सीधा जवाब</h2> with a <blockquote> giving the overall verdict in 2-3 lines.
  3. {claims_section}
  4. <h2>कैसे अपनाएं (सुरक्षित तरीका)</h2> with practical Indian examples, include one HTML <table> with <thead>/<tbody> (क्या खाएं / क्या न खाएं, or a simple routine).
  5. <h2>किसे सावधानी रखनी चाहिए</h2>
  6. <h2>कब डॉक्टर से मिलें</h2>
  7. FAQ: one <h2>, then 4 questions as <h3>, each followed by a <p>.
  8. One short closing paragraph (share/comment), then ONE short <blockquote> medical disclaimer (only one disclaimer in the whole article).
- Allowed HTML only: h2, h3, p, ul, li, ol, strong, table, thead, tbody, tr, th, td, blockquote. No <h1>, <html>, <body>, markdown, code fences.

Output EXACTLY in this format (no extra text before it):
TITLE: <own Hindi title with the main keyword, max 65 characters, must NOT copy any video title, no clickbait promises. {title_rule}>
DESCRIPTION: <Hindi meta description, max 150 characters>
SLUG: <4-6 lowercase English words describing the topic, e.g. vegetable juice weight loss facts>
LABELS: <2 or 3 labels that best match this topic, ONLY from this list, comma-separated. Use "घरेलू उपाय" only if the topic is a home remedy: """ + ", ".join(ALLOWED_LABELS) + """>
IMAGE_QUERY: <2-4 English words for a stock photo search, topic specific>
IMAGE_PROMPT: <English prompt for a bright realistic photo of food, a drink, objects or an empty scene related to the topic; no people, no faces, no text, no watermark, no logo>
===HTML===
<the article HTML>
"""

CLAIMS_SEEN = """For EACH claim below, write its own section: <h2>दावा N: <the claim in simple Hindi></h2>, then <p><strong>फैसला: सच / आधा सच / पक्का सबूत नहीं / झूठ</strong></p>, then 2-3 short paragraphs: what is reasonable, what is exaggerated or unproven, and an Indian everyday example. Keep the claim numbering exactly as given."""

CLAIMS_UNSEEN = """<h2>वायरल दावा क्या है</h2> (what people commonly claim about this topic), then <h2>असल में क्या सही है</h2> with 3-5 short bullet/paragraph points (what is reasonable, what is unproven or exaggerated)."""

CHECK_PROMPT = """You are a strict medical-content safety reviewer. Read this Hindi health article HTML.
FAIL if it has any of: claims of curing/treating/reversing a disease, medicine or supplement doses, invented statistics/study names/quotes, guaranteed or absolute results, telling readers to stop medicines or skip a doctor, "detox" or "boost immunity" promises, or presenting an unproven remedy as working.
Otherwise PASS.
Reply EXACTLY in 2 lines:
VERDICT: PASS or FAIL
REASON: <one short English sentence>

ARTICLE:
"""


def parse(text):
    head, _, body = text.partition("===HTML===")
    body = re.sub(r"^```(?:html)?\s*|\s*```$", "", body.strip())
    labels = [x.strip() for x in field(head, "LABELS").split(",") if x.strip()]
    labels = [x for x in labels if x in ALLOWED_LABELS][:3] or ["फिटनेस"]
    slug = re.sub(r"\s+", " ", re.sub(r"[^a-zA-Z0-9 ]", " ", field(head, "SLUG"))).strip().lower()[:60]
    return {"title": field(head, "TITLE"), "desc": field(head, "DESCRIPTION"), "labels": labels,
            "slug": slug, "image": field(head, "IMAGE_PROMPT"),
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


def image_html(art):
    alt = htmllib.escape(art["title"], quote=True)
    px = pexels_image(art["image_query"])
    crop = False
    if px:
        src, name, link = px
        cap = f'<br/><small>Photo: <a href="{link}" rel="nofollow noopener" target="_blank">{htmllib.escape(name)}</a> / Pexels</small>'
    elif USE_IMAGE and art["image"]:
        prompt = art["image"] + ", no text, no watermark, no logo"
        src = "https://image.pollinations.ai/prompt/" + quote(prompt) + "?width=1200&height=700&nologo=true"
        cap = ""
        crop = True  # neeche ki patti kaat dete hain (watermark wahi hota hai)
    else:
        return ""
    if crop:
        img = (f'<div style="overflow:hidden;border-radius:8px"><img src="{src}" alt="{alt}" '
               'style="width:100%;display:block;margin-bottom:-9%"/></div>')
    else:
        img = f'<img src="{src}" alt="{alt}" style="max-width:100%;height:auto;border-radius:8px"/>'
    return f'<div style="text-align:center;margin-bottom:12px">{img}{cap}</div>'


def video_embed(v):
    t = htmllib.escape(v["title"])
    ch = htmllib.escape(v["channel"])
    return ('<h2>वायरल वीडियो</h2>'
            '<p><small>यह वीडियो हमारा नहीं है। इसमें कही गई बातें उसके क्रिएटर की हैं। '
            'इन दावों की जांच ऊपर लिखी है।</small></p>'
            '<div style="position:relative;padding-bottom:56.25%;height:0;overflow:hidden;margin:12px 0">'
            f'<iframe src="https://www.youtube-nocookie.com/embed/{v["id"]}" title="{t}" '
            'style="position:absolute;top:0;left:0;width:100%;height:100%;border:0" allowfullscreen></iframe></div>'
            f'<p><small>वीडियो: <a href="https://www.youtube.com/watch?v={v["id"]}" rel="nofollow noopener" '
            f'target="_blank">{t}</a> &middot; चैनल: {ch}</small></p>')


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


def main():
    done = json.load(open(HISTORY, encoding="utf-8")) if os.path.exists(HISTORY) else []
    pick = pick_topic(done)
    topic, risk, video = pick["topic"], pick["risk"], pick["video"]
    print("Topic:", topic, "| risk:", risk, "| video:", video["id"] if video else None, flush=True)

    # video seedha dekhna
    seen, claims, summary = False, [], ""
    if video:
        wt, _ = gemini(WATCH_PROMPT, video_url="https://www.youtube.com/watch?v=" + video["id"], fatal=False)
        claims = re.findall(r"^CLAIM\d+:\s*(.+)$", wt, re.M)[:6]
        summary = field(wt, "SUMMARY")
        if len(claims) >= 2:
            seen = True
            if "RISKY" in field(wt, "RISK").upper():
                risk = "RISKY"
        else:
            print("Video dekh nahi paye, bina video ke article banega.", flush=True)
    print("Video seen:", seen, "| claims:", len(claims), flush=True)

    if seen:
        claim = (f'This article fact-checks the claims of a popular video titled "{video["title"]}". '
                 f"Video summary: {summary}\nClaims it makes (paraphrased):\n" +
                 "\n".join(f"{i+1}. {c}" for i, c in enumerate(claims)) + "\n")
        claims_section = CLAIMS_SEEN
        title_rule = (f"The title MUST contain the number {len(claims)} (e.g. '{len(claims)} दावे')."
                      if 3 <= len(claims) <= 7 else "Do not put a number in the title.")
    else:
        claim = ""
        claims_section = CLAIMS_UNSEEN
        title_rule = "Put a number in the title only if the article truly has that many numbered points."
    prompt = (PROMPT.replace("{topic}", topic).replace("{claim}", claim)
              .replace("{claims_section}", claims_section).replace("{title_rule}", title_rule))
    text, chunks = gemini(prompt, search=True)
    art = parse(text)
    if not art["title"] or len(art["html"]) < 2000:
        sys.exit("Article adhura/chhota aaya, post nahi ki gayi.\n" + text[:500])

    check_text, _ = gemini(CHECK_PROMPT + art["html"][:40000])
    verdict = field(check_text, "VERDICT").upper()
    reason = field(check_text, "REASON")
    print("Safety check:", verdict, reason, flush=True)

    words = len(re.sub(r"<[^>]+>", " ", art["html"]).split())
    reasons = []
    if risk != "SAFE":
        reasons.append("risky topic")
    if "PASS" not in verdict:
        reasons.append("safety check fail: " + reason)
    if NO_SEARCH_USED and not ALLOW_NO_SEARCH_LIVE:
        reasons.append("Google Search nahi chala")
    if words < MIN_WORDS:
        reasons.append(f"article chhota ({words} shabd)")

    mode = MODE
    labels = list(art["labels"])
    html = image_html(art)
    if art["desc"]:
        html += f"<p><b>{htmllib.escape(art['desc'])}</b></p>"
    if reasons:
        mode = "draft"
        labels.append("तथ्य-जांच बाकी")
        html = ('<p style="background:#fdecea;border:1px solid #d93025;padding:10px;color:#a50e0e">'
                '[DRAFT NOTE: ' + htmllib.escape("; ".join(reasons)) + '. Publish se pehle facts check '
                'karein, aur ye note tatha label "तथ्य-जांच बाकी" hata dein.]</p>') + html
    html += style_html(art["html"])
    if seen:
        html += video_embed(video)
    html += sources_html(chunks) + AUTHOR_BOX

    print("Words:", words, "| mode:", mode, "| reasons:", reasons, flush=True)
    link = publish(art["title"], html, labels, mode, art["slug"])
    print("Done:", mode, link, flush=True)

    done.append(topic)
    if video:
        done.append("yt:" + video["id"])
    json.dump(done, open(HISTORY, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
