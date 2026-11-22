"""Content Planner Agent: shared agent code used by app.py (Streamlit) and main.py (CLI).

Secrets are read from environment variables (never hard-code them):
    GROQ_API_KEY    - Groq API key
    CALENDAR_URL    - Google Apps Script web app URL (ends with /exec)
    CALENDAR_TOKEN  - the SECRET value from apps_script.gs
Optional:
    DATA_DIR        - folder for brand.json and posts.json (default: ./data)
    GROQ_MODEL      - Groq model id (default: openai/gpt-oss-120b)
"""

import json
import os
from datetime import date, datetime, timedelta
from typing import Annotated

import requests
from typing_extensions import TypedDict
from langchain_core.messages import SystemMessage
from langchain_core.tools import tool
from langchain_groq import ChatGroq
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.types import interrupt

# ---------- Storage (brand and posts survive between sessions) ----------

DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
os.makedirs(DATA_DIR, exist_ok=True)
BRAND_FILE = os.path.join(DATA_DIR, "brand.json")
POSTS_FILE = os.path.join(DATA_DIR, "posts.json")


def read_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------- Tools: brand ----------

@tool
def save_brand(tone: str, audience: str, values: str, colors: str, fonts: str, forbidden: str) -> str:
    """Save the brand profile extracted from the brand book and brand platform.
    Use when the user shares brand book or brand platform text.
    tone: voice and tone; audience: target audience; values: brand values;
    colors: brand colors (names or HEX); fonts: brand fonts;
    forbidden: what the brand must never say or do."""
    brand = {"tone": tone, "audience": audience, "values": values,
             "colors": colors, "fonts": fonts, "forbidden": forbidden}
    write_json(BRAND_FILE, brand)
    return "Brand saved."


@tool
def get_brand() -> dict:
    """Read the saved brand profile. Always call this before writing a post idea."""
    brand = read_json(BRAND_FILE, None)
    if brand is None:
        return {"error": "No brand saved yet. Ask the user for the brand book."}
    return brand


# ---------- Tools: external APIs ----------

@tool
def get_upcoming_holidays(days: int = 30) -> list:
    """Get public holidays in Georgia (the country) within the next `days` days (Nager.Date API).
    Returns a list of {date, name, local_name, days_left}. An empty list means no holidays in that period."""
    today = date.today()
    end = today + timedelta(days=days)
    result = []
    for year in sorted({today.year, end.year}):
        try:
            r = requests.get(f"https://date.nager.at/api/v3/PublicHolidays/{year}/GE", timeout=20)
        except requests.RequestException as e:
            return [{"error": f"Nager.Date is not reachable: {e}"}]
        if r.status_code != 200 or not r.text.strip():
            return [{"error": f"Nager.Date returned status {r.status_code} for Georgia ({year})"}]
        for h in r.json():
            d = date.fromisoformat(h["date"])
            if today <= d <= end:
                result.append({"date": h["date"], "name": h["name"],
                               "local_name": h["localName"], "days_left": (d - today).days})
    return result


WIKI_HEADERS = {"User-Agent": "ContentPlannerAgent/0.1 (BTU student project)"}

# An event is kept only if it has a positive word AND no negative word,
# so wars, disasters, scandals and politics do not reach a brand post
POSITIVE_WORDS = ("first", "launch", "invent", "discover", "found", "open", "premiere",
                  "record", "award", "prize", "publish", "release", "complete")
NEGATIVE_WORDS = ("kill", "dead", "death", "dies", "die ", "war", "attack", "bomb", "terror",
                  "earthquake", "fire", "massacre", "murder", "crash", "shoot", "invade",
                  "invasion", "riot", "disaster", "execut", "assassin", "genocide", "explosion",
                  "scandal", "offensive", "apolog", "protest", "coup", "impeach", "trial",
                  "arrest", "sink", "crisis", "president", "elect", "government", "revolution")


