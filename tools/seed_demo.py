"""Seed data/demo.db with synthetic-but-plausible archive data.

Used for public screenshots and demo videos so no real personal data
ever appears. Run:  .venv/bin/python tools/seed_demo.py
Then:               KM_DB=data/demo.db .venv/bin/python -m km.cli ui

Deterministic (seeded RNG) so re-runs produce the same demo.
"""
from __future__ import annotations

import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from km.db import get_db
from km.models import NormalizedItem
from km.store import add_source, upsert_item

rng = random.Random(1889)

TWEETS = [
    ("aphorism", "The market can stay irrational longer than you can stay solvent."),
    ("aphorism", "Slow is smooth, smooth is fast."),
    ("aphorism", "You do not rise to the level of your goals. You fall to the level of your systems."),
    ("natural_law", "Goodhart's law: when a measure becomes a target, it ceases to be a good measure."),
    ("natural_law", "Hofstadter's law: it always takes longer than you expect, even when you take into account Hofstadter's law."),
    ("natural_law", "Chesterton's fence: never take down a fence until you know why it was put up."),
    ("contrarian", "Unpopular opinion: most productivity advice is procrastination with better branding."),
    ("contrarian", "Reading fewer, older books beats reading more, newer ones. The Lindy filter is free."),
    ("interesting_fact", "The Great Emu War of 1932: Australia deployed machine guns against emus. The emus won."),
    ("interesting_fact", "Oxford University is older than the Aztec Empire by about 250 years."),
    ("tool_or_resource", "If you write anything at all, get a copy of Zinsser's On Writing Well. It pays for itself in the first chapter."),
    ("tool_or_resource", "Anki + one deck per domain + 10 minutes a day. That's the whole secret."),
    ("hot_take", "Note-taking apps are where ideas go to feel organized while dying."),
    ("hot_take", "Your bookmarks folder is a graveyard with a search bar."),
    ("joke", "My code doesn't work, I have no idea why. My code works, I have no idea why."),
    ("joke", "There are two hard things in computer science: cache invalidation, naming things, and off-by-one errors."),
    ("quote", "\"How we spend our days is, of course, how we spend our lives.\" - Annie Dillard"),
    ("quote", "\"The impediment to action advances action. What stands in the way becomes the way.\" - Marcus Aurelius"),
    ("thread", "1/ A thread on spaced repetition and why your brain forgets on purpose. The forgetting curve is not a bug."),
    ("thread", "1/ I read every Paul Graham essay this year. Here are the 12 ideas that actually changed how I work."),
    ("anecdote", "A professor once told me: the library has two kinds of students, the ones reading and the ones organizing their notes. Only one group graduates."),
    ("personal", "Finally shipped the side project I have been talking about for two years. Turns out the last 10% is 90% of the work."),
]

ESSAYS = [
    ("How to Do Great Work", "paulgraham.com", "https://paulgraham.com/greatwork.html"),
    ("Spaced Repetition for Efficient Learning", "gwern.net", "https://gwern.net/spaced-repetition"),
    ("Meditations on Moloch", "slatestarcodex.com", "https://slatestarcodex.com/2014/07/30/meditations-on-moloch/"),
    ("The Days Are Long But the Decades Are Short", "blog.samaltman.com", "https://blog.samaltman.com/the-days-are-long-but-the-decades-are-short"),
    ("You and Your Research", "cs.virginia.edu", "https://www.cs.virginia.edu/~robins/YouAndYourResearch.html"),
    ("Speed Matters", "jsomers.net", "https://jsomers.net/blog/speed-matters"),
    ("In Praise of Idleness", "harpers.org", "https://harpers.org/archive/1932/10/in-praise-of-idleness/"),
    ("The Tyranny of the Marginal User", "nothinghuman.substack.com", "https://nothinghuman.substack.com/p/the-tyranny-of-the-marginal-user"),
]

