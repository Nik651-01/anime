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
    "User-Agent": "linux:anime.mystery.bot:v6.0 (by /u/anime_mystery_bot)",
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
    return "⚠️ Gemini AI is temporarily at max capacity. Please try again in 30 seconds!"

def fetch_anilist_media(search_title):
    url = "https://graphql.anilist.co"
    query = """
    query ($search: String) {
      Media(search: $search, type: ANIME) {
        title { english romaji }
        averageScore
        status
        genres
        description(asHtml: false)
        siteUrl
        characters(perPage: 5, sort: ROLE) {
          nodes { name { full } }
        }
      }
    }
    """
    try:
        res = requests.post(url, json={"query": query, "variables": {"search": search_title}}, timeout=15)
        return res.json().get("data", {})
    except Exception:
        return {}

def fetch_reddit_theories(query_term):
    url = f"https://www.reddit.com/r/anime/search.rss?q={query_term}+theory+mystery+lore&restrict_sr=on&sort=relevance&t=all"
    items = []
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        feed = feedparser.parse(res.content)
        for entry in feed.entries[:5]:
            snippet = entry.get("summary", "")[:300].replace("\n", " ")
            items.append(f"[Theory/Discussion] {entry.title} | {snippet}")
    except Exception:
        pass
    return items

def handle_ongoing_questions():
    url = "https://graphql.anilist.co"
    query = """
    query {
      Page(perPage: 10) {
        media(type: ANIME, status: RELEASING, sort: TRENDING_DESC) {
          title { english romaji }
          genres
          averageScore
          description(asHtml: false)
          nextAiringEpisode { episode }
        }
      }
    }
    """
    try:
        res = requests.post(url, json={"query": query}, timeout=15)
        data = res.json().get("data", {}).get("Page", {}).get("media", [])
        if not data:
            return "❌ No ongoing anime found at the moment."

        raw_shows = []
        for m in data:
            title = m["title"]["english"] or m["title"]["romaji"]
            ep = (m["nextAiringEpisode"]["episode"] - 1) if m.get("nextAiringEpisode") else "Current"
            genres = ", ".join(m.get("genres", []))
            synopsis = (m.get("description") or "")[:200].replace("\n", " ")
            raw_shows.append(f"- Anime: {title} (Latest Ep: {ep}, Genres: {genres})\n  Plot Hook: {synopsis}")

        prompt = f"""
        You are an Anime Mystery & Trivia Master for a Telegram channel. 
        Based on the following currently ONGOING anime series, generate a fun, engaging series of questions or hidden mystery prompts for the channel members to solve.
        
        Rules:
        - Use ONLY Telegram HTML tags (<b>, <i>, <tg-spoiler>). Do NOT use Markdown (** or #).
        - For each of the top ongoing shows provided below, create:
          1. 📺 <b>[Anime Title] (Ep X)</b>
          2. ❓ <b>The Hidden Question / Lore Riddle:</b> (A compelling question about what mystery, plot thread, or conflict characters are facing in the latest episodes).
          3. 🔒 <b>Answer / Clue:</b> (Wrap the answer or spoiler in <tg-spoiler>...</tg-spoiler> tags so channel members can tap to reveal).

        Current Ongoing Shows Data:
        {chr(10).join(raw_shows)}
        """
        return ask_gemini(prompt)
    except Exception as e:
        return f"⚠️ Error generating ongoing questions: {e}"

