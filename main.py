import os, re, sys, json, time, datetime
from urllib.parse import quote
import requests
import feedparser

GEMINI_KEY = os.environ["GEMINI_API_KEY"]
MODEL = os.environ.get("GEMINI_MODEL") or "gemini-3-flash-preview"
BLOG_ID = os.environ["BLOG_ID"]
CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
REFRESH_TOKEN = os.environ["GOOGLE_REFRESH_TOKEN"]
MODE = os.environ.get("PUBLISH_MODE") or "draft"
USE_IMAGE = (os.environ.get("USE_IMAGE") or "1") == "1"
HISTORY = "posted.json"


def gemini(prompt, search=False, retries=4):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    if search:
        body["tools"] = [{"google_search": {}}]
    headers = {"x-goog-api-key": GEMINI_KEY, "Content-Type": "application/json"}
    for i in range(retries):
        r = requests.post(url, headers=headers, json=body, timeout=300)
        if r.status_code == 200:
            cand = r.json()["candidates"][0]
            parts = cand.get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
            chunks = cand.get("groundingMetadata", {}).get("groundingChunks", [])
            return text.strip(), chunks
        if r.status_code in (429, 500, 503):
            time.sleep(20 * (i + 1))
            continue
        sys.exit(f"Gemini error {r.status_code}: {r.text[:500]}")
    sys.exit("Gemini: bar-bar fail hua, baad me try karein")


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

Use Google Search to verify facts from reliable sources (WHO, ICMR, NIH, AIIMS, Mayo Clinic, peer-reviewed studies). Never copy text from any source; write original content.

Write in simple, natural Hindi (Devanagari script), 1200+ words.
Rules:
- Medical safety: no cure claims, no guaranteed results, no exact medicine doses, never discourage seeing a doctor. Mention when to see a doctor.
- Begin with a short disclaimer paragraph and end with a final disclaimer paragraph.
- Use <h2>/<h3> headings (use the main keyword naturally), <p>, <ul>/<li>, and at least one HTML <table> with <thead>/<tbody> where useful (diet plan, nutrient chart, etc.).
- Add an FAQ section: one <h2>, then 4 questions as <h3> each followed by <p>.
- Do NOT use <h1>, <html>, <body>, markdown, or code fences.

Output EXACTLY in this format:
TITLE: <click-worthy Hindi title, max 65 characters>
DESCRIPTION: <Hindi meta description, max 150 characters>
LABELS: <3 to 5 comma-separated labels>
IMAGE_PROMPT: <English prompt for a bright, wholesome, realistic stock-style photo, no text, no close-up faces>
===HTML===
<the article HTML>
"""


def field(head, key):
    m = re.search(rf"^{key}:\s*(.+)$", head, re.M)
    return m.group(1).strip() if m else ""


def parse(text):
    head, _, body = text.partition("===HTML===")
    body = re.sub(r"^```(?:html)?\s*|\s*```$", "", body.strip())
    labels = [x.strip() for x in field(head, "LABELS").split(",") if x.strip()][:5]
    return {
        "title": field(head, "TITLE"),
        "desc": field(head, "DESCRIPTION"),
        "labels": labels,
        "image": field(head, "IMAGE_PROMPT"),
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


def access_token():
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN, "grant_type": "refresh_token"}, timeout=60)
    if r.status_code != 200:
        sys.exit(f"Token error {r.status_code}: {r.text[:300]}")
    return r.json()["access_token"]


def publish(title, html, labels):
    url = f"https://www.googleapis.com/blogger/v3/blogs/{BLOG_ID}/posts/"
    params = {"isDraft": "false" if MODE == "live" else "true"}
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

    html = ""
    if USE_IMAGE and art["image"]:
        img = ("https://image.pollinations.ai/prompt/" + quote(art["image"]) +
               "?width=1200&height=630&nologo=true")
        html += (f'<div style="text-align:center"><img src="{img}" alt="{art["title"]}" '
                 'style="max-width:100%;height:auto"/></div>')
    if art["desc"]:
        html += f"<p><b>{art['desc']}</b></p>"
    html += art["html"] + sources_html(chunks)

    link = publish(art["title"], html, art["labels"])
    print("Done:", MODE, link)

    done.append(topic)
    json.dump(done, open(HISTORY, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