@tool
def get_on_this_day(month: int, day: int) -> list:
    """Get up to 5 positive or neutral historical events for this month and day (Wikipedia 'On this day').
    Each item is {"fact": "YEAR: text"}; copy the year exactly as given.
    Wars, disasters, crimes, scandals and politics are filtered out.
    month: 1-12, day: 1-31. Call only for holiday dates or dates the user chose."""
    urls = [
        f"https://en.wikipedia.org/api/rest_v1/feed/onthisday/events/{month:02d}/{day:02d}",
        f"https://api.wikimedia.org/feed/v1/wikipedia/en/onthisday/events/{month:02d}/{day:02d}",
    ]
    for url in urls:
        try:
            r = requests.get(url, headers=WIKI_HEADERS, timeout=20)
        except requests.RequestException:
            continue
        if r.status_code == 200:
            events = r.json().get("events", [])
            safe = []
            for e in events:
                text = (e.get("text") or "").lower()
                if any(p in text for p in POSITIVE_WORDS) and not any(n in text for n in NEGATIVE_WORDS):
                    safe.append({"fact": f'{e.get("year")}: {e.get("text")}'})
            if not safe:
                return [{"info": "No suitable (positive or neutral) events for this date."}]
            return safe[:5]
    return [{"error": "Wikipedia 'On this day' is not reachable"}]


# ---------- Tools: posts ----------

@tool
def save_post(idea: str, caption: str, hashtags: str, visual_brief: str,
              material_links: str, publish_date: str, publish_time: str) -> str:
    """Save a planned post to the content plan.
    publish_date format YYYY-MM-DD, publish_time format HH:MM (24h).
    material_links: links to the final visuals (e.g. Google Drive).
    Call only when the user gave the final caption, material link, date and time."""
    try:
        datetime.strptime(f"{publish_date} {publish_time}", "%Y-%m-%d %H:%M")
    except ValueError:
        return "Not saved: publish_date must be YYYY-MM-DD and publish_time must be HH:MM."
    posts = read_json(POSTS_FILE, [])
    post_id = max([p["id"] for p in posts], default=0) + 1
    posts.append({"id": post_id, "idea": idea, "caption": caption, "hashtags": hashtags,
                  "visual_brief": visual_brief, "material_links": material_links,
                  "publish_date": publish_date, "publish_time": publish_time})
    write_json(POSTS_FILE, posts)
    return f"Post #{post_id} saved for {publish_date} {publish_time}."


@tool
def list_posts() -> list:
    """Show all planned posts (the content plan), sorted by date. Use it to find a post number."""
    posts = read_json(POSTS_FILE, [])
    return sorted(posts, key=lambda p: (p["publish_date"], p["publish_time"]))


# ---------- Tools with approval: Google Calendar ----------

def calendar_call(payload: dict) -> dict:
    """Send a request to the Google Apps Script web app that creates and deletes Calendar events."""
    url = os.environ.get("CALENDAR_URL")
    token = os.environ.get("CALENDAR_TOKEN")
    if not url or not token:
        return {"error": "CALENDAR_URL or CALENDAR_TOKEN is not set"}
    try:
        r = requests.post(url, json={**payload, "token": token}, timeout=30)
        return r.json()
    except (requests.RequestException, ValueError) as e:
        return {"error": f"Calendar web app did not answer correctly: {e}"}


@tool
def create_calendar_reminder(post_id: int) -> str:
    """Create a Google Calendar event for a saved post, with caption, hashtags,
    material links and visual brief in the description. Asks the user for approval first."""
    posts = read_json(POSTS_FILE, [])
    post = next((p for p in posts if p["id"] == post_id), None)
    if post is None:
        return f"Post #{post_id} not found."
    approved = interrupt(
        f'Google Calendar-ში ვქმნი შეხსენებას: პოსტი #{post_id}, '
        f'{post["publish_date"]} {post["publish_time"]}. ვადასტურებ?')
    if not approved:
        return "User denied. Reminder was NOT created. The post stays saved."
    start = datetime.strptime(f'{post["publish_date"]} {post["publish_time"]}', "%Y-%m-%d %H:%M")
    end = start + timedelta(minutes=30)
    result = calendar_call({
        "action": "create",
        "title": f'Publish post #{post_id}: {post["idea"][:60]}',
        "start": start.strftime("%Y-%m-%dT%H:%M:00+04:00"),
        "end": end.strftime("%Y-%m-%dT%H:%M:00+04:00"),
        "description": (f'Caption:\n{post["caption"]}\n\nHashtags: {post["hashtags"]}\n\n'
                        f'Materials: {post["material_links"]}\n\nVisual brief: {post["visual_brief"]}'),
    })
    if not result.get("ok"):
        return f"Calendar error: {result}"
    post["event_id"] = result["id"]
    write_json(POSTS_FILE, posts)
    return f'Event created in Google Calendar on {post["publish_date"]} {post["publish_time"]}.'


