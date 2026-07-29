# Assumptions

Decisions made while building km that the spec left open. Flag anything
you want changed.

## Data model

- Dedupe keys: tweets dedupe on tweet id (`tweet:<id>`), URL items on
  canonical URL (`url:<canonical>`), Reddit on the base36 id with the
  t3_/t1_ prefix stripped so GDPR CSVs and the scraper merge, HN on item
  id, chats on conversation id, searches on lowercased query text.
- When the same item arrives with different kinds (a tweet both liked
  and bookmarked), the item keeps the highest-intent kind
  (bookmark > like/save > visit) and every event is preserved as an
  occurrence row.
- items.created_at holds the earliest known timestamp (first seen);
  each occurrence keeps its own timestamp.
- Chrome live history emits one record per visit (urls joined with
  visits), so a page visited 3 times produces 3 occurrences on one item.
- Search queries from My Activity are stored as kind search_query and
  excluded from essay detection.

## Parsers

- account.js in the Twitter archive is metadata only; it is parsed for
  the username but produces no items.
- like.js fullText is truncated by Twitter (roughly 140 chars); the item
  keeps the truncated text and raw_json records truncated: true.
- Retweet author is the handle extracted from the leading "RT @handle:".
- ChatGPT and Claude exports both produce one chat_conversation item per
  conversation (full text, used later for embeddings) plus one
  chat_message occurrence per distinct URL mentioned, so chat links merge
  with the same URL seen elsewhere.
- Gemini activity comes through the My Activity parser: "Prompted ..."
  entries become chat_message items.
- Grok: if a provided export matches the ChatGPT or Claude schema it is
  parsed as such, otherwise it logs "unsupported yet" and is skipped.
- Flexible CSV/JSON exports: timestamp units are inferred by magnitude
  (seconds vs millis vs Unix micros vs WebKit micros). Ambiguous columns
  raise a needs-mapping error listed in skipped.log and fixable via
  column_mappings in config.yaml.

## Heuristics

- Interest score weights: x-bookmark 3.0, browser bookmark 2.5,
  like/retweet/reddit-save/hn-favorite 2.0, upvote and chat mention 1.5,
  visit and own tweet 1.0, search query 0.5. Repeat occurrences of the
  same kind add +0.2 each, capped at +1.0, so many visits never outrank
  a deliberate save.
- Essay detection marks YouTube and GitHub PRs as separate buckets
  (never is_essay, never discarded).
- Tweets themselves are never essays; links inside tweets are separate
  URL items that bucket normally.
- Thread detection: marker regex (thread emoji, "1/", "a thread"),
  self-reply chains in my own tweets, and bookmark conversation ids.

## Scrapers

- Session validity is checked by auth cookie presence and expiry
  (auth_token for X, reddit_session, substack.sid, user for HN), which
  avoids loading pages just to check login state.
- Scrapers stop at the first already-seen item (incremental), so a full
  re-fetch requires clearing scrape_state and the relevant items.
- X bookmarks stores the newest tweet id of each run as its cursor.
- Substack API response shapes vary, so the parser walks the JSON for
  anything post-shaped (canonical_url + title) rather than pinning one
  schema; raw responses are snapshotted for parser fixes.
- Raw snapshots land in data/raw/<scraper>/<timestamp>/.

## Environment

- uv manages the environment; Python 3.12 is pinned locally via uv.
- Optional dependency groups: scrape (playwright), ai (anthropic),
  embed (sentence-transformers, sqlite-vec), web (fastapi, uvicorn),
  fetch (httpx, trafilatura).
