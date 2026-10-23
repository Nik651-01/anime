import os
import sys
import time
import requests
import feedparser
from datetime import datetime, timezone
from google import genai
from google.genai import types

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

if not all([TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, GEMINI_API_KEY]):
    print("❌ ERROR: Missing one or more GitHub Secrets (TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, GEMINI_API_KEY).")
    sys.exit(1)

EVENT_NAME = os.environ.get("GITHUB_EVENT_NAME", "")
# Look back 12 hours on manual test runs so you get rich episode discussions, or 65 mins on hourly cron
LOOKBACK_SECONDS = 43200 if EVENT_NAME == "workflow_dispatch" else 3900
NOW = int(time.time())
START_TIME = NOW - LOOKBACK_SECONDS

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AnimeReactionBot/3.0)"}

def get_aired_and_trending_anime():
    """Fetches newly aired episodes AND top trending anime with scores from AniList."""
    url = "https://graphql.anilist.co"
    query = """
    query ($start: Int, $end: Int) {
      aired: Page(perPage: 20) {
        airingSchedules(airingAt_greater: $start, airingAt_lesser: $end, sort: TIME_DESC) {
          episode
          media {
            title { english romaji }
            averageScore
            popularity
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
          nextAiringEpisode { episode }
          siteUrl
        }
      }
    }
    """
    aired_list = []
    trending_list = []
    try:
        res = requests.post(url, json={"query": query, "variables": {"start": START_TIME, "end": NOW}}, timeout=15)
        data = res.json().get("data", {})

        for item in data.get("aired", {}).get("airingSchedules", []):
            media = item["media"]
            title = media["title"]["english"] or media["title"]["romaji"]
            score = f"{media['averageScore']}/100" if media.get("averageScore") else "Unrated"
            aired_list.append(
                f"- {title} | Episode {item['episode']} | Rating: {score} | Hype/Trending Score: {media.get('trending', 0)} | Link: {media['siteUrl']}"
            )

        for media in data.get("trending", {}).get("media", []):
            title = media["title"]["english"] or media["title"]["romaji"]
            score = f"{media['averageScore']}/100" if media.get("averageScore") else "Unrated"
            latest_ep = (media["nextAiringEpisode"]["episode"] - 1) if media.get("nextAiringEpisode") else "Latest"
            trending_list.append(
                f"- {title} (Latest Ep: {latest_ep}) | Rating: {score} | Trending Velocity: {media.get('trending', 0)} | Link: {media['siteUrl']}"
            )
    except Exception as e:
        print(f"⚠️ AniList Error: {e}")

    return aired_list, trending_list

def get_episode_reactions_and_ratings():
    """Fetches hot r/anime episode discussion threads, ratings/upvotes, and top fan comments."""
    search_url = "https://www.reddit.com/r/anime/search.json?q=flair%3AEpisode&restrict_sr=on&sort=hot&t=day&limit=5"
    discussions = []
    try:
        res = requests.get(search_url, headers=HEADERS, timeout=15)
        if not res.ok:
            return discussions
        posts = res.json().get("data", {}).get("children", [])
        for post in posts:
            pdata = post.get("data", {})
            title = pdata.get("title", "")
            upvotes = pdata.get("score", 0)
            upvote_ratio = int(pdata.get("upvote_ratio", 0) * 100)
            num_comments = pdata.get("num_comments", 0)
            permalink = f"https://www.reddit.com{pdata.get('permalink', '')}"
            selftext = pdata.get("selftext", "")[:400]

            # Fetch top 4 fan comments inside this episode thread to see WHY it's trending & fan reactions
            comments_url = f"https://www.reddit.com{pdata.get('permalink', '')}.json?sort=top&limit=6"
            top_comments = []
            try:
                c_res = requests.get(comments_url, headers=HEADERS, timeout=10)
                if c_res.ok and len(c_res.json()) > 1:
                    c_children = c_res.json()[1].get("data", {}).get("children", [])
                    for c in c_children:
                        body = c.get("data", {}).get("body", "")
                        if body and "AutoModerator" not in str(c.get("data", {}).get("author", "")):
                            clean_body = body.replace("\n", " ")[:250]
                            top_comments.append(f'"{clean_body}"')
                        if len(top_comments) >= 4:
                            break
            except Exception:
                pass

            discussions.append(
                f"THREAD: {title}\n"
                f"Episode Community Rating: {upvotes} upvotes ({upvote_ratio}% positive) | {num_comments} comments\n"
                f"Link: {permalink}\n"
                f"Post Info: {selftext}\n"
                f"Top Fan Reactions: {' | '.join(top_comments) if top_comments else 'No comments yet.'}\n"
            )
    except Exception as e:
        print(f"⚠️ Reddit Reaction Fetch Error: {e}")
    return discussions

def summarize_with_gemini(aired_list, trending_list, discussions):
    client = genai.Client(api_key=GEMINI_API_KEY)
    raw_content = (
        f"1. NEWLY AIRED ANIME EPISODES:\n" + ("\n".join(aired_list) if aired_list else "No new episodes in the last hour.") + "\n\n"
        f"2. CURRENTLY TRENDING ANIME & RATINGS:\n" + ("\n".join(trending_list) if trending_list else "None.") + "\n\n"
        f"3. TRENDING EPISODE DISCUSSIONS, RATINGS & FAN REACTIONS:\n" + ("\n".join(discussions) if discussions else "None.")
    )

    prompt = f"""
    You are an Anime & Manga Hype & Reaction Bot for Telegram.
    Create a detailed, engaging report structured into these exact 4 sections:

    1. 📺 <b>Aired Anime List</b>
       - List the anime episodes that recently aired with their episode number and rating.
    2. 🔥 <b>Trending Anime & Episode Ratings</b>
       - List the hottest trending anime right now alongside their AniList score (out of 100) and Reddit episode rating (upvotes & % positive approval).
    3. 🚀 <b>Why These Episodes Are Trending</b>
       - For the top 2–4 hottest episodes in the data, explain specifically WHY each episode is trending right now (e.g., major fight scene, plot twist, peak animation, emotional cliffhanger). Wrap specific plot spoilers in <tg-spoiler>...</tg-spoiler> tags.
    4. 💬 <b>Fan Reactions & Community Buzz</b>
       - Summarize the vibe of the fanbase for each trending episode (hyped, shocked, emotional, debating) and include 1–2 short paraphrased or quoted fan reactions from the top comments.

    Formatting Rules:
    - Use ONLY Telegram HTML tags: <b>bold</b>, <i>italic</i>, <a href="url">link</a>, and <tg-spoiler>spoiler</tg-spoiler>.
    - Do NOT use Markdown (** or #) or unsupported HTML tags like <ul>, <li>, or <br>.
    - Keep total output under 3800 characters.

    Raw Data:
    {raw_content}
    """
    response = client.models.generate_content(
        model="gemini-3.8-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_budget=0)
        )
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
        payload.pop("parse_mode")
        requests.post(url, json=payload, timeout=15)

def main():
    aired_list, trending_list = get_aired_and_trending_anime()
    discussions = get_episode_reactions_and_ratings()

    print(f"Fetched {len(aired_list)} aired episodes, {len(trending_list)} trending shows, {len(discussions)} reaction threads.")

    # On automatic hourly runs, skip if no new episodes aired in the past hour
    if EVENT_NAME != "workflow_dispatch" and not aired_list:
        print("No new episodes aired this hour — skipping message.")
        return

    digest = summarize_with_gemini(aired_list, trending_list, discussions)
    send_telegram_message(digest)
    print("✅ Trending & Reaction Digest sent to Telegram!")

if __name__ == "__main__":
    main()
