import os
import sys
import time
import requests
import feedparser
from google import genai

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

if not all([TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, GEMINI_API_KEY]):
    print("❌ ERROR: Missing GitHub Secrets.")
    sys.exit(1)

HEADERS = {
    "User-Agent": "linux:anime.command.bot:v5.0 (by /u/anime_digest_bot)",
    "Accept": "application/rss+xml, application/xml, text/xml, */*"
}

def send_telegram_message(text, chat_id=TELEGRAM_CHAT_ID):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    res = requests.post(url, json=payload, timeout=15)
    if not res.ok:
        payload.pop("parse_mode", None)
        requests.post(url, json=payload, timeout=15)

def ask_gemini(prompt):
    client = genai.Client(api_key=GEMINI_API_KEY)
    candidate_models = [
        "gemini-3-flash-preview",
        "gemini-3.0-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.0-flash",
        "gemini-2.5-flash",
    ]
    try:
        for m in client.models.list():
            name = m.name.replace("models/", "")
            if "gemini" in name and ("flash" in name or "pro" in name) and "tts" not in name and "image" not in name:
                if name not in candidate_models:
                    candidate_models.append(name)
    except Exception:
        pass

    for model_name in candidate_models:
        try:
            res = client.models.generate_content(model=model_name, contents=prompt)
            if res and res.text:
                return res.text
        except Exception as e:
            print(f"⚠️ {model_name} busy ({e}), trying next...")
            time.sleep(1)
    return "⚠️ Gemini AI is temporarily at max capacity. Please try your command again in 30 seconds!"

def fetch_anilist_data(search_title=None):
    url = "https://graphql.anilist.co"
    now = int(time.time())
    day_ago = now - 86400

    if search_title:
        query = """
        query ($search: String) {
          Media(search: $search, type: ANIME) {
            title { english romaji }
            averageScore
            trending
            popularity
            episodes
            status
            genres
            description(asHtml: false)
            siteUrl
            reviews(limit: 3, sort: RATING_DESC) {
              nodes { summary score }
            }
          }
        }
        """
        res = requests.post(url, json={"query": query, "variables": {"search": search_title}}, timeout=15)
        return res.json().get("data", {})

    query = """
    query ($start: Int, $end: Int) {
      aired: Page(perPage: 15) {
        airingSchedules(airingAt_greater: $start, airingAt_lesser: $end, sort: TIME_DESC) {
          episode
          media {
            title { english romaji }
            averageScore
            trending
            siteUrl
          }
        }
      }
      trending: Page(perPage: 8) {
        media(type: ANIME, status: RELEASING, sort: TRENDING_DESC) {
          title { english romaji }
          averageScore
          trending
          popularity
          nextAiringEpisode { episode }
          description(asHtml: false)
          siteUrl
          reviews(limit: 2, sort: CREATED_AT_DESC) {
            nodes { summary score }
          }
        }
      }
      upcoming_hype: Page(perPage: 5) {
        media(type: ANIME, status: NOT_YET_RELEASED, sort: POPULARITY_DESC) {
          title { english romaji }
          popularity
          trending
          description(asHtml: false)
          siteUrl
        }
      }
      top_airing: Page(perPage: 10) {
        media(type: ANIME, status: RELEASING, sort: SCORE_DESC) {
          title { english romaji }
          averageScore
          trending
          popularity
          nextAiringEpisode { episode }
        }
      }
    }
    """
    res = requests.post(url, json={"query": query, "variables": {"start": day_ago, "end": now}}, timeout=15)
    return res.json().get("data", {})

def fetch_rss_buzz():
    feeds = [
        ("Reddit Episode Thread", "https://www.reddit.com/r/anime/search.rss?q=flair%3AEpisode&restrict_sr=on&sort=hot&t=day"),
        ("Anime News", "https://www.animenewsnetwork.com/all/rss.xml")
    ]
    items = []
    for label, url in feeds:
        try:
            res = requests.get(url, headers=HEADERS, timeout=12)
            feed = feedparser.parse(res.content)
            for entry in feed.entries[:6]:
                snippet = entry.get("summary", "")[:300].replace("\n", " ")
                items.append(f"[{label}] {entry.title} | {snippet}")
        except Exception:
            pass
    return items

