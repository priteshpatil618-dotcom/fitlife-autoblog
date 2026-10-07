import os, re, sys, json, time, datetime, random, html as htmllib
from urllib.parse import quote
import requests
import feedparser

GEMINI_KEY = os.environ["GEMINI_API_KEY"]
DEFAULT_MODELS = ("gemini-3.1-flash-lite,gemini-3.5-flash-lite,gemini-3-flash-preview,"
                  "gemini-2.5-flash-lite,gemini-2.5-flash")
MODELS = [m.strip() for m in (os.environ.get("GEMINI_MODEL") or DEFAULT_MODELS).split(",") if m.strip()]
WORKING = []
NO_SEARCH_USED = False
BLOG_ID = os.environ["BLOG_ID"]
CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
REFRESH_TOKEN = os.environ["GOOGLE_REFRESH_TOKEN"]
MODE = os.environ.get("PUBLISH_MODE") or "draft"      # draft ya live
USE_IMAGE = (os.environ.get("USE_IMAGE") or "1") == "1"
HISTORY = "posted.json"
ALLOWED_LABELS = ["फिटनेस", "डाइट", "योग", "इम्यूनिटी", "मौसमी स्वास्थ्य", "वजन प्रबंधन",
                  "घरेलू उपाय", "सामान्य रोग", "महिला स्वास्थ्य", "बच्चों का स्वास्थ्य",
                  "मानसिक स्वास्थ्य"]


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
                            print("WARNING: search grounding nahi chala, bina search ke likha gaya", flush=True)
                        print(f"OK model={model} search={use_search}", flush=True)
                        return text.strip(), chunks
                print(f"FAIL model={model} search={use_search} status={r.status_code} {r.text[:300]}", flush=True)
                if r.status_code in (500, 503) and attempt == 0:
                    time.sleep(15)
                    continue
                break
            time.sleep(3)
    sys.exit("Gemini: koi bhi model nahi chala. Upar ke FAIL lines dekhein.")


def trending():
    try:
        feed = feedparser.parse("https://trends.google.com/trending/rss?geo=IN")
        return [e.title for e in feed.entries][:40]
    except Exception:
        return []


def pick_topic(done):
    avoid = "\n".join(done[-60:]) or "(none)"
    trends = trending()
    if trends:
        text, _ = gemini(
            "Below are today's trending searches in India:\n" + "\n".join(trends) +
            "\n\nPick ONE that relates to health, fitness, diet, yoga, nutrition or a common "
            "illness AND can be written about safely and factually. Do not pick any of these "
            "already-covered topics:\n" + avoid +
            "\n\nReply with only the topic name. If nothing suits, reply only: NONE"
        )
        line = text.splitlines()[0].strip() if text else "NONE"
        if line and line.upper() != "NONE":
            return line
    today = datetime.date.today().strftime("%d %B %Y")
    text, _ = gemini(
        f"Today is {today}. Suggest ONE useful, evergreen-but-timely health or fitness topic "
        "for Hindi readers in India, fitting the current season. Avoid these already-covered "
        "topics:\n" + avoid + "\n\nReply with only the topic name.",
        search=True,
    )
    return text.splitlines()[0].strip()


PROMPT = """You are a senior health-research writer for an Indian Hindi blog named "FitLife India".
Topic: {topic}

Use Google Search (when available) to verify facts from reliable sources (WHO, ICMR, NIH, AIIMS, Mayo Clinic, peer-reviewed studies). Never copy text from any source; write original content.

Write in simple, natural, friendly Hindi (Devanagari script), 1500-2000 words, for Indian readers (use Indian foods, seasons, household items, lifestyle examples).
Strict rules:
- Medical safety: no cure claims, no guaranteed results, no "detox" or "boost immunity" promises, no exact medicine doses. Use cautious wording such as "मदद कर सकता है". Never discourage seeing a doctor.
- Do NOT invent statistics, percentages, study names, quotes or numbers. If you are not sure about a figure, leave it out. Never write that a doctor reviewed the article.
- Start with a 2-line hook (a relatable question or situation) and a direct answer. Then one short <blockquote> with a 1-2 sentence disclaimer.
- Then a "संक्षेप में" <h2> followed by a <ul> of 4-5 key points.
- Then 5-7 <h2> sections (use the main keyword naturally in some). Keep paragraphs to 2-3 sentences. Use <h3>, <ul>/<li> and practical tips. Include at least one HTML <table> with <thead>/<tbody> (for example a diet plan or food chart).
- Include an <h2> "कब डॉक्टर से मिलें".
- Include an FAQ section: one <h2>, then 5 questions as <h3>, each followed by a <p> answer.
- End with one short closing paragraph asking readers to share the article and comment, then one final <blockquote> with the full medical disclaimer.
- Allowed HTML only: h2, h3, p, ul, li, ol, strong, table, thead, tbody, tr, th, td, blockquote. Do NOT use <h1>, <html>, <body>, markdown or code fences.

Output EXACTLY in this format (no extra text before it):
TITLE: <Hindi title: main keyword + a number or clear benefit, max 65 characters, no clickbait promises>
DESCRIPTION: <Hindi meta description, max 150 characters>
LABELS: <2 to 4 labels, chosen ONLY from this exact list, comma-separated: फिटनेस, डाइट, योग, इम्यूनिटी, मौसमी स्वास्थ्य, वजन प्रबंधन, घरेलू उपाय, सामान्य रोग, महिला स्वास्थ्य, बच्चों का स्वास्थ्य, मानसिक स्वास्थ्य>
IMAGE_QUERY: <2-4 English words to search a stock photo, topic specific, e.g. a food, activity or setting>
IMAGE_PROMPT: <English prompt for a bright, realistic, topic-specific photo; no text, no watermark, no logo, no close-up faces>
===HTML===
<the article HTML>
"""


