"""km wrapped: a year in review as one shareable, self-contained HTML page.

Entirely offline: system fonts, inline CSS, no scripts. The page is meant
to be screenshotted, so numbers are big and the palette matches the km
UI (lamplight on dark).
"""
from __future__ import annotations

import html
import sqlite3

from km.extract.rewind import year_rewind
from km.extract.rhythms import DEFAULT_TZ, hourly_rhythms

_KIND_LABELS = {
    "search_query": "searches", "visit": "pages read", "like": "tweets liked",
    "own_tweet": "tweets written", "retweet": "retweets", "note": "notes written",
    "chat_conversation": "AI conversations", "bookmark_tweet": "tweets bookmarked",
    "saved_post": "posts saved", "bookmark": "bookmarks", "chat_message": "chat messages",
    "saved_comment": "comments saved", "linked": "links mined",
}


def _fmt_hour(hour: int) -> str:
    if hour == 0:
        return "midnight"
    if hour < 12:
        return f"{hour}am"
    if hour == 12:
        return "noon"
    return f"{hour - 12}pm"


def wrapped_data(conn: sqlite3.Connection, year: str, tz: str = DEFAULT_TZ) -> dict:
    data = year_rewind(conn, year)
    rhythms = hourly_rhythms(conn, tz)
    by_hour = rhythms["by_hour_year"].get(year, [0] * 24)
    peak_hour = max(range(24), key=lambda h: by_hour[h]) if any(by_hour) else None
    night = sum(by_hour[:5])
    timed = sum(by_hour)
    months = [0] * 12
    for row in conn.execute(
        """SELECT substr(created_at, 6, 2) m, count(*) c FROM items
           WHERE substr(created_at, 1, 4) = ? GROUP BY m""", (year,)):
        try:
            months[int(row["m"]) - 1] = row["c"]
        except (ValueError, IndexError):
            pass
    busiest = conn.execute(
        """SELECT substr(created_at, 1, 10) d, count(*) c FROM items
           WHERE substr(created_at, 1, 4) = ? GROUP BY d ORDER BY c DESC LIMIT 1""",
        (year,),
    ).fetchone()
    active_days = conn.execute(
        """SELECT count(DISTINCT substr(created_at, 1, 10)) FROM items
           WHERE substr(created_at, 1, 4) = ?""", (year,)).fetchone()[0]
    data.update({
        "months": months,
        "peak_hour": peak_hour,
        "night_share": round(night / timed * 100) if timed else 0,
        "busiest_day": dict(busiest) if busiest else None,
        "active_days": active_days,
    })
    return data


def ai_epilogue(data: dict, client, model: str) -> str:
    """One paragraph from Claude on what the year was actually about."""
    import json

    summary = {
        "year": data["year"],
        "counts": data["counts"],
        "new_obsessions": data["new_obsessions"][:12],
        "places_discovered": data["new_domains"][:12],
        "notes_written": data["notes"][:20],
        "ai_conversations": data["chats"][:30],
        "peak_hour": data.get("peak_hour"),
        "night_share_pct": data.get("night_share"),
    }
    response = client.messages.create(
        model=model,
        max_tokens=400,
        system=(
            "You write the closing paragraph of someone's personal year-in-review, "
            "built from their own digital traces. 90 to 130 words, second person, "
            "warm but unsentimental, name what the year was actually about including "
            "the hard parts the data implies. No lists, no flattery, no em dashes."
        ),
        messages=[{"role": "user", "content": json.dumps(summary, ensure_ascii=False)}],
    )
    return "".join(b.text for b in response.content if b.type == "text").strip()


def render_wrapped(data: dict, epilogue: str | None = None) -> str:
    year = data["year"]
    e = html.escape

    def counts_tiles() -> str:
        ordered = sorted(data["counts"].items(), key=lambda kv: -kv[1])[:6]
        tiles = "".join(
            f'<div class="tile"><div class="n">{c:,}</div>'
            f'<div class="l">{e(_KIND_LABELS.get(k, k))}</div></div>'
            for k, c in ordered
        )
        return tiles

    def month_bars() -> str:
        peak = max(data["months"]) or 1
        bars = "".join(
            f'<div class="bar" style="height:{max(3, round(c / peak * 100))}%" title="{c:,}"></div>'
            for c in data["months"]
        )
        return bars

    def obsession_rows() -> str:
        return "".join(
            f'<li><b>{e(o["term"])}</b> · {o["count"]} searches, {o["before"]} before this year</li>'
            for o in data["new_obsessions"][:8]
        )

    def domain_rows() -> str:
        return "".join(
            f'<li><b>{e(d["domain"])}</b> · {d["visits"]} visits</li>'
            for d in data["new_domains"][:8]
        )

    best_tweet = ""
    # replies and fragments are engagement noise, not the year's best words
    standalone = [t for t in data["best_tweets"]
                  if not (t["text"] or "").startswith("@") and len(t["text"] or "") > 30]
    if standalone:
        t = standalone[0]
        best_tweet = (
            '<div class="sec"><div class="cap">your tweet that landed</div>'
            f'<blockquote>{e(" ".join((t["text"] or "").split()))}</blockquote></div>'
        )

    busiest = ""
    if data["busiest_day"]:
        busiest = (
            f'<div class="tile"><div class="n">{data["busiest_day"]["c"]:,}</div>'
            f'<div class="l">traces on {e(data["busiest_day"]["d"])}, your busiest day</div></div>'
        )

    peak = ""
    if data["peak_hour"] is not None:
        peak = (
            f'<div class="tile"><div class="n">{_fmt_hour(data["peak_hour"])}</div>'
            f'<div class="l">your peak hour · {data["night_share"]}% of activity after midnight</div></div>'
        )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>km wrapped {year}</title>
