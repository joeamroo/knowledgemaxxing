"""Seed data/demo.db with synthetic-but-plausible archive data.

Used for public screenshots and demo videos so no real personal data
ever appears. Run:  .venv/bin/python tools/seed_demo.py
Then:               KM_DB=data/demo.db .venv/bin/python -m km.cli ui

Deterministic (seeded RNG) so re-runs produce the same demo.
"""
from __future__ import annotations

import json
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

# Long-form pieces WITH body text, so the demo can show passage-level search
# (a hit on the paragraph, not the page). Invented essays on Youssef's own
# domain: the real-essay bookmarks above stay title-only, because attaching
# fabricated prose to a real author's title would be misattribution.
LONGFORM = [
    (
        "The Half-Life of a Bookmark",
        "https://montroselabs.ai/notes/half-life-of-a-bookmark",
        """Every bookmark is a promise you make to a future version of yourself, and
that person does not read email from the past.

I measured mine. Of the things I saved in a given month, I opened about one in
nine, and almost all of those within the first two days. After a week the odds
collapse. After a month the link is functionally a tombstone: it marks that
something was once interesting to me, on a Tuesday, for a reason I can no
longer reconstruct.

The obvious explanation is laziness. I think the real one is that saving is a
different act from reading, and it satisfies a different itch. Reading costs
twenty minutes. Saving costs one second and produces most of the same feeling,
which is the feeling of having dealt with the thing. The bookmark is not a
plan. It is a way of closing a tab without admitting you are closing it.

What changed my behavior was not a better system. It was seeing the pile. Once
the unopened saves were counted and dated and sitting in one list, the promise
stopped being abstract. You cannot owe a vague debt to a folder. You can
absolutely owe four hundred and six specific articles, the oldest of which has
been waiting since 2019.""",
    ),
    (
        "Forgetting Is the Feature",
        "https://montroselabs.ai/notes/forgetting-is-the-feature",
        """We talk about forgetting as decay, as if memory were a warehouse with a
leaky roof. That framing has never matched the evidence.

Forgetting is selective and it is fast where speed is cheap. You lose the
parking space from last Tuesday and keep the shape of an argument you found
convincing a decade ago. A warehouse does not do that. A warehouse loses things
in proportion to how long they sat and how wet the corner was. Your memory
loses things in proportion to how little they have mattered since.

This is why review beats rereading, and why spacing beats cramming. The signal
that something should be retained is not exposure. It is retrieval under mild
difficulty. Every time you almost fail to remember something and then succeed,
you are telling the system that this item keeps coming up in the world, so it
should stay cheap to reach. Cramming sends no such signal. It sends the
opposite one: this was available a moment ago, no need to hold it.

The practical consequence is uncomfortable. If you want to keep an idea, you
have to arrange to need it again. Systems that make everything permanently
available remove exactly the pressure that made anything stick.""",
    ),
    (
        "What the Exhaust Knows",
        "https://montroselabs.ai/notes/what-the-exhaust-knows",
        """Your reading history is a more honest document than your journal, because
you were not writing it for anyone.

A journal is edited in the act of writing. You choose what was worth recording,
and the choosing is already an argument about who you are. Browser history has
no such filter. It records the 2 a.m. detour into naval history, the nine
searches for the same symptom, the essay you opened four separate times over
three years and finished on none of them. Nobody curates their own exhaust.
That is what makes it evidence.

The uncomfortable part is that it is legible. Given a few years of it, you can
recover the shape of a person's obsessions without them saying a word: when
they started caring about something, how long the interest ran, whether it
resolved or just stopped. I can point at the exact month I gave up on a
project, and I never wrote that down anywhere.

Which is the argument for keeping it on your own disk. This is the most
revealing dataset you will ever generate, and it is generated whether or not
you pay attention. The only real decision is who else gets a copy.""",
    ),
    (
        "Against the Second Brain",
        "https://montroselabs.ai/notes/against-the-second-brain",
        """The pitch for a second brain is that capturing an idea frees you from
holding it. In practice, capture is where most ideas go to be safely ignored.

I ran a linked-note system for three years, with tags and daily notes and an
index that I maintained with real discipline. It contained a great deal. I
consulted it perhaps a dozen times. The notes were not bad. The problem was
that a note is only useful if something makes you look at it again, and nothing
did. The system had a superb front door and no reason to walk back in.

What actually worked was smaller and dumber. Search that reaches the sentence I
half remember, and a feed that puts old saves back in front of me without being
asked. Neither of those requires me to have organized anything in advance. Both
of them work on the material as it arrived, which matters, because the version
of me that saved the thing had no idea what it would later be needed for.

Organization is a bet that you can predict your future questions. I keep losing
that bet. Retrieval is the hedge.""",
    ),
    (
        "Commonplace, Uncommon",
        "https://montroselabs.ai/notes/commonplace-uncommon",
        """For roughly four centuries the standard tool of a literate person was a
commonplace book: a bound notebook where you copied out passages worth keeping,
usually with no organizing principle beyond the order you met them.

Copying by hand was slow, and the slowness was doing work. You do not
transcribe a paragraph you merely agreed with. The friction forced a judgment
at the moment of reading, and the judgment is the part that lasts. Modern
capture has removed the friction completely, which is why a highlight archive
tends to read like a list of sentences that sounded good once.

I am not arguing for going back to the notebook. I am arguing that the judgment
has to happen somewhere, and if it does not happen at capture then it has to
happen at retrieval. Either you decide what matters when you meet it, or you
build something that can find the good paragraph inside ten thousand mediocre
ones later. What does not work is skipping both and calling the pile a
library.""",
    ),
    (
        "The Cost of a Search Box",
        "https://montroselabs.ai/notes/the-cost-of-a-search-box",
        """A search box promises that you do not need to remember where you put
things. It is mostly telling the truth, and the exception is expensive.

Keyword search finds documents you can name. It fails on the ordinary case,
which is remembering a passage and none of its words: the piece about a
government losing a war against birds, the essay that compared attention to a
commons. You know the shape of the idea and not one term that appears in the
text. Every year of accumulated reading makes that failure more likely, because
the corpus grows and your recall of exact phrasing does not.

Meaning-based retrieval fixes the specific failure and introduces a subtler
one. It will always return something, ranked and plausible, whether or not the
thing you want exists. Confidence is uniform across cases where it should not
be. The fix is not to pick a side. Run both, fuse the rankings, and let a
second pass reread the candidates against what you actually asked. That is
slower and it costs nothing but patience, and it is the difference between a
search box and an archive that answers.""",
    ),
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

    # long-form with body text, so passage search has passages to find
    longform_ids: list[int] = []
    for title, url, body in LONGFORM:
        body = body.strip()
        item_id = upsert_item(conn, NormalizedItem(
            kind="bookmark", dedupe_key=f"url:{url}", title=title, url=url,
            created_at=_stamp(2024)), sid)
        conn.execute(
            "UPDATE items SET is_essay=1, domain=?, interest_score=? WHERE id=?",
            ("montroselabs.ai", round(rng.uniform(0.7, 0.98), 2), item_id))
        conn.execute(
            """INSERT OR REPLACE INTO content(item_id, text, word_count, fetched_at, ok)
               VALUES (?, ?, ?, ?, 1)""",
            (item_id, body, len(body.split()), "2026-07-28T12:00:00+00:00"))
        longform_ids.append(item_id)

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

    # saved searches, so the sidebar shows what collections actually look like
    for name, spec in [
        ("Memory and recall", {"query": "forgetting spaced repetition memory", "mode": "hybrid"}),
        ("Essays, unread", {"filters": {"is_essay": True, "in_reading_list": True}}),
        ("Laws and aphorisms", {"filters": {"category": "natural_law"}}),
        ("From gwern.net", {"filters": {"domain": "gwern.net"}}),
    ]:
        conn.execute(
            "INSERT INTO smart_collections(name, spec, created_at) VALUES (?,?,?)",
            (name, json.dumps(spec), "2026-07-28T12:00:00+00:00"))

    # a few stars and one margin note, so those columns are not uniformly empty
    for item_id in longform_ids[:3]:
        conn.execute(
            """INSERT INTO user_edits(item_id, starred, updated_at) VALUES (?,1,?)
               ON CONFLICT(item_id) DO UPDATE SET starred=1""",
            (item_id, "2026-07-29T09:00:00+00:00"))
    if longform_ids:
        conn.execute(
            """INSERT INTO user_edits(item_id, note, updated_at) VALUES (?,?,?)
               ON CONFLICT(item_id) DO UPDATE SET note=excluded.note""",
            (longform_ids[0], "The four-hundred-and-six number is the one that stuck.",
             "2026-07-29T09:02:00+00:00"))

    conn.commit()
    total = conn.execute("SELECT count(*) FROM items").fetchone()[0]
    articles = conn.execute("SELECT count(*) FROM content").fetchone()[0]
    print(f"demo.db seeded with {total:,} items ({articles} with article text) at {db_path}")
    print("next: KM_DB=data/demo.db .venv/bin/python -m km.cli embed")


if __name__ == "__main__":
    main()