def field(head, key):
    m = re.search(rf"^{key}:\s*(.+)$", head, re.M)
    return m.group(1).strip() if m else ""


def parse(text):
    head, _, body = text.partition("===HTML===")
    body = re.sub(r"^```(?:html)?\s*|\s*```$", "", body.strip())
    labels = [x.strip() for x in field(head, "LABELS").split(",") if x.strip()]
    labels = [x for x in labels if x in ALLOWED_LABELS][:4] or ["फिटनेस"]
    return {
        "title": field(head, "TITLE"),
        "desc": field(head, "DESCRIPTION"),
        "labels": labels,
        "image": field(head, "IMAGE_PROMPT"),
        "image_query": field(head, "IMAGE_QUERY"),
        "html": body,
    }


def sources_html(chunks):
    seen, items = set(), []
    for c in chunks:
        w = c.get("web") or {}
        t, u = w.get("title"), w.get("uri")
        if t and u and t not in seen:
            seen.add(t)
            items.append(f'<li><a href="{u}" rel="nofollow noopener" target="_blank">{t}</a></li>')
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
    if px:
        src, name, link = px
        cap = f'<br/><small>Photo: <a href="{link}" rel="nofollow noopener" target="_blank">{htmllib.escape(name)}</a> / Pexels</small>'
    elif USE_IMAGE and art["image"]:
        prompt = art["image"] + ", no text, no watermark, no logo"
        src = "https://image.pollinations.ai/prompt/" + quote(prompt) + "?width=1200&height=630&nologo=true"
        cap = ""
    else:
        return ""
    return (f'<div style="text-align:center;margin-bottom:12px"><img src="{src}" alt="{alt}" '
            f'style="max-width:100%;height:auto;border-radius:8px"/>{cap}</div>')


AUTHOR_BOX = ('<blockquote><b>लेखक:</b> FitLife India टीम &middot; यह लेख केवल सामान्य जानकारी के लिए है। '
              'किसी भी स्वास्थ्य समस्या में अपने डॉक्टर से सलाह लें।</blockquote>')


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
    r = requests.post(url, params=params,
                      headers={"Authorization": "Bearer " + access_token()},
                      json={"kind": "blogger#post", "title": title,
                            "content": html, "labels": labels}, timeout=120)
    if r.status_code != 200:
        sys.exit(f"Blogger error {r.status_code}: {r.text[:500]}")
    return r.json().get("url") or r.json().get("id")


def main():
    done = json.load(open(HISTORY, encoding="utf-8")) if os.path.exists(HISTORY) else []
    topic = pick_topic(done)
    print("Topic:", topic)

    text, chunks = gemini(PROMPT.replace("{topic}", topic), search=True)
    art = parse(text)
    if not art["title"] or len(art["html"]) < 2000:
        sys.exit("Article adhura/chhota aaya, post nahi ki gayi.\n" + text[:500])

    html = image_html(art)
    if art["desc"]:
        html += f"<p><b>{htmllib.escape(art['desc'])}</b></p>"
    labels = list(art["labels"])
    mode = MODE
    if NO_SEARCH_USED:
        mode = "draft"
        labels.append("तथ्य-जांच बाकी")
        html = ('<p style="background:#fdecea;border:1px solid #d93025;padding:10px;color:#a50e0e">'
                '[DRAFT NOTE: ye article bina Google Search ke bana hai. Publish se pehle facts '
                'zaroor check karein, aur ye note tatha label "तथ्य-जांच बाकी" hata dein.]</p>') + html
    html += style_html(art["html"]) + sources_html(chunks) + AUTHOR_BOX

    link = publish(art["title"], html, labels, mode)
    print("Done:", mode, link)

    done.append(topic)
    json.dump(done, open(HISTORY, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
