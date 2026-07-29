"""Substack saved-posts scraper.

The reader is a heavy SPA, so we prefer intercepting its JSON API
responses while scrolling substack.com/saved; DOM parsing is the
fallback. Substack has no official export for saved posts.
"""
from __future__ import annotations

import json
from typing import Optional

from km.models import NormalizedItem
from km.scrapers.base import BaseScraper
from km.timeutil import infer_timestamp
from km.urls import canonicalize, domain_of


def items_from_api_json(payload: dict | list) -> list[NormalizedItem]:
    """Pull saved posts out of a reader API response, defensively.

    Substack shuffles its internal API shapes, so we look for any dicts
    that look like posts (canonical_url/title) wherever they nest.
    """
    found: list[NormalizedItem] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            url = node.get("canonical_url") or node.get("canonicalUrl")
            title = node.get("title")
            if url and title and isinstance(url, str) and url.startswith("http"):
                pub = node.get("publication") or {}
                pub_name = (
                    pub.get("name") if isinstance(pub, dict) else None
                ) or node.get("publication_name") or node.get("pub_name")
                author = node.get("author_name") or (
                    ", ".join(
                        b.get("name", "") for b in node.get("publishedBylines", [])
                        if isinstance(b, dict)
                    ) or None
                )
                saved_at = (
                    node.get("saved_at") or node.get("savedAt")
                    or node.get("post_date") or node.get("published_at")
                )
                found.append(
                    NormalizedItem(
                        kind="saved_post",
                        dedupe_key=f"url:{canonicalize(url)}",
                        url=url,
                        title=title,
                        author=author,
                        created_at=infer_timestamp(saved_at),
                        raw={"publication": pub_name, "substack_id": node.get("id")},
                        occurrence_detail=f"substack saved: {pub_name or domain_of(url)}",
                    )
                )
                return  # don't descend into a matched post
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    return found


def items_from_dom(html: str) -> list[NormalizedItem]:
    """Fallback: pull /p/ post links out of the rendered reader page."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    items = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/p/" not in href or not href.startswith("http"):
            continue
        canonical = canonicalize(href.split("?")[0])
        if canonical in seen:
            continue
        seen.add(canonical)
        title = a.get_text(" ", strip=True)
        if not title or len(title) < 5:
            continue
        items.append(
            NormalizedItem(
                kind="saved_post",
                dedupe_key=f"url:{canonical}",
                url=href.split("?")[0],
                title=title[:300],
                raw={},
                occurrence_detail=f"substack saved: {domain_of(href)}",
            )
        )
    return items


class SubstackScraper(BaseScraper):
    name = "substack_saved"
    source_kind = "substack_saved"

    MAX_IDLE_SCROLLS = 6

    def run(self) -> int:
        from km.scrapers.session import session_valid

        if not session_valid(self.context, "substack"):
            self.stop("no valid Substack session; run km login")

        collected: dict[str, NormalizedItem] = {}
        response_count = 0

        def on_response(response) -> None:
            nonlocal response_count
            url = response.url
            if "substack.com" not in url or "api" not in url:
                return
            if not any(marker in url for marker in ("saved", "library", "reader")):
                return
            try:
                payload = response.json()
            except Exception:
                return
            response_count += 1
            self.save_raw(f"api-{response_count}.json", payload)
            for item in items_from_api_json(payload):
                collected.setdefault(item.dedupe_key, item)

        page = self.context.new_page()
        page.on("response", on_response)
        try:
            page.goto("https://substack.com/saved", wait_until="domcontentloaded")
            if "sign-in" in page.url or "login" in page.url:
                self.stop("Substack redirected to sign-in; run km login")
            page.wait_for_timeout(3000)

            # pointer must sit over the feed or wheel events scroll nothing
            viewport = page.viewport_size or {"width": 1280, "height": 900}
            center = (viewport["width"] * 0.5, viewport["height"] * 0.55)
            idle = 0
            last_count = -1
            while idle < self.MAX_IDLE_SCROLLS:
                page.mouse.move(*center)
                page.mouse.wheel(0, 1400)
                if idle >= 2:
                    page.evaluate("window.scrollBy(0, window.innerHeight * 1.5)")
                self.pause(1.0, 2.0)
                if len(collected) == last_count:
                    idle += 1
                else:
                    idle = 0
                    last_count = len(collected)

            if not collected:  # API interception found nothing: DOM fallback
                html = page.content()
                self.save_raw("saved-dom.html", html)
                for item in items_from_dom(html):
                    collected.setdefault(item.dedupe_key, item)

            for item in collected.values():
                self.save_item(item)
        finally:
            page.close()
        return self.items_saved
