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

DEFAULT_MODELS = ("gemini-3.1-flash-lite,gemini-3.5-flash-lite,gemini-3-flash-preview,"
                  "gemini-2.5-flash-lite,gemini-2.5-flash")
MODELS = [m.strip() for m in (os.environ.get("GEMINI_MODEL") or DEFAULT_MODELS).split(",") if m.strip()]
WORKING = []
NO_SEARCH_USED = False

ALLOWED_LABELS = ["फिटनेस", "डाइट", "योग", "इम्यूनिटी", "मौसमी स्वास्थ्य", "वजन प्रबंधन",
                  "घरेलू उपाय", "सामान्य रोग", "महिला स्वास्थ्य", "बच्चों का स्वास्थ्य", "मानसिक स्वास्थ्य"]


# ---------------- Gemini ----------------
def gemini(prompt, search=False):
    global NO_SEARCH_USED
    headers = {"x-goog-api-key": GEMINI_KEY, "Content-Type": "application/json"}
    order = WORKING + [m for m in MODELS if m not in WORKING]
    for use_search in ([True, False] if search else [False]):
        for model in order:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            body = {"contents": [{"parts": [{"text": prompt}]}]}
            if use_search:
                body["tools"] = [{"google_search": {}}]
            for attempt in range(2):
                r = requests.post(url, headers=headers, json=body, timeout=300)
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
                            print("WARNING: search grounding nahi chala", flush=True)
                        print(f"OK model={model} search={use_search}", flush=True)
                        return text.strip(), chunks
                print(f"FAIL model={model} search={use_search} status={r.status_code} {r.text[:300]}", flush=True)
                if r.status_code in (500, 503) and attempt == 0:
                    time.sleep(15)
                    continue
                break
            time.sleep(3)
    sys.exit("Gemini: koi bhi model nahi chala. Upar ke FAIL lines dekhein.")


# ---------------- YouTube ----------------
YT_QUERIES = ["health tips hindi", "fitness hindi", "gharelu nuskhe", "weight loss hindi", "yoga hindi"]


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
            "part": "snippet,statistics", "id": ",".join(ids[:50]), "key": YT_KEY}, timeout=30)
        for it in r.json().get("items", []):
            if "yt:" + it["id"] in done:
                continue
            out.append({"id": it["id"], "title": it["snippet"]["title"],
                        "channel": it["snippet"].get("channelTitle", ""),
                        "views": int(it.get("statistics", {}).get("viewCount", 0))})
    except Exception as e:
        print("YouTube videos exception", e, flush=True)
    out.sort(key=lambda v: v["views"], reverse=True)
    return out[:15]


def field(head, key):
    m = re.search(rf"^{key}:\s*(.+)$", head, re.M | re.I)
    return m.group(1).strip() if m else ""


def pick_topic(done):
    avoid = "\n".join(x for x in done[-60:] if not x.startswith("yt:")) or "(none)"
    vids = youtube_videos(done)
    if vids:
        lst = "\n".join(f"{i+1}. {v['title']} ({v['views']} views)" for i, v in enumerate(vids))
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


# ---------------- Article ----------------
PROMPT = """You are a careful health writer for an Indian blog named "FitLife India".
Topic: {topic}
{claim}
Write a "सच या झूठ" (fact-check) article. Use Google Search (when available) to verify facts from reliable sources (WHO, ICMR, NIH, AIIMS, Mayo Clinic, peer-reviewed studies). Write original content in your own words and your own structure.

Language: very simple everyday Hindi in Devanagari, the way people talk. Use common English words people already use (weight loss, diet, workout, protein, tips, doctor). Avoid heavy/shuddh Hindi words. Short sentences. 1200-1600 words.

Strict rules:
- Medical safety: no cure claims, no guaranteed results, no "detox" or "boost immunity" promises, no medicine/supplement doses. Use cautious wording ("मदद कर सकता है", "पक्का सबूत नहीं है"). Never discourage seeing a doctor.
- Do NOT invent statistics, percentages, study names, quotes or numbers. If unsure, leave it out. Never say a doctor reviewed the article.
- Structure:
  1. 2-line hook (relatable question) + direct answer.
  2. <h2>सच या झूठ? सीधा जवाब</h2> with a <blockquote> giving the verdict: सच / आधा सच / पक्का सबूत नहीं / झूठ, in 2-3 lines.
  3. <h2>वायरल दावा क्या है</h2>
  4. <h2>असल में क्या सही है</h2> (what is reasonable, what is unproven or exaggerated)
  5. <h2>कैसे अपनाएं (सुरक्षित तरीका)</h2> with practical Indian examples, include one HTML <table> with <thead>/<tbody> (क्या खाएं / क्या न खाएं, or a simple routine).
  6. <h2>किसे सावधानी रखनी चाहिए</h2>
  7. <h2>कब डॉक्टर से मिलें</h2>
  8. FAQ: one <h2>, then 4 questions as <h3>, each followed by a <p>.
  9. One short closing paragraph (share/comment), then ONE short <blockquote> medical disclaimer (only one disclaimer in the whole article).
- Allowed HTML only: h2, h3, p, ul, li, ol, strong, table, thead, tbody, tr, th, td, blockquote. No <h1>, <html>, <body>, markdown, code fences.

Output EXACTLY in this format (no extra text before it):
TITLE: <own Hindi title with the main keyword + number or clear benefit, max 65 characters, must NOT copy any video title, no clickbait promises>
DESCRIPTION: <Hindi meta description, max 150 characters>
LABELS: <2 to 4 labels ONLY from this list, comma-separated: """ + ", ".join(ALLOWED_LABELS) + """>
IMAGE_QUERY: <2-4 English words for a stock photo search, topic specific>
IMAGE_PROMPT: <English prompt for a bright realistic topic-specific photo; no text, no watermark, no logo, no close-up faces>
===HTML===
<the article HTML>
"""