def handle_mysteries(query):
    if not query:
        return "❓ Please specify an anime name! Example: <code>/mysteries Attack on Titan</code>"
    data = fetch_anilist_media(query)
    media = data.get("Media")
    if not media:
        return f"❓ Could not find an anime matching '<b>{query}</b>'."
    
    title = media["title"]["english"] or media["title"]["romaji"]
    discussions = fetch_reddit_theories(title)

    prompt = f"""
    You are an expert Anime Mystery Sleuth & Lore Detective. Analyze <b>{title}</b> and present its biggest hidden questions.
    Use ONLY Telegram HTML tags (<b>, <i>, <tg-spoiler>). Do NOT use Markdown (** or #).

    Structure your report into:
    1. 🔍 <b>Unsolved Mysteries & Hidden Questions</b> (Highlight the major plot holes, secret identities, or open-ended questions remaining in the series).
    2. 🕵️‍♂️ <b>Subtle Clues & In-Universe Hints</b> (What hints has the author dropped so far?)
    3. 🧠 <b>Top Fan Theories</b> (Summarize active community theories addressing these mysteries. Wrap major plot spoilers inside <tg-spoiler>...</tg-spoiler> tags).

    Anime Data:
    Title: {title}
    Synopsis: {media.get('description')}
    Community Discussions: {chr(10).join(discussions) if discussions else 'None found.'}
    """
    return ask_gemini(prompt)

def handle_foreshadow(query):
    if not query:
        return "❓ Please specify an anime name! Example: <code>/foreshadow One Piece</code>"
    data = fetch_anilist_media(query)
    media = data.get("Media")
    if not media:
        return f"❓ Could not find an anime matching '<b>{query}</b>'."
    
    title = media["title"]["english"] or media["title"]["romaji"]
    prompt = f"""
    You are an Anime Foreshadowing & Easter Egg Expert. Analyze <b>{title}</b>.
    Use ONLY Telegram HTML tags (<b>, <i>, <tg-spoiler>). Do NOT use Markdown (** or #).

    Structure your report into:
    1. 👁️ <b>Hidden Foreshadowing & Visual Cues</b> (Details in openings, character designs, or background art that hinted at future twists).
    2. 🧩 <b>Early-Series Parallels</b> (Clues dropped early on that fans only understood much later).
    3. ⚠️ <b>Spoiler Warning Note</b> (Wrap major payoff spoilers inside <tg-spoiler>...</tg-spoiler> tags).

    Anime Synopsis: {media.get('description')}
    """
    return ask_gemini(prompt)

def handle_theories(query):
    if not query:
        return "❓ Please specify an anime name! Example: <code>/theories Jujutsu Kaisen</code>"
    data = fetch_anilist_media(query)
    media = data.get("Media")
    if not media:
        return f"❓ Could not find an anime matching '<b>{query}</b>'."
    
    title = media["title"]["english"] or media["title"]["romaji"]
    discussions = fetch_reddit_theories(title)

    prompt = f"""
    You are an Anime Theory Crafter. Analyze <b>{title}</b> and the community buzz.
    Use ONLY Telegram HTML tags (<b>, <i>, <tg-spoiler>). Do NOT use Markdown (** or #).

    Structure your report into:
    1. 💡 <b>Most Popular Fan Theories</b> (What are fans currently arguing or theorizing about this show?)
    2. 📉 <b>Plausibility Breakdown</b> (Which theories have strong evidence and which are stretches?)
    3. 🔒 <b>Spoiler Status</b> (Wrap specific spoilers in <tg-spoiler>...</tg-spoiler> tags).

    Data:
    Title: {title}
    Community Threads: {chr(10).join(discussions) if discussions else 'Standard popular theories apply.'}
    """
    return ask_gemini(prompt)

def handle_lore(query):
    if not query:
        return "❓ Please specify an anime name! Example: <code>/lore Hunter x Hunter</code>"
    data = fetch_anilist_media(query)
    media = data.get("Media")
    if not media:
        return f"❓ Could not find an anime matching '<b>{query}</b>'."
    
    title = media["title"]["english"] or media["title"]["romaji"]
    prompt = f"""
    You are an Anime Lore Historian. Break down the deep world-building and history of <b>{title}</b>.
    Use ONLY Telegram HTML tags (<b>, <i>). Do NOT use Markdown (** or #).

    Structure your report into:
    1. 📜 <b>World History & Origins</b> (The backstory or ancient lore of the universe).
    2. 🏛️ <b>Factions, Kingdoms, or Systems</b> (Power structures, magic systems, or societal rules).
    3. 🌐 <b>Unexplored Regions & Deep Lore Secrets</b> (Areas of the world still shrouded in mystery).

    Synopsis: {media.get('description')}
    """
    return ask_gemini(prompt)

