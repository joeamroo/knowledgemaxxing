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


def items_from_api_json(
    payload: dict | list, kind: str = "saved_post",
    occurrence_kind: str = "", label: str = "saved",
) -> list[NormalizedItem]:
    """Pull posts out of a reader API response, defensively.

    Substack shuffles its internal API shapes, so we look for any dicts
    that look like posts (canonical_url/title) wherever they nest. The
    same walker serves saved, liked, and restacked surfaces; only the
    item kind and labels change.
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
                        kind=kind,
                        dedupe_key=f"url:{canonicalize(url)}",
                        url=url,
                        title=title,
                        author=author,
                        created_at=infer_timestamp(saved_at),
                        raw={"publication": pub_name, "substack_id": node.get("id")},
                        occurrence_kind=occurrence_kind or kind,
                        occurrence_detail=f"substack {label}: {pub_name or domain_of(url)}",
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

    def _scrape_surface(self, page, url: str, kind: str, occurrence_kind: str,
                        label: str, dom_fallback: bool = False) -> int:
        """Scroll one reader surface, intercepting its API responses."""
        collected: dict[str, NormalizedItem] = {}
        response_count = 0

        def on_response(response) -> None:
            nonlocal response_count
            r_url = response.url
            if "substack.com" not in r_url or "api" not in r_url:
                return
            try:
                payload = response.json()
            except Exception:
                return
            response_count += 1
            self.save_raw(f"{label}-api-{response_count}.json", payload)
            for item in items_from_api_json(payload, kind, occurrence_kind, label):
                collected.setdefault(item.dedupe_key, item)

        page.on("response", on_response)
        try:
            page.goto(url, wait_until="domcontentloaded")
            if "sign-in" in page.url or "login" in page.url:
                self.stop("Substack redirected to sign-in; run km login")
            page.wait_for_timeout(3000)
            if page.url.rstrip("/").endswith("substack.com") and not url.rstrip("/").endswith("substack.com"):
                return 0  # surface does not exist for this account; bounced home

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

            if not collected and dom_fallback:
                html = page.content()
                self.save_raw(f"{label}-dom.html", html)
                for item in items_from_dom(html):
                    collected.setdefault(item.dedupe_key, item)

            for item in collected.values():
                self.save_item(item)
        finally:
            page.remove_listener("response", on_response)
        return len(collected)

    def run(self) -> int:
        from km.scrapers.session import session_valid

        if not session_valid(self.context, "substack"):
            self.stop("no valid Substack session; run km login")

        page = self.context.new_page()
        try:
            self._scrape_surface(page, "https://substack.com/saved",
                                 "saved_post", "saved_post", "saved", dom_fallback=True)
            # likes and restacks: reader surfaces vary by account age, so
            # try candidates and accept whichever loads
            for url in ("https://substack.com/liked", "https://substack.com/library/liked"):
                if self._scrape_surface(page, url, "like", "like", "liked"):
                    break
            handle = None
            try:
                handle = page.evaluate(
                    """async () => { try {
                         const r = await fetch('https://substack.com/api/v1/user/self',
                                               {credentials: 'include'});
                         const j = await r.json();
                         return j.handle || j.username || null;
                       } catch (e) { return null } }""")
            except Exception:
                pass
            if handle:
                for url in (f"https://substack.com/@{handle}/restacks",
                            f"https://substack.com/@{handle}/notes"):
                    if self._scrape_surface(page, url, "saved_post", "restack", "restacked"):
                        break
        finally:
            page.close()
        return self.items_saved