CHECK_PROMPT = """You are a strict medical-content safety reviewer. Read this Hindi health article HTML.
FAIL if it has any of: claims of curing/treating/reversing a disease, medicine or supplement doses, invented statistics/study names/quotes, guaranteed or absolute results, telling readers to stop medicines or skip a doctor, "detox" or "boost immunity" promises.
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
    labels = [x for x in labels if x in ALLOWED_LABELS][:4] or ["फिटनेस"]
    return {"title": field(head, "TITLE"), "desc": field(head, "DESCRIPTION"), "labels": labels,
            "image": field(head, "IMAGE_PROMPT"), "image_query": field(head, "IMAGE_QUERY"), "html": body}


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
        cap = "<br/><small>Image: AI-generated</small>"
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
    if not v:
        return ""
    t = htmllib.escape(v["title"])
    ch = htmllib.escape(v["channel"])
    return ('<h2>वायरल वीडियो</h2>'
            '<p><small>यह वीडियो हमारा नहीं है। इसमें कही गई बातें उसके क्रिएटर की हैं, '
            'हमारी जांच ऊपर लिखी है।</small></p>'
            f'<div style="position:relative;padding-bottom:56.25%;height:0;overflow:hidden;margin:12px 0">'
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


def publish(title, html, labels, mode):
    url = f"https://www.googleapis.com/blogger/v3/blogs/{BLOG_ID}/posts/"
    params = {"isDraft": "false" if mode == "live" else "true"}
    r = requests.post(url, params=params, headers={"Authorization": "Bearer " + access_token()},
                      json={"kind": "blogger#post", "title": title, "content": html, "labels": labels},
                      timeout=120)
    if r.status_code != 200:
        sys.exit(f"Blogger error {r.status_code}: {r.text[:500]}")
    return r.json().get("url") or r.json().get("id")


def main():
    done = json.load(open(HISTORY, encoding="utf-8")) if os.path.exists(HISTORY) else []
    pick = pick_topic(done)
    topic, risk, video = pick["topic"], pick["risk"], pick["video"]
    print("Topic:", topic, "| risk:", risk, "| video:", video["id"] if video else None, flush=True)

    claim = (f'The viral claim is going around in a popular video titled: "{video["title"]}". '
             "You have NOT seen the video; do not describe or copy it. Only fact-check the common claim.\n"
             if video else "")
    text, chunks = gemini(PROMPT.replace("{topic}", topic).replace("{claim}", claim), search=True)
    art = parse(text)
    if not art["title"] or len(art["html"]) < 2000:
        sys.exit("Article adhura/chhota aaya, post nahi ki gayi.\n" + text[:500])

    check_text, _ = gemini(CHECK_PROMPT + art["html"][:20000])
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
    if words < 700:
        reasons.append(f"article chhota ({words} shabd)")

    mode = MODE
    labels = list(art["labels"])
    html = image_html(art)
    if art["desc"]:
        html += f"<p><b>{htmllib.escape(art['desc'])}</b></p>"
    if reasons and mode == "live":
        mode = "draft"
    if reasons:
        labels.append("तथ्य-जांच बाकी")
        html = ('<p style="background:#fdecea;border:1px solid #d93025;padding:10px;color:#a50e0e">'
                '[DRAFT NOTE: ' + htmllib.escape("; ".join(reasons)) + '. Publish se pehle facts check '
                'karein, aur ye note tatha label "तथ्य-जांच बाकी" hata dein.]</p>') + html
    html += style_html(art["html"]) + video_embed(video) + sources_html(chunks) + AUTHOR_BOX

    link = publish(art["title"], html, labels, mode)
    print("Done:", mode, link, flush=True)

    done.append(topic)
    if video:
        done.append("yt:" + video["id"])
    json.dump(done, open(HISTORY, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