NOTES = [
    "Book ideas running list",
    "Quotes worth keeping",
    "Project: home server setup",
    "Recipes that actually worked",
    "Gym log ideas",
    "Gift ideas for family",
    "Things to learn in 2026",
    "Interview prep notes",
    "Trip planning: national parks",
    "Reading queue, prioritized",
]

CHATS = [
    "Explaining spaced repetition algorithms",
    "SQLite FTS5 ranking options",
    "Plan a 12-week strength program",
    "Debugging a Playwright scraper",
    "Compare static site generators",
    "Draft a cold email to a mentor",
    "Summarize Meditations by Marcus Aurelius",
    "Python asyncio pitfalls",
]

SEARCHES = [
    "how does spaced repetition work", "best paul graham essays",
    "sqlite full text search tutorial", "why do we forget things",
    "goodhart's law examples", "emu war", "anki vs supermemo",
    "how to read more books", "zettelkasten method explained",
    "what is the lindy effect", "marcus aurelius meditations best translation",
    "python typer cli tutorial", "local first software", "bge embeddings",
    "how to make sourdough starter", "national parks road trip route",
    "chesterton's fence meaning", "hofstadter's law", "best essays of the decade",
    "annie dillard how we spend our days", "deliberate practice vs flow",
    "why are old books better", "commonplace book examples", "sqlite vs postgres for local apps",
    "reading retention techniques", "hamming you and your research summary",
    "compound interest of reading", "digital garden vs blog", "note taking second brain",
    "how long to form a habit", "essay about doing hard things", "attention residue",
    "best substack essays", "rss is not dead", "why keep a journal",
    "memory palace technique", "slow productivity", "what makes writing good",
    "archive your own data", "personal knowledge management overkill",
]

DOMAINS = [
    "news.ycombinator.com", "en.wikipedia.org", "github.com", "gwern.net",
    "paulgraham.com", "lesswrong.com", "substack.com", "reddit.com",
    "marginalrevolution.com", "arxiv.org", "stackoverflow.com",
]

PAGE_TITLES = {
    "news.ycombinator.com": [
        "Show HN: I built a local-first knowledge base",
        "Ask HN: What did you learn the hard way?",
        "SQLite is all you need", "The forgotten history of hypertext",
    ],
    "en.wikipedia.org": [
        "Zettelkasten - Wikipedia", "Ebbinghaus forgetting curve - Wikipedia",
        "Great Emu War - Wikipedia", "Lindy effect - Wikipedia",
    ],
    "github.com": [
        "sqlite/sqlite", "karpathy/nanoGPT", "obsidianmd/obsidian-releases",
        "asg017/sqlite-vec",
    ],
    "gwern.net": [
        "Spaced Repetition for Efficient Learning", "The Melancholy of Subculture Society",
        "Why Correlation Usually Does Not Imply Causation",
    ],
    "paulgraham.com": ["How to Do Great Work", "Maker's Schedule", "Life is Short"],
    "lesswrong.com": [
        "Being the (Pareto) Best in the World", "Notes on notetaking",
        "The Costly Coordination Mechanism of Common Knowledge",
    ],
    "substack.com": [
        "The Tyranny of the Marginal User", "In Praise of the Long Essay",
        "What I Read This Month",
    ],
    "reddit.com": [
        "r/selfhosted: My home server journey", "r/books: Books that rewired your brain",
        "r/GetDisciplined: The two-day rule",
    ],
    "marginalrevolution.com": [
        "Assorted links", "The economics of used bookstores", "What I've been reading",
    ],
    "arxiv.org": [
        "Attention Is All You Need", "Retrieval-Augmented Generation Survey",
        "Matryoshka Representation Learning",
    ],
    "stackoverflow.com": [
        "How do FTS5 external content tables work?", "SQLite WAL mode gotchas",
        "Python zoneinfo vs pytz",
    ],
}


def _page_title(domain: str) -> str:
    return rng.choice(PAGE_TITLES[domain])


