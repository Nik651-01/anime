import os
import sys
import time
import requests
import feedparser
from datetime import datetime, timezone
from google import genai

# 1. Safely check Secrets and print helpful messages if any are missing
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

missing = []
if not TELEGRAM_TOKEN: missing.append("TELEGRAM_TOKEN")
if not TELEGRAM_CHAT_ID: missing.append("TELEGRAM_CHAT_ID")
if not GEMINI_API_KEY: missing.append("GEMINI_API_KEY")

if missing:
    print(f"❌ ERROR: Missing GitHub Secrets: {', '.join(missing)}")
    print("Go to Settings -> Secrets and variables -> Actions and make sure these exact names are added under 'Repository secrets'.")
    sys.exit(1)

# Look back 6 hours on manual runs so you always get a test message, or 65 mins on hourly schedule
EVENT_NAME = os.environ.get("GITHUB_EVENT_NAME", "")
LOOKBACK_SECONDS = 21600 if EVENT_NAME == "workflow_dispatch" else 3900
NOW = int(time.time())
START_TIME = NOW - LOOKBACK_SECONDS

def get_aired_episodes():
    url = "https://graphql.anilist.co"
    query = """
    query ($start: Int, $end: Int) {
      Page(perPage: 25) {
        airingSchedules(airingAt_greater: $start, airingAt_lesser: $end, sort: TIME_DESC) {
          episode
          media {
            title { romaji english }
            siteUrl
          }
        }
      }
    }
    """
    try:
        res = requests.post(url, json={"query": query, "variables": {"start": START_TIME, "end": NOW}}, timeout=15)
        schedules = res.json().get("data", {}).get("Page", {}).get("airingSchedules", [])
        results = []
        for item in schedules:
            title = item["media"]["title"]["english"] or item["media"]["title"]["romaji"]
            ep = item["episode"]
            link = item["media"]["siteUrl"]
            results.append(f"- {title} (Episode {ep}) - {link}")
        return results
    except Exception as e:
        print(f"⚠️ AniList Error: {e}")
        return []

def get_rss_updates(feed_url, category_name):
    headers = {"User-Agent": "Mozilla/5.0 (compatible; HourlyAnimeDigestBot/1.0)"}
    items = []
    try:
        res = requests.get(feed_url, headers=headers, timeout=15)
        feed = feedparser.parse(res.content)
        for entry in feed.entries[:20]:
            pub_parsed = entry.get("published_parsed") or entry.get("updated_parsed")
            if pub_parsed:
                pub_ts = int(datetime(*pub_parsed[:6], tzinfo=timezone.utc).timestamp())
                if pub_ts >= START_TIME:
                    summary_text = entry.get("summary", "")[:400]
                    items.append(f"[{category_name}] {entry.title}\nLink: {entry.link}\nSnippet: {summary_text}")
    except Exception as e:
        print(f"⚠️ RSS Error ({category_name}): {e}")
    return items

def summarize_with_gemini(episodes, news, discussions):
    client = genai.Client(api_key=GEMINI_API_KEY)
    raw_content = (
        f"NEWLY AIRED EPISODES:\n" + ("\n".join(episodes) if episodes else "None.") + "\n\n"
        f"BREAKING ANIME & MANGA NEWS:\n" + ("\n".join(news) if news else "None.") + "\n\n"
        f"EPISODE & MANGA CHAPTER DISCUSSIONS:\n" + ("\n".join(discussions) if discussions else "None.")
    )
    prompt = f"""
    You are an Anime & Manga update bot for Telegram.
    Summarize the following updates into a clean, engaging digest.
    
    Rules:
    - Use ONLY Telegram HTML tags: <b>bold</b>, <i>italic</i>, <a href="url">link</a>, and <tg-spoiler>spoiler</tg-spoiler>. Do NOT use Markdown (** or #) or <ul>/<li>/<br> tags.
    - Organize into 3 sections:
      📺 <b>Recently Aired Episodes</b>
      🚨 <b>Breaking Anime & Manga News</b>
      💬 <b>Plot Recaps & Chapter Buzz</b> (wrap any plot spoilers inside <tg-spoiler>...</tg-spoiler> tags!)
    - Keep total length under 3500 characters.

    Raw Data:
    {raw_content}
    """
    response = client.models.generate_content(
        model="gemini-3.0-flash",
        contents=prompt,
    )
    return response.text

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    res = requests.post(url, json=payload, timeout=15)
    if not res.ok:
        print(f"⚠️ HTML parse issue ({res.text}), retrying as plain text...")
        payload.pop("parse_mode")
        res2 = requests.post(url, json=payload, timeout=15)
        if not res2.ok:
            print(f"❌ Telegram API Error: {res2.text}")
            sys.exit(1)

def main():
    print(f"Fetching updates from the last {LOOKBACK_SECONDS // 60} minutes...")
    episodes = get_aired_episodes()
    news = get_rss_updates("https://www.animenewsnetwork.com/all/rss.xml", "News")
    anime_disc = get_rss_updates("https://www.reddit.com/r/anime/search.rss?q=flair%3AEpisode&restrict_sr=on&sort=new", "Anime Episode Discussion")
    manga_disc = get_rss_updates("https://www.reddit.com/r/manga/search.rss?q=%5BDISC%5D&restrict_sr=on&sort=new", "Manga Chapter Discussion")
    discussions = anime_disc + manga_disc

    print(f"Found: {len(episodes)} episodes, {len(news)} news items, {len(discussions)} discussions.")

    if not episodes and not news and not discussions:
        send_telegram_message("🤖 <b>Anime Bot Test Successful!</b>\nNo new episodes or news in the window, but your Telegram connection is working!")
        return

    digest = summarize_with_gemini(episodes, news, discussions)
    send_telegram_message(digest)
    print("✅ Digest sent to Telegram successfully!")

if __name__ == "__main__":
    main()