def handle_update():
    data = fetch_anilist_data()
    buzz = fetch_rss_buzz()

    aired_lines = []
    for item in data.get("aired", {}).get("airingSchedules", []):
        m = item["media"]
        title = m["title"]["english"] or m["title"]["romaji"]
        score = f"{m['averageScore']}/100" if m.get("averageScore") else "N/A"
        aired_lines.append(f"{title} — Episode {item['episode']} (Rating: {score})")

    trending_lines = []
    for idx, m in enumerate(data.get("trending", {}).get("media", [])[:6], 1):
        title = m["title"]["english"] or m["title"]["romaji"]
        ep = (m["nextAiringEpisode"]["episode"] - 1) if m.get("nextAiringEpisode") else "Latest"
        score = m.get("averageScore") or "N/A"
        desc = (m.get("description") or "")[:250].replace("\n", " ")
        trending_lines.append(f"{idx}. {title} | Ep {ep} | Rating: {score} | Trend Score: {m.get('trending', 0)}\nContext: {desc}")

    prompt = f"""
    Format an anime report in this EXACT structure and style using ONLY Telegram HTML tags (<b>bold</b>, <i>italic</i>):

    1. 📺 <b>Aired Anime List</b>
    (List the aired anime in format: Title — Episode X (Rating: XX/100))

    2. 🔥 <b>Trending Anime & Episode Ratings</b>
    (Numbered list 1 to 6 in format: 1. Title | Ep X | Rating: XX | Trend Score: XXX)

    3. 🚀 <b>Why These Episodes Are Trending</b>
    (For each top trending anime, write a rich 2-3 sentence paragraph explaining the exact plot hook, character moment, or hype factor driving the episode right now)

    4. 💬 <b>Fan Reactions & Community Buzz</b>
    (Write 3-4 energetic paragraphs capturing current fan debates, Reddit praise, and major anime news headlines from the data)

    Do NOT use Markdown (** or #) or <ul>/<li>/<br>. Keep under 3800 characters.

    Raw Data:
    AIRED:
    {chr(10).join(aired_lines)}

    TRENDING:
    {chr(10).join(trending_lines)}

    BUZZ & NEWS:
    {chr(10).join(buzz)}
    """
    return ask_gemini(prompt)

def handle_trending():
    data = fetch_anilist_data()
    trending_lines = []
    for idx, m in enumerate(data.get("trending", {}).get("media", [])[:8], 1):
        title = m["title"]["english"] or m["title"]["romaji"]
        ep = (m["nextAiringEpisode"]["episode"] - 1) if m.get("nextAiringEpisode") else "Latest"
        score = m.get("averageScore") or "N/A"
        desc = (m.get("description") or "")[:220].replace("\n", " ")
        trending_lines.append(f"{idx}. {title} | Ep {ep} | Rating: {score}/100 | Trend Score: {m.get('trending', 0)}\nInfo: {desc}")

    prompt = f"""
    Create today's 🔥 <b>Trending Anime of the Day</b> report for Telegram using ONLY HTML tags (<b>, <i>):
    1. List the Top 8 Trending Anime today in clean numbered format: `1. <b>Title</b> | Ep X | Rating: XX | Trend Score: XXX`
    2. Below the list, include a short breakdown of why the Top 3 shows are dominating today's charts.
    Do NOT use Markdown (** or #).

    Data:
    {chr(10).join(trending_lines)}
    """
    return ask_gemini(prompt)

def handle_hype():
    data = fetch_anilist_data()
    buzz = fetch_rss_buzz()
    trending = data.get("trending", {}).get("media", [])[:5]
    upcoming = data.get("upcoming_hype", {}).get("media", [])[:5]

    raw = "CURRENTLY AIRING HYPE:\n"
    for m in trending:
        t = m["title"]["english"] or m["title"]["romaji"]
        raw += f"- {t} (Trend Score: {m.get('trending', 0)}, Rating: {m.get('averageScore')})\n"

    raw += "\nMOST HYPED UPCOMING ANIME:\n"
    for m in upcoming:
        t = m["title"]["english"] or m["title"]["romaji"]
        raw += f"- {t} ({m.get('popularity', 0)} fans waiting) | {(m.get('description') or '')[:180]}\n"

    raw += "\nBREAKING NEWS:\n" + "\n".join(buzz[:6])

    prompt = f"""
    Create an ⚡ <b>Anime Hype Radar</b> report for Telegram using ONLY HTML tags (<b>, <i>):
    - Section 1: 🚨 <b>Hype Meter: Currently Exploding</b> (Which airing anime has massive hype right now & why)
    - Section 2: ⏳ <b>Most Hyped Upcoming Releases</b> (Shows fans are waiting for most)
    - Section 3: 📰 <b>Hyped Industry Announcements</b>
    Do NOT use Markdown (** or #).

    Data:
    {raw}
    """
    return ask_gemini(prompt)

def handle_top():
    data = fetch_anilist_data()
    top_airing = data.get("top_airing", {}).get("media", [])[:10]
    lines = ["🏆 <b>Top 10 Highest-Rated Airing Anime Right Now</b>\n"]
    for idx, m in enumerate(top_airing, 1):
        title = m["title"]["english"] or m["title"]["romaji"]
        ep = (m["nextAiringEpisode"]["episode"] - 1) if m.get("nextAiringEpisode") else "Airing"
        score = m.get("averageScore") or "N/A"
        trend = m.get("trending", 0)
        lines.append(f"{idx}. <b>{title}</b>\n   ⭐ Rating: <b>{score}/100</b> | 📺 Ep: {ep} | 🔥 Trend: {trend}")
    return "\n\n".join(lines)