def handle_summary(query):
    if not query:
        return "❓ Please specify an anime name! Example: <code>/summary Steins;Gate</code>"
    data = fetch_anilist_media(query)
    media = data.get("Media")
    if not media:
        return f"❓ Could not find an anime matching '<b>{query}</b>'."
    
    title = media["title"]["english"] or media["title"]["romaji"]
    status_text = media.get('status', 'UNKNOWN')
    status_map = {'RELEASING': '🟢 Ongoing', 'NOT_YET_RELEASED': '⏳ Upcoming', 'FINISHED': '🔴 Completed'}

    prompt = f"""
    Write an engaging breakdown for <b>{title}</b> using ONLY HTML tags (<b>, <i>, <tg-spoiler>). Do NOT use Markdown (** or #):
    1. 📖 <b>Plot Overview</b> (Clean synopsis)
    2. 📊 <b>Status</b> ({status_map.get(status_text, status_text)})
    3. ❓ <b>Key Remaining Questions & Mysteries</b> (What major questions does this show leave open or unresolved for fans to ponder?)
    4. ⭐ <b>Rating & Verdict</b> ({media.get('averageScore')}/100)

    Data: Synopsis: {media.get('description')}
    """
    return ask_gemini(prompt)

def process_command(text, chat_id):
    parts = text.strip().split(maxsplit=1)
    cmd = parts[0].lower().split("@")[0]
    arg = parts[1] if len(parts) > 1 else ""

    try:
        if cmd == "/ongoing":
            send_telegram_message("⏳ <i>Generating mystery questions for all ongoing anime...</i>", chat_id)
            send_telegram_message(handle_ongoing_questions(), chat_id)
        elif cmd == "/mysteries":
            send_telegram_message(f"🔍 <i>Hunting down hidden mysteries & questions for '{arg}'...</i>", chat_id)
            send_telegram_message(handle_mysteries(arg), chat_id)
        elif cmd == "/foreshadow":
            send_telegram_message(f"👁️ <i>Scanning for subtle foreshadowing & clues in '{arg}'...</i>", chat_id)
            send_telegram_message(handle_foreshadow(arg), chat_id)
        elif cmd == "/theories":
            send_telegram_message(f"💡 <i>Gathering community fan theories for '{arg}'...</i>", chat_id)
            send_telegram_message(handle_theories(arg), chat_id)
        elif cmd == "/lore":
            send_telegram_message(f"📜 <i>Unrolling the historical lore of '{arg}'...</i>", chat_id)
            send_telegram_message(handle_lore(arg), chat_id)
        elif cmd == "/summary":
            send_telegram_message(f"⏳ <i>Analyzing plot & lingering questions for '{arg}'...</i>", chat_id)
            send_telegram_message(handle_summary(arg), chat_id)
        elif cmd in ["/start", "/help"]:
            help_msg = (
                "🕵️‍♂️ <b>Anime Mystery & Lore Sleuth Bot Ready!</b>\n\n"
                "Uncover hidden questions and secrets with these commands:\n"
                "• /ongoing — Generate mystery questions & riddles for current ongoing anime\n"
                "• /mysteries <i>Anime Name</i> — Find unsolved questions & plot threads\n"
                "• /foreshadow <i>Anime Name</i> — Spot hidden clues & Easter eggs\n"
                "• /theories <i>Anime Name</i> — Read top community fan theories\n"
                "• /lore <i>Anime Name</i> — Dive into world history & hidden secrets\n"
                "• /summary <i>Anime Name</i> — Overview with key remaining mysteries"
            )
            send_telegram_message(help_msg, chat_id)
    except Exception as e:
        print(f"❌ Error: {e}")
        send_telegram_message(f"⚠️ Sorry, an error occurred while processing your <code>{cmd}</code> request.", chat_id)

def main():
    command_text = os.environ.get("BOT_COMMAND", "").strip() or "/help"
    print(f"⚡ Executing command: {command_text}")
    process_command(command_text, TELEGRAM_CHAT_ID)
    print("✅ Finished and shutting down!")

if __name__ == "__main__":
    main()
