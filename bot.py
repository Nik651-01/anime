import os
import sys
import time
import requests
import feedparser
from datetime import datetime, timezone
from google import genai

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

if not all([TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, GEMINI_API_KEY]):
    print("❌ ERROR: Missing one or more GitHub Secrets (TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, GEMINI_API_KEY).")
    sys.exit(1)

EVENT_NAME = os.environ.get("GITHUB_EVENT_NAME", "")
LOOKBACK_SECONDS = 43200 if EVENT_NAME == "workflow_dispatch" else 3900
NOW = int(time.time())
START_TIME = NOW - LOOKBACK_SECONDS

HEADERS = {
    "User-Agent": "linux:anime.hype.bot:v4.0 (by /u/anime_digest_bot)",
    "Accept": "application/rss+xml, application/xml, text/xml, */*"
}

def get_aired_trending_and_reactions():
    """Fetches newly aired episodes, trending anime, AND live fan reviews/reactions from AniList."""
    url = "https://graphql.anilist.co"
    query = """
    query ($start: Int, $end: Int) {
      aired: Page(perPage: 20) {
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
      trending: Page(perPage: 6) {
        media(type: ANIME, status: RELEASING, sort: TRENDING_DESC) {
          title { english romaji }
          averageScore
          trending
          popularity
          nextAiringEpisode { episode }
          description(asHtml: false)
          siteUrl
          reviews(limit: 2, sort: CREATED_AT_DESC) {
            nodes {
              summary
              score
            }
          }
        }
      }
    }
    """
    aired_list = []
    trending_list = []
    anilist_reactions = []

    try:
        res = requests.post(url, json={"query": query, "variables": {"start": START_TIME, "end": NOW}}, timeout=15)
        data = res.json().get("data", {})

        for item in data.get("aired", {}).get("airingSchedules", []):
            media = item["media"]
            title = media["title"]["english"] or media["title"]["romaji"]
            score = f"{media['averageScore']}/100" if media.get("averageScore") else "Unrated"
            aired_list.append(
                f"- {title} | Episode {item['episode']} | Rating: {score} | Hype Score: {media.get('trending', 0)} | Link: {media['siteUrl']}"
            )

        for media in data.get("trending", {}).get("media", []):
            title = media["title"]["english"] or media["title"]["romaji"]
            score = f"{media['averageScore']}/100" if media.get("averageScore") else "Unrated"
            latest_ep = (media["nextAiringEpisode"]["episode"] - 1) if media.get("nextAiringEpisode") else "Latest"
            desc = (media.get("description") or "")[:250].replace("\n", " ")
            trending_list.append(
                f"- {title} (Latest Ep: {latest_ep}) | Rating: {score} | Trending Activity: {media.get('trending', 0)} | Fans: {media.get('popularity', 0)} | Link: {media['siteUrl']}\n  Context: {desc}"
            )
            for rev in media.get("reviews", {}).get("nodes", []):
                if rev.get("summary"):
                    anilist_reactions.append(f"[{title} Fan Reaction ({rev.get('score', '?')}/100)]: \"{rev['summary']}\"")

    except Exception as e:
        print(f"⚠️ AniList Error: {e}")

    return aired_list, trending_list, anilist_reactions

def get_reddit_rss_reactions():
    """Fetches Episode Discussions & News via RSS (bypasses JSON blocks) for community buzz."""
    feeds = [
        ("Episode Thread", "https://www.reddit.com/r/anime/search.rss?q=flair%3AEpisode&restrict_sr=on&sort=hot&t=day"),
        ("Breaking Anime Buzz", "https://www.animenewsnetwork.com/all/rss.xml")
    ]
    discussions = []
    for label, url in feeds:
        try:
            res = requests.get(url, headers=HEADERS, timeout=15)
            feed = feedparser.parse(res.content)
            for entry in feed.entries[:6]:
                snippet = entry.get("summary", "")[:350].replace("\n", " ")
                discussions.append(f"[{label}] {entry.title} | Link: {entry.link}\nDetails: {snippet}")
        except Exception as e:
            print(f"⚠️ RSS Fetch Warning ({label}): {e}")
    return discussions

