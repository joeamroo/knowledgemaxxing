"""Reddit saved scraper via old.reddit.com (far easier to parse).

Reddit caps the saved listing at roughly the most recent 1000 items;
the GDPR export CSVs fill the tail and merge by dedupe key.
Optional backend: Reddit OAuth API if credentials are configured
(scraping stays the default).
"""
from __future__ import annotations

from typing import Optional

from bs4 import BeautifulSoup

from km.models import NormalizedItem
from km.scrapers.base import BaseScraper
from km.timeutil import infer_timestamp


def parse_saved_page(html: str) -> tuple[list[NormalizedItem], Optional[str]]:
    """Parse one old.reddit saved page. Returns (items, next_page_url)."""
    soup = BeautifulSoup(html, "lxml")
    items: list[NormalizedItem] = []
    for index, thing in enumerate(soup.select("div.thing")):
        fullname = thing.get("data-fullname", "")
        rid = fullname.split("_", 1)[1] if "_" in fullname else fullname
        if not rid:
            continue
        subreddit = thing.get("data-subreddit", "")
        permalink = thing.get("data-permalink", "")
        if permalink.startswith("/"):
            permalink = f"https://old.reddit.com{permalink}"
        is_comment = "comment" in (thing.get("class") or [])
        timestamp = None
        time_el = thing.select_one("time")
        if time_el and time_el.get("datetime"):
            timestamp = infer_timestamp(time_el["datetime"])

        if is_comment:
            body = thing.select_one("div.md")
            link_title = thing.select_one("a.title, p.parent a")
            items.append(
                NormalizedItem(
                    kind="saved_comment",
                    dedupe_key=f"reddit:{rid}",
                    url=permalink,
                    title=f"Comment in r/{subreddit}" if subreddit else "Reddit comment",
                    text=body.get_text(" ", strip=True)[:2000] if body else None,
                    created_at=timestamp,
                    raw={"subreddit": subreddit, "saved_order": index},
                    occurrence_detail="reddit saved (comment)",
                )
            )
        else:
            title_el = thing.select_one("a.title")
            external = thing.get("data-url", "")
            if external.startswith("/"):
                external = f"https://old.reddit.com{external}"
            items.append(
                NormalizedItem(
                    kind="saved_post",
                    dedupe_key=f"reddit:{rid}",
                    url=permalink,
                    title=title_el.get_text(strip=True) if title_el else None,
                    created_at=timestamp,
                    raw={
                        "subreddit": subreddit,
                        "external_url": external
                        if external and "reddit.com" not in external else None,
                        "saved_order": index,
                    },
                    occurrence_detail="reddit saved (post)",
                )
            )
    next_el = soup.select_one("span.next-button > a")
    return items, next_el.get("href") if next_el else None


class RedditScraper(BaseScraper):
    name = "reddit_saved"
    source_kind = "reddit_saved"

    def run(self) -> int:
        from km.scrapers.session import session_valid

        username = self.cfg.usernames.reddit
        if not username:
            self.stop("set usernames.reddit in config.yaml first")
        if not session_valid(self.context, "reddit"):
            self.stop("no valid Reddit session; run km login")

        seen = {
            r["dedupe_key"]
            for r in self.conn.execute(
                "SELECT dedupe_key FROM items WHERE kind IN ('saved_post','saved_comment')"
            )
        }
        page = self.context.new_page()
        url = f"https://old.reddit.com/user/{username}/saved"
        page_num = 0
        try:
            while url:
                response = page.goto(url, wait_until="domcontentloaded")
                if response and response.status == 429:
                    self.stop("rate limited by Reddit (HTTP 429)")
                if "login" in page.url:
                    self.stop("Reddit redirected to login; run km login")
                html = page.content()
                self.save_raw(f"saved-page{page_num}.html", html)
                items, next_url = parse_saved_page(html)
                if not items and page_num == 0:
                    if "over 18" in html.lower():
                        self.stop("Reddit interstitial page; open old.reddit.com once in km login --headed")
                    return self.items_saved
                new = 0
                for item in items:
                    if item.dedupe_key in seen:
                        return self.items_saved  # incremental boundary reached
                    self.save_item(item)
                    new += 1
                if not next_url or new == 0:
                    break
                url = next_url
                page_num += 1
                self.pause(1.0, 2.0)
        finally:
            page.close()
        return self.items_saved
