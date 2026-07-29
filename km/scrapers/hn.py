"""Hacker News scraper: favorites (public) and upvoted (needs session).

Pagination via the "More" link; both stories and comments (&comments=t).
"""
from __future__ import annotations

from typing import Iterator, Optional

from bs4 import BeautifulSoup

from km.models import NormalizedItem
from km.scrapers.base import BaseScraper
from km.timeutil import infer_timestamp

BASE = "https://news.ycombinator.com"


def parse_listing(html: str, kind: str) -> tuple[list[NormalizedItem], Optional[str]]:
    """Parse a favorites/upvoted page. Returns (items, next_page_path)."""
    soup = BeautifulSoup(html, "lxml")
    items: list[NormalizedItem] = []

    for thing in soup.select("tr.athing"):
        item_id = thing.get("id")
        if not item_id:
            continue
        title_link = thing.select_one("span.titleline > a, td.title > a")
        if title_link:  # story row
            url = title_link.get("href", "")
            if url.startswith("item?"):
                url = f"{BASE}/{url}"
            points = None
            age = None
            subtext = thing.find_next_sibling("tr")
            if subtext:
                score_el = subtext.select_one("span.score")
                if score_el:
                    points = int(score_el.get_text().split()[0])
                age_el = subtext.select_one("span.age")
                if age_el and age_el.get("title"):
                    age = age_el["title"].split()[0]
            items.append(
                NormalizedItem(
                    kind=kind,
                    dedupe_key=f"hn:{item_id}",
                    url=url,
                    title=title_link.get_text(strip=True),
                    created_at=infer_timestamp(age),
                    raw={
                        "hn_id": item_id, "points": points,
                        "discussion": f"{BASE}/item?id={item_id}",
                    },
                    occurrence_detail=f"hn {kind}",
                )
            )
        else:  # comment row
            comment_el = thing.select_one("div.comment, span.commtext")
            onstory = thing.select_one("span.onstory a")
            age_el = thing.select_one("span.age")
            items.append(
                NormalizedItem(
                    kind=kind,
                    dedupe_key=f"hn:{item_id}",
                    url=f"{BASE}/item?id={item_id}",
                    title=f"Comment on: {onstory.get_text(strip=True)}" if onstory else "HN comment",
                    text=comment_el.get_text(" ", strip=True)[:2000] if comment_el else None,
                    created_at=infer_timestamp(age_el["title"].split()[0])
                    if age_el and age_el.get("title") else None,
                    raw={"hn_id": item_id, "is_comment": True},
                    occurrence_detail=f"hn {kind} (comment)",
                )
            )

    more = soup.select_one("a.morelink")
    return items, more.get("href") if more else None


class HnScraper(BaseScraper):
    name = "hn"
    source_kind = "hn"

    def __init__(self, conn, cfg, context, include_upvoted: bool = True) -> None:
        super().__init__(conn, cfg, context)
        self.include_upvoted = include_upvoted

    def _walk(self, page, start_path: str, kind: str, seen_newest: set[str]) -> None:
        path = start_path
        page_num = 0
        while path:
            response = page.goto(f"{BASE}/{path}", wait_until="domcontentloaded")
            if response and response.status == 429:
                self.stop("rate limited by HN (HTTP 429)")
            if "login" in page.url:
                self.stop(f"HN asked for login while fetching {kind}")
            html = page.content()
            self.save_raw(f"{kind}-page{page_num}.html", html)
            items, next_path = parse_listing(html, kind)
            if not items and page_num == 0 and "no such" in html.lower():
                self.stop(f"HN returned an unrecognized page for {kind}")
            new = 0
            for item in items:
                if item.dedupe_key in seen_newest:
                    return  # incremental: reached items from a previous run
                self.save_item(item)
                new += 1
            if not next_path or new == 0:
                return
            path = next_path
            page_num += 1
            self.pause(1.0, 2.0)

    def run(self) -> int:
        username = self.cfg.usernames.hn
        if not username:
            self.stop("set usernames.hn in config.yaml first")
        seen = {
            r["dedupe_key"]
            for r in self.conn.execute(
                "SELECT dedupe_key FROM items WHERE kind IN ('favorite','upvote')"
            )
        }
        page = self.context.new_page()
        try:
            # favorites are public: stories then comments
            self._walk(page, f"favorites?id={username}", "favorite", seen)
            self._walk(page, f"favorites?id={username}&comments=t", "favorite", seen)
            if self.include_upvoted:
                from km.scrapers.session import session_valid

                if not session_valid(self.context, "hn"):
                    self.stop("upvoted needs a logged-in HN session; run km login")
                self._walk(page, f"upvoted?id={username}", "upvote", seen)
                self._walk(page, f"upvoted?id={username}&comments=t", "upvote", seen)
        finally:
            page.close()
        return self.items_saved
