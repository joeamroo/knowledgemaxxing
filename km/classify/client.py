"""Claude API wrapper for classification passes.

Privacy: only tweet/post text and titles are ever sent, never file paths
or identity metadata. Paid runs print an estimate and require explicit
confirmation before any call is made.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

# $ per million tokens, keyed by model prefix (fallback: sonnet pricing)
_PRICING = {
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-opus": (5.0, 25.0),
    "claude-haiku": (1.0, 5.0),
}


@dataclass
class CostEstimate:
    item_count: int
    batch_count: int
    est_input_tokens: int
    est_output_tokens: int
    est_dollars: float

    def describe(self) -> str:
        return (
            f"{self.item_count} items in {self.batch_count} batches, "
            f"~{self.est_input_tokens:,} input + ~{self.est_output_tokens:,} output tokens, "
            f"estimated cost ${self.est_dollars:.2f}"
        )


def estimate_cost(texts: list[str], prompt_overhead_chars: int, model: str, batch_size: int) -> CostEstimate:
    total_chars = sum(len(t) for t in texts)
    batch_count = max(1, -(-len(texts) // batch_size))
    input_tokens = (total_chars + batch_count * prompt_overhead_chars) // 4
    output_tokens = len(texts) * 30  # one compact JSON line per item
    in_price, out_price = next(
        (v for k, v in _PRICING.items() if model.startswith(k)), (3.0, 15.0)
    )
    dollars = input_tokens / 1e6 * in_price + output_tokens / 1e6 * out_price
    return CostEstimate(len(texts), batch_count, input_tokens, output_tokens, dollars)


def strip_code_fences(text: str) -> str:
    text = text.strip()
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    return match.group(1) if match else text


def parse_json_response(text: str):
    """Parse a model response that should be JSON, defensively."""
    cleaned = strip_code_fences(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # last resort: find the outermost JSON array or object
    for pattern in (r"\[.*\]", r"\{.*\}"):
        match = re.search(pattern, cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                continue
    return None


def get_client():
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError("classification needs the ai extras: uv sync --extra ai") from exc
    return anthropic.Anthropic(max_retries=4)


def call_claude(client, model: str, system: str, user: str, max_tokens: int = 8000) -> str:
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(block.text for block in response.content if block.type == "text")
