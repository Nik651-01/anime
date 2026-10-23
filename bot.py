import os
import time
import requests
import feedparser
from datetime import datetime, timezone
from google import genai

# Load secrets from GitHub Environment
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

# Look back 65 minutes (3900 seconds) to ensure no gaps between hourly runs
NOW = int(time.time())
ONE_HOUR_AGO = NOW - 3900

def get_aired_episodes():
    """Fetches anime episodes that aired in the last hour via AniList GraphQL."""
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
        res = requests.post(url, json={"query": query, "variables": {"start": ONE_HOUR_AGO, "end": NOW}}, timeout=15)
        schedules = res.json().get("data", {}).get("Page", {}).get("airingSchedules", [])
        results = []
        for item in schedules:
            title = item["media"]["title"]["english"] or item["media"]["title"]["romaji"]
            ep = item["episode"]
            link = item["media"]["siteUrl"]
            results.append(f"- {title} (Episode {ep}) - {link}")
        return results
    except Exception as e:
        print(f"AniList Error: {e}")
        return []

def get_rss_updates(feed_url, category_name):
    """Fetches RSS items published in the last hour."""
    headers = {"User-Agent": "HourlyAnimeDigestBot/1.0"}
    items = []
    try:
        res = requests.get(feed_url, headers=headers, timeout=15)
        feed = feedparser.parse(res.content)
        for entry in feed.entries[:20]:
            pub_parsed = entry.get("published_parsed") or entry.get("updated_parsed")
            if pub_parsed:
                pub_ts = int(datetime(*pub_parsed[:6], tzinfo=timezone.utc).timestamp())
                if pub_ts >= ONE_HOUR_AGO:
                    summary_text = entry.get("summary", "")[:500]
                    items.append(f"[{category_name}] {entry.title}\nLink: {entry.link}\nSnippet: {summary_text}")
    except Exception as e:
        print(f"RSS Error ({category_name}): {e}")
    return items

def summarize_with_gemini(episodes, news, discussions):
    """Uses Gemini to create a clean Telegram HTML digest with spoiler tags."""
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    raw_content = (
        f"NEWLY AIRED EPISODES:\n" + ("\n".join(episodes) if episodes else "None this hour.") + "\n\n"
        f"BREAKING ANIME & MANGA NEWS:\n" + ("\n".join(news) if news else "None this hour.") + "\n\n"
        f"EPISODE & MANGA CHAPTER DISCUSSIONS:\n" + ("\n".join(discussions) if discussions else "None this hour.")
    )

    prompt = f"""
    You are an hourly Anime & Manga update bot for Telegram.
    Format the following raw data from the past hour into a punchy, engaging digest.
    
    Rules:
    - Use ONLY Telegram-supported HTML tags: <b>bold</b>, <i>italic</i>, <a href="url">link</a>, and <tg-spoiler>spoiler text</tg-spoiler>. Do NOT use Markdown (** or #) or <ul>/<li>/<br> tags.
    - Organize into 3 sections with emojis:
      1. 📺 <b>Just Aired This Hour</b> (list the anime & episode numbers)
      2. 🚨 <b>Breaking Anime & Manga News</b> (1-sentence summary per news item with link)
      3. 💬 <b>Plot Recaps & Chapter Buzz</b> (summarize what happened or what fans are discussing; wrap any plot spoilers inside <tg-spoiler>...</tg-spoiler> tags!)
    - Keep the total output under 3500 characters.

    Raw Data:
    {raw_content}
    """
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )
    return response.text

def send_telegram_message(text):
    """Sends the formatted HTML message to Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    res = requests.post(url, json=payload, timeout=15)
    if not res.ok:
        # Fallback to plain text if HTML tags were slightly malformed
        payload.pop("parse_mode")
        requests.post(url, json=payload, timeout=15)

def main():
    episodes = get_aired_episodes()
    news = get_rss_updates("https://www.animenewsnetwork.com/all/rss.xml", "News")
    anime_disc = get_rss_updates("https://www.reddit.com/r/anime/search.rss?q=flair%3AEpisode&restrict_sr=on&sort=new", "Anime Episode Discussion")
    manga_disc = get_rss_updates("https://www.reddit.com/r/manga/search.rss?q=%5BDISC%5D&restrict_sr=on&sort=new", "Manga Chapter Discussion")
    
    discussions = anime_disc + manga_disc

    # Only send a message if something actually happened in the last hour
    if not episodes and not news and not discussions:
        print("Quiet hour — no new episodes, news, or major chapters dropped.")
        return

    digest = summarize_with_gemini(episodes, news, discussions)
    send_telegram_message(digest)
    print("Hourly digest sent successfully!")

if __name__ == "__main__":
    main()
