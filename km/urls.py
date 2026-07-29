"""URL canonicalization and domain extraction.

Rules (see spec): lowercase scheme+host, strip utm_*/fbclid/gclid/ref/s/t
params, strip trailing slashes and fragments, unify twitter hosts, unwrap
Google redirect wrappers. Dedupe happens on the canonical form; original
URLs are preserved on the item.
"""
from __future__ import annotations

from urllib.parse import parse_qs, parse_qsl, unquote, urlencode, urlsplit, urlunsplit

_TRACKING_PARAMS = {"fbclid", "gclid", "ref", "s", "t"}
_TWITTER_HOSTS = {"mobile.twitter.com", "x.com", "www.x.com", "twitter.com", "www.twitter.com", "m.twitter.com"}


def unwrap_google_redirect(url: str) -> str:
    """Strip the https://www.google.com/url?q=<real>&... wrapper."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if parts.hostname and parts.hostname.endswith("google.com") and parts.path == "/url":
        q = parse_qs(parts.query).get("q") or parse_qs(parts.query).get("url")
        if q:
            return unquote(q[0])
    return url


def canonicalize(url: str) -> str:
    """Return the canonical form of a URL for deduplication."""
    if not url:
        return ""
    url = unwrap_google_redirect(url.strip())
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.scheme or not parts.netloc:
        return url

    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if host in _TWITTER_HOSTS:
        host = "twitter.com"
    netloc = host
    try:
        port = parts.port  # raises ValueError on junk like localhost:${PORT}
    except ValueError:
        port = None
    if port and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        netloc = f"{host}:{port}"

    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.startswith("utm_") and k not in _TRACKING_PARAMS
    ]
    query = urlencode(kept)
    path = parts.path.rstrip("/")
    return urlunsplit((scheme, netloc, path, query, ""))


def domain_of(url: str) -> str:
    """Lowercased hostname with a leading www. removed; empty on failure."""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host