def handle_summary(custom_query=""):
    if custom_query:
        data = fetch_anilist_data(search_title=custom_query)
        m = data.get("Media")
        if not m:
            return f"❓ Could not find an anime matching '<b>{custom_query}</b>'. Try `/summary One Piece` or just `/summary`!"
        title = m["title"]["english"] or m["title"]["romaji"]
        raw = f"Title: {title}\nScore: {m.get('averageScore')}/100\nGenres: {', '.join(m.get('genres', []))}\nSynopsis: {m.get('description')}"
        prompt = f"""
        Write an engaging Telegram spotlight for <b>{title}</b> using ONLY HTML tags (<b>, <i>):
        1. 📖 <b>Plot Summary</b> (Clear, spoiler-free hook)
        2. ✨ <b>Why It's So Interesting & Worth Watching</b> (Themes, animation/story appeal, and why fans love it)
        3. ⭐ <b>Verdict & Rating</b> ({m.get('averageScore')}/100)
        Do NOT use Markdown (** or #).
        Data: {raw}
        """
        return ask_gemini(prompt)
    else:
        data = fetch_anilist_data()
        top_shows = data.get("trending", {}).get("media", [])[:4]
        raw = ""
        for m in top_shows:
            title = m["title"]["english"] or m["title"]["romaji"]
            raw += f"Title: {title} (Rating: {m.get('averageScore')}/100)\nSynopsis: {(m.get('description') or '')[:350]}\n\n"
        prompt = f"""
        For each of these 4 trending anime, write a mini guide using ONLY Telegram HTML tags (<b>, <i>):
        - 📖 <b>What It's About</b> (2-sentence summary)
        - ✨ <b>Why It's So Interesting</b> (What makes the premise or current season special)
        Tip at the bottom: Mention that the user can also type `/summary <anime name>` for any specific show!
        Do NOT use Markdown (** or #).
        Data:
        {raw}
        """
        return ask_gemini(prompt)

def process_command(text, chat_id):
    parts = text.strip().split(maxsplit=1)
    cmd = parts[0].lower().split("@")[0]
    arg = parts[1] if len(parts) > 1 else ""

    try:
        if cmd == "/update":
            send_telegram_message("⏳ <i>Generating full 4-part Anime Update...</i>", chat_id)
            result = handle_update()
            send_telegram_message(result, chat_id)
        elif cmd == "/trending":
            send_telegram_message("⏳ <i>Fetching today's Trending Anime...</i>", chat_id)
            result = handle_trending()
            send_telegram_message(result, chat_id)
        elif cmd == "/hype":
            send_telegram_message("⏳ <i>Scanning the Anime Hype Radar...</i>", chat_id)
            result = handle_hype()
            send_telegram_message(result, chat_id)
        elif cmd == "/top":
            result = handle_top()
            send_telegram_message(result, chat_id)
        elif cmd == "/summary":
            send_telegram_message(f"⏳ <i>Writing summary & appeal breakdown{' for ' + arg if arg else ''}...</i>", chat_id)
            result = handle_summary(arg)
            send_telegram_message(result, chat_id)
        elif cmd in ["/start", "/help"]:
            help_msg = (
                "🤖 <b>Anime Command Bot Ready!</b>\n\n"
                "Use these commands anytime:\n"
                "• /update — Full 4-part Aired, Trending, Why & Reactions digest\n"
                "• /trending — Today's Trending Anime & episode ratings\n"
                "• /hype — Check which airing & upcoming anime are most hyped\n"
                "• /top — Top 10 highest-rated anime rankings\n"
                "• /summary — Summary of trending anime & why they're interesting\n"
                "• /summary <i>Anime Name</i> — Deep dive on any specific anime!"
            )
            send_telegram_message(help_msg, chat_id)
    except Exception as e:
        print(f"❌ Error processing command {cmd}: {e}")
        send_telegram_message(f"⚠️ Sorry, an error occurred while generating your <code>{cmd}</code> report. Please try again in a moment!", chat_id)

def main():
    # If triggered manually via GitHub Actions UI with an input command, run it instantly
    manual_cmd = os.environ.get("BOT_COMMAND", "").strip()
    if manual_cmd and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        print(f"⚡ Executing manual workflow command: {manual_cmd}")
        process_command(manual_cmd, TELEGRAM_CHAT_ID)
        return

    print("🎧 Bot is awake and listening for Telegram commands (/update, /trending, /hype, /top, /summary)...")
    
    # Get current update offset so we don't re-process ancient messages
    offset = None
    try:
        init_res = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?limit=1", timeout=10).json()
        results = init_res.get("result", [])
        if results:
            offset = results[-1]["update_id"] + 1
    except Exception:
        pass

    # Listen for 40 minutes (2400 seconds)
    end_time = time.time() + 2400

    while time.time() < end_time:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?timeout=30"
        if offset:
            url += f"&offset={offset}"
        try:
            res = requests.get(url, timeout=35).json()
            for item in res.get("result", []):
                offset = item["update_id"] + 1
                msg = item.get("message", {})
                text = msg.get("text", "").strip()
                chat_id = str(msg.get("chat", {}).get("id", ""))

                if text.startswith("/") and chat_id == str(TELEGRAM_CHAT_ID):
                    print(f"⚡ Received command: {text}")
                    process_command(text, chat_id)
        except Exception as e:
            print(f"Polling warning: {e}")
            time.sleep(3)

    print("⏰ 40-minute listener window finished. Shutting down until next hour's cron.")

if __name__ == "__main__":
    main()
