"""Playwright session management.

A persistent browser context at ~/.km/browser-profile/ keeps logins
across runs. Alternative: attach to real Chrome over CDP (--cdp <port>),
but the dedicated profile is the default so the daily browser is never
touched.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

PROFILE_DIR = Path("~/.km/browser-profile").expanduser()

# Auth cookie fingerprints per service: cheap validity checks without page loads
SERVICES: dict[str, dict] = {
    "x": {
        "label": "X (Twitter)",
        "login_url": "https://x.com/login",
        "cookie_domain": ".x.com",
        "cookie_name": "auth_token",
    },
    "reddit": {
        "label": "Reddit",
        "login_url": "https://old.reddit.com/login",
        "cookie_domain": ".reddit.com",
        "cookie_name": "reddit_session",
    },
    "substack": {
        "label": "Substack",
        "login_url": "https://substack.com/sign-in",
        "cookie_domain": "substack.com",
        "cookie_name": "substack.sid",
    },
    "hn": {
        "label": "Hacker News",
        "login_url": "https://news.ycombinator.com/login",
        "cookie_domain": "news.ycombinator.com",
        "cookie_name": "user",
    },
}


@contextmanager
def browser_context(headed: bool = False, cdp_port: Optional[int] = None) -> Iterator:
    """Yield a Playwright BrowserContext: persistent profile or CDP attach."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        if cdp_port:
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            try:
                yield context
            finally:
                browser.close()
        else:
            PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            context = p.chromium.launch_persistent_context(
                str(PROFILE_DIR),
                headless=not headed,
                viewport={"width": 1280, "height": 900},
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                yield context
            finally:
                context.close()


def session_valid(context, service: str) -> bool:
    """Check login validity by auth cookie presence and expiry.

    context.cookies() can throw when Playwright's connection got confused
    by a page that tore down frames mid-navigation (X's login flow does
    this); one retry after a beat almost always recovers, and a check
    failure must never crash the login walkthrough.
    """
    import time

    spec = SERVICES[service]
    cookies = None
    for attempt in (1, 2):
        try:
            cookies = context.cookies()
            break
        except Exception:
            if attempt == 1:
                time.sleep(1.0)
    if cookies is None:
        return False
    for cookie in cookies:
        if cookie["name"] == spec["cookie_name"] and spec["cookie_domain"] in cookie["domain"]:
            expires = cookie.get("expires", -1)
            if expires in (-1, None) or expires > time.time():
                return True
    return False


def check_all(context) -> dict[str, bool]:
    return {name: session_valid(context, name) for name in SERVICES}
