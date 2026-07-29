"""Activity rhythms: when you are actually awake, working, and spiraling.

Everything here runs offline over timestamps already in the DB. Rows with
an exact-midnight time are date-only precision from sources that never
knew the hour (Takeout HTML, some archives), so they are excluded from
hour-of-day math rather than faking a midnight spike.
"""
from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

DEFAULT_TZ = "America/Chicago"

_DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _timed_buckets(conn: sqlite3.Connection, tz: str):
    """Yield (local datetime truncated to the hour, count).

    Aggregates in SQL by UTC hour-bucket first (all stored timestamps are
    UTC ISO), so Python only converts a few thousand distinct hours
    instead of hundreds of thousands of rows.
    """
    zone = ZoneInfo(tz)
    for row in conn.execute(
        """SELECT substr(created_at, 1, 13) b, count(*) c FROM items
           WHERE created_at IS NOT NULL
           AND substr(created_at, 12, 8) != '00:00:00'
           AND kind IN ('visit','search_query','like','own_tweet','retweet',
                        'chat_conversation','note','bookmark_tweet','saved_post')
           GROUP BY b"""
    ):
        try:
            stamp = datetime.fromisoformat(row["b"] + ":00:00+00:00")
        except ValueError:
            continue
        yield stamp.astimezone(zone), row["c"]


def hourly_rhythms(conn: sqlite3.Connection, tz: str = DEFAULT_TZ) -> dict:
    """Hour-of-day and day-of-week histograms, plus a monthly night-owl index.

    night_owl_by_month: share of each month's timed activity that happened
    between midnight and 5am local. A rising line is a sliding sleep schedule.
    """
    by_hour: Counter = Counter()
    by_weekday: Counter = Counter()
    by_hour_year: dict[str, Counter] = defaultdict(Counter)
    month_total: Counter = Counter()
    month_night: Counter = Counter()
    for local, count in _timed_buckets(conn, tz):
        by_hour[local.hour] += count
        by_weekday[local.weekday()] += count
        by_hour_year[str(local.year)][local.hour] += count
        month = local.strftime("%Y-%m")
        month_total[month] += count
        if local.hour < 5:
            month_night[month] += count
    night_owl = {
        month: round(month_night[month] / total, 3)
        for month, total in sorted(month_total.items()) if total >= 30
    }
    return {
        "by_hour": [by_hour.get(h, 0) for h in range(24)],
        "by_weekday": {_DAY_NAMES[d]: by_weekday.get(d, 0) for d in range(7)},
        "by_hour_year": {
            year: [counts.get(h, 0) for h in range(24)]
            for year, counts in sorted(by_hour_year.items())
        },
        "night_owl_by_month": night_owl,
        "total_timed": sum(by_hour.values()),
    }


def activity_streaks(conn: sqlite3.Connection) -> dict:
    """Longest and current runs of consecutive active days."""
    days = sorted(
        date.fromisoformat(row["d"])
        for row in conn.execute(
            """SELECT DISTINCT substr(created_at, 1, 10) d FROM items
               WHERE created_at IS NOT NULL AND substr(created_at, 1, 4) >= '2010'"""
        )
        if len(row["d"] or "") == 10
    )
    if not days:
        return {"active_days": 0, "longest": 0, "longest_span": None, "current": 0}
    longest = run = 1
    longest_end = days[0]
    for prev, cur in zip(days, days[1:]):
        run = run + 1 if cur - prev == timedelta(days=1) else 1
        if run > longest:
            longest, longest_end = run, cur
    current = 1
    for prev, cur in zip(reversed(days[:-1]), reversed(days)):
        if cur - prev == timedelta(days=1):
            current += 1
        else:
            break
    return {
        "active_days": len(days),
        "first_day": days[0].isoformat(),
        "last_day": days[-1].isoformat(),
        "longest": longest,
        "longest_span": f"{(longest_end - timedelta(days=longest - 1)).isoformat()} to {longest_end.isoformat()}",
        "current": current,
    }


def _bar(count: int, peak: int, width: int = 40) -> str:
    return "█" * max(1 if count else 0, round(count / peak * width)) if peak else ""


def export_rhythms(conn: sqlite3.Connection, out_path, tz: str = DEFAULT_TZ) -> dict:
    rhythms = hourly_rhythms(conn, tz)
    streaks = activity_streaks(conn)
    lines = [
        "# Activity rhythms",
        "",
        f"Local time: {tz}. {rhythms['total_timed']:,} timestamped traces",
        "(date-only rows excluded).",
        "",
        "## Hour of day, all years",
        "",
    ]
    peak = max(rhythms["by_hour"]) if rhythms["by_hour"] else 0
    for hour, count in enumerate(rhythms["by_hour"]):
        lines.append(f"    {hour:02d}  {_bar(count, peak)} {count:,}")
    lines += ["", "## Day of week", ""]
    peak = max(rhythms["by_weekday"].values(), default=0)
    for day, count in rhythms["by_weekday"].items():
        lines.append(f"    {day}  {_bar(count, peak)} {count:,}")
    lines += ["", "## Night-owl index by month", "",
              "Share of activity between midnight and 5am. High months mean the",
              "sleep schedule slid.", ""]
    for month, share in rhythms["night_owl_by_month"].items():
        lines.append(f"    {month}  {_bar(round(share * 1000), 500, 25)} {share:.0%}")
    lines += [
        "",
        "## Streaks",
        "",
        f"- Active on {streaks['active_days']:,} distinct days "
        f"({streaks.get('first_day')} to {streaks.get('last_day')})",
        f"- Longest streak: {streaks['longest']} consecutive days ({streaks['longest_span']})",
        f"- Current streak ending {streaks.get('last_day')}: {streaks['current']} days",
        "",
    ]
    out_path.write_text("\n".join(lines) + "\n")
    return {"timed": rhythms["total_timed"], "longest_streak": streaks["longest"]}