@tool
def delete_post(post_id: int) -> str:
    """Delete a planned post from the content plan AND its Google Calendar event, if one was created.
    Asks the user for approval first."""
    posts = read_json(POSTS_FILE, [])
    post = next((p for p in posts if p["id"] == post_id), None)
    if post is None:
        return f"Post #{post_id} not found."
    approved = interrupt(
        f'ვშლი პოსტს #{post_id}: „{post["idea"][:60]}“, '
        f'{post["publish_date"]} {post["publish_time"]}'
        f'{" და მის მოვლენას Google Calendar-ში" if post.get("event_id") else ""}. ვადასტურებ?')
    if not approved:
        return f"User denied. Post #{post_id} was NOT deleted. Nothing changed."
    calendar_msg = "No Google Calendar event was linked to this post."
    if post.get("event_id"):
        result = calendar_call({"action": "delete", "id": post["event_id"]})
        calendar_msg = ("Google Calendar event deleted." if result.get("ok")
                        else f"Google Calendar event NOT deleted: {result}")
    write_json(POSTS_FILE, [p for p in posts if p["id"] != post_id])
    return f"Post #{post_id} deleted from the content plan. {calendar_msg}"


TOOLS = [save_brand, get_brand, get_upcoming_holidays, get_on_this_day,
         save_post, list_posts, create_calendar_reminder, delete_post]

# ---------- Agent ----------

SYSTEM_PROMPT = """You are Content Planner Agent. You help a brand plan social media posts.
Always reply in the user's language (usually Georgian). Write correct literary Georgian.
Use only Georgian letters in Georgian sentences: never insert Armenian, Cyrillic or other similar-looking letters,
and never insert words from other languages (brand names, HEX codes, fonts and hashtags are allowed).

When the user shares a brand book or brand platform, extract the fields and call save_brand.

When the user asks for a post idea:
1. ALWAYS call get_brand first. You do not know the brand until you call it.
   Never say that the brand is missing unless get_brand returned an error.
2. Call get_upcoming_holidays with days=30.
3. If holidays are coming, you may call get_on_this_day for those holiday dates
   and use only the events it returns.
4. Give a short review of the upcoming dates and ASK whether to connect the idea to one of them.
   Do not write the idea before the user answers.
   When the user has chosen a date or said yes, do NOT ask any more questions:
   write the full idea (idea, caption, hashtags, visual brief) in the same reply.
5. If no holiday is coming, skip the question and build the idea on the brand only.
6. The idea must include: the idea, a caption draft, hashtags, and a visual brief
   (format: post/story/Reels, composition, brand colors, text on the visual, mood).
   Follow the brand tone and never break the brand's "forbidden" rules.

Call save_post only when the user gives the final caption, material link, date and time.
To find a post number, use the conversation or call list_posts.

Approval: create_calendar_reminder and delete_post ask the user for approval by themselves.
When the user asks to create a reminder or delete a post, call the tool directly; do not ask for confirmation in text first.
If the tool says the user denied, tell the user that nothing was changed and do not ask again.

Facts: when you mention a historical event, copy its year exactly from get_on_this_day.
Never change a year or a date. Use only facts and dates from tool results. Never invent dates or events."""



class State(TypedDict):
    messages: Annotated[list, add_messages]


def build_graph(model: str | None = None):
    """Build and compile the agent graph with in-memory conversation memory."""
    llm = ChatGroq(
        model=model or os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b"),
        temperature=0.3,
        max_tokens=2000,
    )
    llm_with_tools = llm.bind_tools(TOOLS)

    def assistant(state: State):
        messages = [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]
        return {"messages": [llm_with_tools.invoke(messages)]}

    builder = StateGraph(State)
    builder.add_node("assistant", assistant)
    builder.add_node("tools", ToolNode(TOOLS))
    builder.add_edge(START, "assistant")
    builder.add_conditional_edges("assistant", tools_condition)
    builder.add_edge("tools", "assistant")
    return builder.compile(checkpointer=InMemorySaver())
