"""AI spend ledger and monthly budget guard.

Every interactive API call gets recorded (tokens -> estimated dollars) in
the ai_spend table, and tracked_create refuses to start a call once the
month's estimated spend crosses the configured budget. Estimates lean
slightly high (cache writes billed at 1.25x, reads at 0.1x) so the guard
trips before the real balance does.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

# $ per million tokens (input, output), keyed by model prefix
_PRICING = {
    "claude-sonnet": (3.0, 15.0),
    "claude-opus": (5.0, 25.0),
    "claude-haiku": (1.0, 5.0),
    "claude-fable": (25.0, 125.0),
}
_DEFAULT_PRICE = (3.0, 15.0)


class BudgetExceeded(RuntimeError):
    def __init__(self, spent: float, budget: float) -> None:
        super().__init__(
            f"Monthly AI budget reached (estimated ${spent:.2f} of ${budget:.2f}). "
            "Raise ai_monthly_budget_usd in config.yaml to keep going."
        )
        self.spent = spent
        self.budget = budget


def _price(model: str) -> tuple[float, float]:
    return next((v for k, v in _PRICING.items() if model.startswith(k)), _DEFAULT_PRICE)


def estimate_usd(model: str, usage) -> float:
    """Estimated dollars for one response's usage block."""
    in_price, out_price = _price(model)
    input_tokens = getattr(usage, "input_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    return (
        input_tokens / 1e6 * in_price
        + cache_write / 1e6 * in_price * 1.25
        + cache_read / 1e6 * in_price * 0.10
        + output_tokens / 1e6 * out_price
    )


def record_usage(conn: sqlite3.Connection, model: str, context: str, usage) -> float:
    cost = estimate_usd(model, usage)
    conn.execute(
        """INSERT INTO ai_spend(at, model, context, input_tokens, output_tokens,
                                cache_creation, cache_read, cost_usd)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            datetime.now(timezone.utc).isoformat(), model, context,
            getattr(usage, "input_tokens", 0) or 0,
            getattr(usage, "output_tokens", 0) or 0,
            getattr(usage, "cache_creation_input_tokens", 0) or 0,
            getattr(usage, "cache_read_input_tokens", 0) or 0,
            cost,
        ),
    )
    conn.commit()
    return cost


def month_spend(conn: sqlite3.Connection) -> float:
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    row = conn.execute(
        "SELECT coalesce(sum(cost_usd), 0) c FROM ai_spend WHERE at LIKE ?",
        (f"{month}%",),
    ).fetchone()
    return float(row["c"])


def tracked_create(conn: sqlite3.Connection, cfg, client, context: str, **kwargs):
    """messages.create with budget check before and ledger write after.

    cfg=None skips the budget check (tiny background calls like session
    summaries) but still records the spend.
    """
    if cfg is not None:
        budget = getattr(cfg, "ai_monthly_budget_usd", 0) or 0
        if budget > 0:
            spent = month_spend(conn)
            if spent >= budget:
                raise BudgetExceeded(spent, budget)
    response = client.messages.create(**kwargs)
    model = kwargs.get("model", "unknown")
    try:
        record_usage(conn, model, context, response.usage)
    except Exception:
        pass  # a ledger hiccup must never eat a paid response
    return response
