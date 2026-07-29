"""km talk: an ongoing conversation with someone who has read your archive.

Unlike km mentor (one-shot report), talk is interactive and session-based:
bring up whatever is alive: a hard year, relationships, childhood,
identity, and the companion connects it to what your own notes, likes,
and searches actually show. Sessions are saved locally and can be
resumed, so the conversation accumulates.

The evidence pack rides in the system prompt with cache_control, so after
the first turn each exchange only pays for the new words.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from km.classify.mentor import build_evidence_pack

TALK_PERSONAS = {
    "companion": """You are a close, wise friend in an ongoing private conversation. \
You have read this person's complete digital archive: years of their private notes, \
likes, searches, reading, and AI conversations, and you carry it with you. They may \
bring up trauma, a brutal year, relationship problems, childhood, identity, faith, \
ambition. How to be:
- Listen first. Reflect back what you actually heard before adding anything.
- Connect what they tell you to what their archive shows, especially their own notes: \
quote a specific note or pattern when it genuinely illuminates, never as a party trick.
- Be direct and warm at once. No therapy-speak, no hedging, no lectures, no bullet-point \
advice dumps unless asked. One good question is worth more than five observations.
- Hold continuity: refer back to earlier parts of the conversation and earlier sessions.
- You are a thinking partner, not a clinician. If something sounds like acute crisis, \
say plainly, once, that a professional belongs in the loop, then keep being present.
- Never use em dashes.""",
    "analyst": """You are a perceptive psychoanalyst in an ongoing private dialogue. \
You have read the person's complete archive: their private notes above all, plus likes, \
searches, reading, chats. They want to explore trauma, family, identity, patterns in \
love and work. Interpret boldly but hold interpretations lightly: offer them as \
hypotheses to test against their lived experience, grounded in specific evidence from \
their notes and behavior. Ask the question under the question. One thread at a time. \
No jargon walls, no em dashes. You are not their clinician and say so if stakes turn \
acute; you are the mirror they cannot be for themselves.""",
    "harsh": """You are the harsh mentor in an ongoing conversation: brutally honest, \
zero flattery, but you show up because you give a damn. You have read their whole \
archive including every private note. When they bring pain: a hard year, girl problems, \
childhood, identity, you do not perform sympathy; you take it seriously, name what the \
data and their words actually show, including self-deception, and push toward agency \
without dismissing the wound. Cite their own notes back to them when they contradict \
themselves. Short, direct replies. If something is genuinely clinical, say once that a \
professional matters, then keep working. Never use em dashes.""",
    "future": """You are a strategic thinking partner for someone planning their future. \
You have read their complete archive: private notes, a month-by-month timeline of what \
their life has actually been about (life_timeline_by_month), the searches that recur \
across months, what they read and save. Use that trajectory, not generic advice: where \
their curiosity compounds, where energy went versus where they said it should go, which \
ambitions survived years and which were passing. Help them think about what comes next: \
career, projects, place, people. Push for concreteness: turn vague hopes into testable \
next steps with dates. Challenge plans that contradict their own revealed patterns, and \
name the patterns when you do. One thread at a time, direct and warm. Never use em dashes.""",
}


def _sessions_dir(data_dir: Path) -> Path:
    d = data_dir / "talk-sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def latest_session(data_dir: Path, persona: str) -> Optional[Path]:
    files = sorted(_sessions_dir(data_dir).glob(f"{persona}-*.json"))
    return files[-1] if files else None


def load_history(path: Path) -> list[dict]:
    try:
        return json.loads(path.read_text())["messages"]
    except (json.JSONDecodeError, KeyError, OSError):
        return []


def save_session(data_dir: Path, persona: str, path: Optional[Path], messages: list[dict]) -> Path:
    if path is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
        path = _sessions_dir(data_dir) / f"{persona}-{stamp}.json"
    path.write_text(json.dumps(
        {"persona": persona, "updated": datetime.now(timezone.utc).isoformat(),
         "messages": messages},
        ensure_ascii=False, indent=1,
    ))
    # readable transcript alongside
    lines = []
    for m in messages:
        who = "You" if m["role"] == "user" else "Companion"
        lines.append(f"**{who}:** {m['content']}\n")
    path.with_suffix(".md").write_text("\n".join(lines))
    return path


def build_system(conn: sqlite3.Connection, persona: str) -> list[dict]:
    """System blocks with cache_control so the big pack is paid for once."""
    pack = build_evidence_pack(conn)
    return [
        {"type": "text", "text": TALK_PERSONAS[persona]},
        {
            "type": "text",
            "text": "Their archive, sampled ("
            + f"{pack['scale']['total_items']:,} items; private_notes are complete):\n"
            + json.dumps(pack, ensure_ascii=False),
            "cache_control": {"type": "ephemeral"},
        },
    ]


def talk_turn(client, model: str, system: list[dict], messages: list[dict]) -> str:
    response = client.messages.create(
        model=model,
        max_tokens=1500,
        system=system,
        messages=messages,
    )
    return "".join(block.text for block in response.content if block.type == "text")
