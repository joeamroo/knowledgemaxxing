"""The v1 tweet classification pass."""
from __future__ import annotations

from km.classify.passes import Pass

CATEGORIES = [
    "anecdote", "interesting_fact", "thread", "joke", "link_to_essay",
    "quote", "tool_or_resource", "hot_take", "contrarian", "list",
    "aphorism", "natural_law", "personal", "other",
]

SYSTEM_PROMPT = """You classify tweets that a user liked, retweeted, or bookmarked. \
Assign each tweet exactly one primary category from this list, plus optional secondary tags \
from the same list:

- anecdote: a personal story or specific account of something that happened
- interesting_fact: a unique, hard-to-find-elsewhere fact
- thread: the start of or part of a multi-tweet thread (markers like a thread emoji, "1/", "a thread")
- joke: humor, shitposts, memes
- link_to_essay: the tweet mainly exists to share a link to an essay, blog post, or article
- quote: quoting someone else's words (books, speeches, famous people)
- tool_or_resource: shares a tool, product, dataset, course, or practical resource
- hot_take: a spicy opinion for its own sake, provocative but not substantive advice
- contrarian: substantive advice or a story that inverts received wisdom: "everyone says X but \
actually Y", controversial or contrarian advice, takes that go against conventional wisdom. \
Distinguish from hot_take: contrarian is substantive advice or an instructive story, hot_take is \
spice for its own sake
- list: the tweet is itself a list (books, rules, resources, principles)
- aphorism: a compact, self-contained piece of wisdom or maxim, quotable on its own \
("the obstacle is the way", "you get what you tolerate"). Distinguish from quote: an \
aphorism stands alone as distilled wisdom regardless of who said it; use quote when the \
point is attributing someone's words
- natural_law: names or states an eponymous law, effect, razor, or principle that \
describes how systems or people reliably behave: Goodhart's law, Chesterton's fence, \
Gell-Mann amnesia, Hanlon's razor, Lindy effect, Moloch, principal-agent problems, \
regression to the mean. Includes tweets explaining or illustrating such a law even \
without naming it formally
- personal: about the user's own life or relationships with no broader lesson
- other: none of the above fit

Respond with ONLY strict JSON as instructed. Never add commentary."""

TWEET_PASS = Pass(
    name="tweet_categories",
    version="v2",  # v2 adds aphorism + natural_law
    categories=CATEGORIES,
    system_prompt=SYSTEM_PROMPT,
    select_sql="""
        SELECT id, text FROM items
        WHERE kind IN ('like', 'retweet', 'bookmark_tweet')
        AND text IS NOT NULL AND text != ''
    """,
)