<style>
  :root{{--bg:#171310;--raised:#1f1a15;--ink:#e8ddcb;--dim:#a89880;--faint:#6f6252;
        --accent:#e3b04b;--line:#2e2620}}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:var(--bg);color:var(--ink);
       font-family:"Iowan Old Style",Georgia,"Times New Roman",serif;
       padding:56px 20px 80px}}
  .page{{max-width:680px;margin:0 auto}}
  .cap{{font-family:ui-monospace,Menlo,monospace;font-size:11px;letter-spacing:.16em;
       text-transform:uppercase;color:var(--faint);margin-bottom:10px}}
  h1{{font-size:64px;line-height:1;letter-spacing:-1px}}
  h1 b{{color:var(--accent)}}
  .sub{{color:var(--dim);margin-top:12px;font-size:17px}}
  .tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
         gap:12px;margin-top:36px}}
  .tile{{background:var(--raised);border:1px solid var(--line);border-radius:12px;
        padding:20px}}
  .tile .n{{font-size:34px;color:var(--accent)}}
  .tile .l{{color:var(--dim);font-size:13.5px;margin-top:4px;
           font-family:ui-monospace,Menlo,monospace}}
  .sec{{margin-top:44px}}
  .chart{{display:flex;align-items:flex-end;gap:6px;height:120px;background:var(--raised);
         border:1px solid var(--line);border-radius:12px;padding:14px}}
  .bar{{flex:1;background:linear-gradient(180deg,#e3b04b,#8a6c2f);border-radius:4px 4px 0 0}}
  .mlabels{{display:flex;gap:6px;margin-top:6px;font-family:ui-monospace,Menlo,monospace;
           font-size:10px;color:var(--faint)}}
  .mlabels span{{flex:1;text-align:center}}
  ul{{list-style:none;display:grid;gap:10px}}
  li{{border-left:3px solid var(--accent);background:var(--raised);
     border-radius:0 10px 10px 0;padding:12px 16px;font-size:16px;color:var(--dim)}}
  li b{{color:var(--ink);font-weight:600}}
  blockquote{{border-left:3px solid var(--accent);background:var(--raised);
             border-radius:0 10px 10px 0;padding:18px 22px;font-style:italic;font-size:19px}}
  footer{{margin-top:64px;color:var(--faint);font-size:13px;text-align:center;
         font-family:ui-monospace,Menlo,monospace}}
</style></head>
<body><div class="page">
  <div class="cap">km wrapped</div>
  <h1>{year}<b>.</b></h1>
  <p class="sub">{data["total"]:,} traces across {data["active_days"]} days.
  This is what the archive saw.</p>

  <div class="tiles">{counts_tiles()}{busiest}{peak}</div>

  <div class="sec"><div class="cap">the shape of the year</div>
    <div class="chart">{month_bars()}</div>
    <div class="mlabels"><span>J</span><span>F</span><span>M</span><span>A</span><span>M</span><span>J</span><span>J</span><span>A</span><span>S</span><span>O</span><span>N</span><span>D</span></div>
  </div>

  {'<div class="sec"><div class="cap">new obsessions</div><ul>' + obsession_rows() + '</ul></div>' if data["new_obsessions"] else ''}
  {'<div class="sec"><div class="cap">places discovered</div><ul>' + domain_rows() + '</ul></div>' if data["new_domains"] else ''}
  {best_tweet}
  {'<div class="sec"><div class="cap">what the year meant</div><blockquote>' + e(epilogue) + '</blockquote></div>' if epilogue else ''}

  <footer>generated locally by km · your data never left your machine · montroselabs.ai/km</footer>
</div></body></html>
"""


def export_wrapped(
    conn: sqlite3.Connection, year: str, out_path,
    tz: str = DEFAULT_TZ, epilogue: str | None = None,
) -> dict:
    data = wrapped_data(conn, year, tz)
    out_path.write_text(render_wrapped(data, epilogue))
    return data
