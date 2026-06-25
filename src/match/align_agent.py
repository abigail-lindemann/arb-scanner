"""LLM alignment agent (Claude Haiku). §5.2.

For each embedding candidate, ask Haiku whether the two markets resolve on
the SAME real-world event, how their outcomes map, whether the relationship
is inverted (PM-Yes ~ Kalshi-No), and how well their resolution criteria
agree. The model must return JSON only.

Cost control (§5.2 acceptance): results are cached by
(pm_market_id, kalshi_market_id) in matched_pairs. On later runs we only
call Haiku for pairs not already cached -- align_candidates takes a set of
already-aligned keys and a fresh call counter to make that verifiable.

The Anthropic client is injected so this module imports without the SDK and
is fully unit-testable with a fake client.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Iterable

from .embed import Candidate

log = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5"
_MAX_TOKENS = 400

_SYSTEM = (
    "You align prediction markets across two platforms (Polymarket and Kalshi). "
    "Decide whether two markets resolve on the SAME real-world outcome, map their "
    "outcomes to each other, detect inversion (Polymarket-Yes corresponding to "
    "Kalshi-No), and judge how closely their resolution criteria, dates, and data "
    "sources agree. Respond with a single JSON object and nothing else -- no prose, "
    "no markdown fences."
)

_SCHEMA_HINT = (
    '{"same_event": true, '
    '"outcome_map": [{"polymarket": "Yes", "kalshi": "Yes"}], '
    '"inverted": false, '
    '"resolution_match_score": 0.0, '
    '"notes": "short reason"}'
)


@dataclass
class Alignment:
    same_event: bool
    outcome_map: list[dict[str, str]]
    inverted: bool
    resolution_match_score: float
    notes: str

    @property
    def is_valid(self) -> bool:
        return 0.0 <= self.resolution_match_score <= 1.0


def build_prompt(c: Candidate) -> str:
    """User-message content for one candidate pair."""
    return (
        "Polymarket market:\n"
        f"  title: {c.pm_title}\n"
        f"  description: {c.pm_desc[:1500]}\n"
        f"  outcomes: {[o.get('label') for o in c.pm_outcomes]}\n\n"
        "Kalshi market:\n"
        f"  title: {c.kalshi_title}\n"
        f"  description: {c.kalshi_desc[:1500]}\n"
        f"  outcomes: {[o.get('label') for o in c.kalshi_outcomes]}\n\n"
        "Return JSON exactly in this shape (values are examples):\n"
        f"{_SCHEMA_HINT}"
    )


def _extract_text(response: Any) -> str:
    """Pull concatenated text from an Anthropic Messages response object."""
    parts = []
    for block in getattr(response, "content", []) or []:
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def parse_alignment(text: str) -> Alignment | None:
    """Parse the model's JSON, tolerating accidental markdown fences.

    Returns None on anything unparseable -- the caller treats that as a
    failed alignment (fail-soft, §0.5) rather than crashing the run.
    """
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned[:4].lower() == "json":
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    # last-resort: slice the outermost braces
    if not cleaned.startswith("{"):
        lo, hi = cleaned.find("{"), cleaned.rfind("}")
        if lo == -1 or hi == -1 or hi < lo:
            log.warning("alignment response had no JSON object: %r", text[:120])
            return None
        cleaned = cleaned[lo:hi + 1]
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        log.warning("alignment JSON parse failed (%s): %r", e, cleaned[:120])
        return None
    try:
        return Alignment(
            same_event=bool(data["same_event"]),
            outcome_map=list(data.get("outcome_map", []) or []),
            inverted=bool(data.get("inverted", False)),
            resolution_match_score=float(data.get("resolution_match_score", 0.0)),
            notes=str(data.get("notes", "")),
        )
    except (KeyError, TypeError, ValueError) as e:
        log.warning("alignment payload missing/invalid fields (%s): %r", e, data)
        return None


def align_candidate(client: Any, c: Candidate) -> Alignment | None:
    """One Haiku call for one candidate. Fail-soft on API/parse errors."""
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM,
            messages=[{"role": "user", "content": build_prompt(c)}],
        )
    except Exception as e:  # network / API error -> skip this pair
        log.warning("Haiku call failed for (%s,%s): %s",
                    c.pm_market_id, c.kalshi_market_id, e)
        return None
    return parse_alignment(_extract_text(resp))


def align_candidates(
    client: Any,
    candidates: Iterable[Candidate],
    cached_keys: set[tuple[str, str]] | None = None,
) -> dict[tuple[str, str], Alignment]:
    """Align only the candidates NOT already cached.

    `cached_keys` is the set of (pm_market_id, kalshi_market_id) already in
    matched_pairs. Returns alignments for the freshly-called pairs only;
    cached pairs are skipped entirely (no Haiku call), which is what keeps
    ongoing LLM cost near zero (§5.2 acceptance).
    """
    cached_keys = cached_keys or set()
    results: dict[tuple[str, str], Alignment] = {}
    for c in candidates:
        key = (c.pm_market_id, c.kalshi_market_id)
        if key in cached_keys:
            continue
        a = align_candidate(client, c)
        if a is not None:
            results[key] = a
    return results