def summarize_with_gemini(aired_list, trending_list, discussions):
    client = genai.Client(api_key=GEMINI_API_KEY)

    raw_content = (
        f"1. NEWLY AIRED ANIME EPISODES:\n" + ("\n".join(aired_list) if aired_list else "No new episodes in the last hour.") + "\n\n"
        f"2. CURRENTLY TRENDING ANIME & RATINGS:\n" + ("\n".join(trending_list) if trending_list else "None.") + "\n\n"
        f"3. EPISODE DISCUSSIONS, NEWS & FAN REACTIONS:\n" + ("\n".join(discussions) if discussions else "None.")
    )

    prompt = f"""
    You are an Anime Hype & Reaction Bot for Telegram.
    Create a detailed, high-energy report structured into these exact 4 sections:

    1. 📺 <b>Aired Anime List</b>
       - List the anime episodes that recently aired with their episode number and rating.
    2. 🔥 <b>Trending Anime & Episode Ratings</b>
       - List the top trending anime right now alongside their latest episode number, AniList rating (out of 100), and trending activity score.
    3. 🚀 <b>Why These Episodes Are Trending</b>
       - Explain specifically WHY each top trending anime/episode is blowing up right now based on the episode threads, story context, and news. Wrap specific plot spoilers in <tg-spoiler>...</tg-spoiler> tags.
    4. 💬 <b>Fan Reactions & Community Buzz</b>
       - Summarize how fans are reacting to these episodes (hype level, reception, fan reviews, and community buzz).

    Formatting Rules:
    - Use ONLY Telegram HTML tags: <b>bold</b>, <i>italic</i>, <a href="url">link</a>, and <tg-spoiler>spoiler</tg-spoiler>.
    - Do NOT use Markdown (** or #) or unsupported HTML tags like <ul>, <li>, or <br>.
    - Keep total output under 3800 characters.

    Raw Data:
    {raw_content}
    """

    # Automatically discover available Flash models on your API key & try them in order
    candidate_models = []
    try:
        for m in client.models.list():
            name = m.name.replace("models/", "")
            if "gemini" in name and ("flash" in name or "pro" in name) and "tts" not in name and "image" not in name:
                candidate_models.append(name)
    except Exception:
        pass

    # Prioritize fast, low-congestion models first
    preferred_order = [
        "gemini-3-flash-preview",
        "gemini-3.0-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.0-flash",
        "gemini-2.5-flash",
        "gemini-3.5-flash",
        "gemini-3.8-flash",
        "gemini-3.5-flash-lite",
    ]
    models_to_try = preferred_order + [m for m in candidate_models if m not in preferred_order]

    for model_name in models_to_try:
        try:
            print(f"Trying Gemini model: {model_name}...")
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
            )
            if response and response.text:
                print(f"✅ Success with {model_name}!")
                return response.text
        except Exception as e:
            print(f"⚠️ {model_name} unavailable ({e}), trying next fallback model...")
            time.sleep(2)

    # Ultimate fallback if all Gemini servers are overloaded (503) so the bot still sends your update!
    print("⚠️ All Gemini models busy; sending direct formatted fallback report.")
    fallback_msg = (
        "📺 <b>Aired Anime Episodes</b>\n" + "\n".join(aired_list[:10]) + "\n\n"
        "🔥 <b>Trending Anime & Ratings</b>\n" + "\n".join([t.split("\n")[0] for t in trending_list[:6]])
    )
    return fallback_msg[:3800]

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
        payload.pop("parse_mode")
        requests.post(url, json=payload, timeout=15)

def main():
    aired_list, trending_list, anilist_reactions = get_aired_trending_and_reactions()
    rss_discussions = get_reddit_rss_reactions()
    all_discussions = anilist_reactions + rss_discussions

    print(f"Fetched {len(aired_list)} aired episodes, {len(trending_list)} trending shows, {len(all_discussions)} reaction items.")

    if EVENT_NAME != "workflow_dispatch" and not aired_list:
        print("No new episodes aired this hour — skipping message.")
        return

    digest = summarize_with_gemini(aired_list, trending_list, all_discussions)
    send_telegram_message(digest)
    print("✅ Trending & Reaction Digest sent to Telegram!")

if __name__ == "__main__":
    main()