def _stamp(year_lo=2019, year_hi=2026) -> datetime:
    year = rng.randint(year_lo, year_hi)
    month = rng.randint(1, 12 if year < 2026 else 7)
    # evening-heavy hours so the rhythm chart looks alive
    hour = rng.choice([9, 11, 13, 14, 16, 18, 19, 20, 21, 21, 22, 22, 23, 23, 0, 1])
    return datetime(year, month, rng.randint(1, 28), hour, rng.randint(0, 59),
                    rng.randint(1, 59), tzinfo=timezone.utc)


def main() -> None:
    db_path = Path(__file__).resolve().parent.parent / "data" / "demo.db"
    if db_path.exists():
        db_path.unlink()
    conn = get_db(db_path)
    sid, _ = add_source(conn, "demo", "demo-seed", "demo")

    for i, (category, text) in enumerate(TWEETS):
        item_id = upsert_item(conn, NormalizedItem(
            kind="bookmark_tweet", dedupe_key=f"tweet:{1000 + i}", text=text,
            url=f"https://x.com/someone/status/{1000 + i}",
            author="someone", created_at=_stamp(2025)), sid)
        conn.execute(
            """INSERT OR REPLACE INTO classifications
               (item_id, category, confidence, model, prompt_version, classified_at)
               VALUES (?, ?, 0.92, 'demo', 'tweet_categories:v2', '2026-07-01')""",
            (item_id, category))

    for title, domain, url in ESSAYS:
        item_id = upsert_item(conn, NormalizedItem(
            kind="bookmark", dedupe_key=f"url:{url}", title=title, url=url,
            created_at=_stamp(2024)), sid)
        conn.execute(
            "UPDATE items SET is_essay=1, domain=?, interest_score=? WHERE id=?",
            (domain, round(rng.uniform(0.5, 0.95), 2), item_id))

    for title in NOTES:
        upsert_item(conn, NormalizedItem(
            kind="note", dedupe_key=f"apple-note:demo-{title[:12]}", title=title,
            text=f"{title}. A few lines of notes live here.",
            created_at=_stamp(2024)), sid)

    for title in CHATS:
        upsert_item(conn, NormalizedItem(
            kind="chat_conversation", dedupe_key=f"chat:demo:{title[:16]}",
            title=title, text=f"Conversation about {title.lower()}.",
            created_at=_stamp(2024)), sid)

    for i in range(400):
        query = rng.choice(SEARCHES)
        # same dedupe scheme as real ingestion: identical queries merge into
        # one item, every occurrence preserved
        upsert_item(conn, NormalizedItem(
            kind="search_query", dedupe_key=f"search:{query}", text=query,
            created_at=_stamp()), sid)

    for i in range(1200):
        domain = rng.choice(DOMAINS)
        url = f"https://{domain}/page-{i}"
        item_id = upsert_item(conn, NormalizedItem(
            kind="visit", dedupe_key=f"url:{url}", url=url,
            title=_page_title(domain), created_at=_stamp()), sid)
        conn.execute("UPDATE items SET domain=? WHERE id=?", (domain, item_id))

    # a recent, dense stretch so the last-year heatmap and streaks look real
    day = datetime(2025, 8, 1, tzinfo=timezone.utc)
    i = 0
    while day < datetime(2026, 7, 28, tzinfo=timezone.utc):
        for _ in range(rng.randint(1, 6)):
            domain = rng.choice(DOMAINS)
            url = f"https://{domain}/recent-{i}"
            hour = rng.choice([9, 12, 15, 18, 20, 21, 22, 23])
            item_id = upsert_item(conn, NormalizedItem(
                kind="visit", dedupe_key=f"url:{url}", url=url,
                title=_page_title(domain),
                created_at=day.replace(hour=hour, minute=rng.randint(0, 59),
                                       second=rng.randint(1, 59))), sid)
            conn.execute("UPDATE items SET domain=? WHERE id=?", (domain, item_id))
            i += 1
        day += timedelta(days=1)

    conn.commit()
    total = conn.execute("SELECT count(*) FROM items").fetchone()[0]
    print(f"demo.db seeded with {total:,} items at {db_path}")


if __name__ == "__main__":
    main()
