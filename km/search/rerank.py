"""Claude re-ranking for km ask --ai.

Takes the top hybrid candidates, sends them with the fuzzy query, and
returns ranked picks with one line of reasoning each. This is what finds
the half-remembered tweet when neither keyword nor vector similarity
alone nails it.
"""
from __future__ import annotations

import json

from km.classify.client import call_claude, parse_json_response

_SYSTEM = """You help someone find a half-remembered item in their personal knowledge base. \
They describe a fuzzy memory; you see candidate items from hybrid search. Pick the candidates \
most likely to be what they remember, best match first. Judge by meaning, not keyword overlap: \
the memory may paraphrase, misremember words, or describe replies/context not in the text.

Respond with ONLY a JSON array (no prose, no code fences) of at most 8 picks:
[{"id": <candidate id>, "reasoning": "<one short line: this is probably it because...>"}]
If nothing plausibly matches, return []."""


def rerank(client, model: str, query: str, candidates: list[dict],
           conn=None, cfg=None) -> list[dict]:
    """candidates: dicts with id/title/snippet/kind/sources. Returns picks
    (subset of candidates, ordered) each with a `reasoning` line. With conn,
    spend is recorded (and the budget enforced when cfg is also given)."""
    payload = [
        {
            "id": c["id"],
            "kind": c["kind"],
            "title": c["title"],
            "text": c["snippet"],
            "sources": c.get("sources", []),
        }
        for c in candidates
    ]
    user = (
        f"My fuzzy memory: {query}\n\nCandidates:\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    if conn is not None:
        from km.classify.spend import tracked_create

        response = tracked_create(
            conn, cfg, client, "rerank",
            model=model, max_tokens=2000, system=_SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(b.text for b in response.content if b.type == "text")
    else:
        text = call_claude(client, model, _SYSTEM, user, max_tokens=2000)
    parsed = parse_json_response(text)
    if not isinstance(parsed, list):
        return []
    by_id = {c["id"]: c for c in candidates}
    picks = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        candidate = by_id.get(entry.get("id"))
        if candidate:
            picks.append({**candidate, "reasoning": str(entry.get("reasoning") or "")})
    return picks
