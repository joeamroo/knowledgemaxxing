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
    "therapist": """You are a therapist-like companion in an ongoing, long-term \
relationship with this person. You are not a licensed clinician and you say so once \
if stakes ever turn acute, but you do the thing a great therapist does: you remember, \
you notice, and you stay. You have read their complete archive: every private note, \
years of searches (including the 3am ones), what they save and never finish, the \
month-by-month shape of their life, their open and overdue commitments, and notes \
from your own past sessions together. How to work:
- Continuity first. Open by picking up threads from past session notes when they exist: \
what they were carrying last time, what they said they would try.
- Ground interpretations in evidence: quote their own note or pattern, gently, when it \
illuminates. The archive is the shared object in the room.
- Notice avoidance: the topic that appears in searches but never in conversation, the \
task rescheduled five times, the person mentioned once and dropped.
- One thread at a time. Ask the question under the question. Silence-friendly pacing: \
short replies are fine.
- Warmth without performance. No therapy-speak, no diagnosis, no advice dumps. \
Never use em dashes.""",
    "secretary": """You are a sharp, kind chief-of-staff for this person's actual life. \
You can see their open tasks (including what is overdue and how long it slipped), \
today's reading feed, their recent searches, notes, and calendar of behavior (when \
they actually focus). Each conversation: give the state of play in two sentences, \
name the one thing that matters most today and why, be blunt about slippage without \
moralizing, and end with a concrete plan: at most three actions with a suggested order \
and time. If they are avoiding something repeatedly, say so and make the first step \
smaller. Never use em dashes.""",
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
    try:
        from km.taskdriver import tasks_for_ai

        pack["commitments"] = tasks_for_ai(conn)
    except Exception:
        pass
    try:
        from km.feed import get_daily_feed

        pack["todays_reading_feed"] = [
            {"title": f["title"], "reason": f["reason"], "read": bool(f["read"])}
            for f in get_daily_feed(conn)
        ]
    except Exception:
        pass
    notes = session_notes(conn, persona, limit=8)
    if notes:
        pack["past_session_notes"] = notes
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


def session_notes(conn: sqlite3.Connection, persona: str, limit: int = 8) -> list[dict]:
    return [
        {"date": r["date"], "notes": r["summary"]}
        for r in conn.execute(
            """SELECT date, summary FROM companion_notes WHERE persona=?
               ORDER BY date DESC LIMIT ?""", (persona, limit))
    ]


_NOTES_SYSTEM = """Summarize this companion session into private session notes for \
the next session, 120 words max: threads discussed, anything the person committed to \
or resolved, open questions to pick up next time, emotional weather. Second person \
about them ("they..."). No em dashes."""


def summarize_session(conn: sqlite3.Connection, client, model: str,
                      persona: str, path: Path, messages: list[dict]) -> None:
    """Write session notes so the next session starts with memory. Idempotent."""
    if not messages or conn.execute(
        "SELECT 1 FROM companion_notes WHERE session_file=?", (path.name,)
    ).fetchone():
        return
    transcript = "\n".join(f"{m['role']}: {m['content'][:600]}" for m in messages[-30:])
    from km.classify.spend import tracked_create

    response = tracked_create(
        conn, None, client, "summary",
        model=model, max_tokens=300, system=_NOTES_SYSTEM,
        messages=[{"role": "user", "content": transcript}],
    )
    summary = "".join(b.text for b in response.content if b.type == "text").strip()
    now = datetime.now(timezone.utc)
    conn.execute(
        """INSERT OR IGNORE INTO companion_notes(persona, session_file, date, summary, created_at)
           VALUES (?,?,?,?,?)""",
        (persona, path.name, now.date().isoformat(), summary, now.isoformat()))
    conn.commit()


def talk_turn(client, model: str, system: list[dict], messages: list[dict],
              conn=None, cfg=None) -> str:
    """One companion turn. With conn, spend is recorded (and the budget
    enforced when cfg is also given)."""
    if conn is not None:
        from km.classify.spend import tracked_create

        response = tracked_create(
            conn, cfg, client, "talk",
            model=model, max_tokens=1500, system=system, messages=messages,
        )
    else:
        response = client.messages.create(
            model=model, max_tokens=1500, system=system, messages=messages,
        )
    return "".join(block.text for block in response.content if block.type == "text")
