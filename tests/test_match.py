"""Offline tests for Phase 2 matching (embed / align_agent / confidence).

No model download, no API calls:
  - embeddings use a deterministic stub encoder
  - the alignment agent uses a fake client that COUNTS calls
Run: python tests/test_match.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.match.embed import candidate_pairs, embed_text, Candidate
from src.match.align_agent import (
    Alignment,
    align_candidates,
    parse_alignment,
)
from src.match.confidence import composite_confidence, partition, score_pair


# --- fixtures --------------------------------------------------------------

def _df():
    return pd.DataFrame([
        {"platform": "polymarket", "market_id": "pm1",
         "title": "BTC above 100k", "description": "Resolves yes if BTC > 100000.",
         "outcomes": [{"label": "Yes"}, {"label": "No"}],
         "prob_mid": 0.66},
        {"platform": "polymarket", "market_id": "pm2",
         "title": "Lakers win title", "description": "NBA championship.",
         "outcomes": [{"label": "Yes"}, {"label": "No"}],
         "prob_mid": 0.20},
        {"platform": "kalshi", "market_id": "ks1",
         "title": "Bitcoin over $100,000", "description": "Settles on year-end price.",
         "outcomes": [{"label": "Yes"}, {"label": "No"}],
         "prob_mid": 0.60},
    ], columns=["platform", "market_id", "title", "description", "outcomes", "prob_mid"])


def _stub_encoder(vectors):
    """Return an encoder that maps known texts to fixed unit vectors so we
    control cosine similarities deterministically."""
    def enc(texts):
        out = []
        for t in texts:
            v = None
            for needle, vec in vectors.items():
                if needle in t:
                    v = vec
                    break
            if v is None:
                v = [0.0, 0.0, 1.0]  # orthogonal "no match" direction
            arr = np.asarray(v, dtype=np.float32)
            out.append(arr / np.linalg.norm(arr))
        return np.vstack(out)
    return enc


class _FakeClient:
    """Stands in for anthropic.Anthropic; counts calls and returns canned JSON."""
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0
        self.messages = self  # so client.messages.create works

    def create(self, **kwargs):
        self.calls += 1
        block = type("B", (), {"text": self.payload})()
        return type("R", (), {"content": [block]})()


# --- embeddings ------------------------------------------------------------

def test_embed_text_uses_first_sentence():
    assert embed_text("Title", "First sentence. Second one.") == "Title. First sentence."
    assert embed_text("Title", "") == "Title"


def test_candidate_pairs_threshold():
    # BTC titles share a direction (high cosine); Lakers is orthogonal.
    enc = _stub_encoder({
        "BTC": [1.0, 0.0, 0.0], "Bitcoin": [0.97, 0.24, 0.0],
        "Lakers": [0.0, 1.0, 0.0],
    })
    cands = candidate_pairs(_df(), encoder=enc, threshold=0.60)
    keys = {(c.pm_market_id, c.kalshi_market_id) for c in cands}
    assert ("pm1", "ks1") in keys           # BTC pair clears threshold
    assert ("pm2", "ks1") not in keys        # Lakers vs Bitcoin does not
    assert all(c.embedding_sim >= 0.60 for c in cands)


# --- alignment JSON parsing -----------------------------------------------

def test_parse_clean_json():
    a = parse_alignment('{"same_event": true, "outcome_map": [], '
                         '"inverted": false, "resolution_match_score": 0.9, "notes": "x"}')
    assert a and a.same_event and abs(a.resolution_match_score - 0.9) < 1e-9


def test_parse_fenced_json():
    a = parse_alignment('```json\n{"same_event": false, "resolution_match_score": 0.1}\n```')
    assert a and a.same_event is False


def test_parse_prose_wrapped_json():
    a = parse_alignment('Sure! {"same_event": true, "resolution_match_score": 0.5} done')
    assert a and a.same_event is True


def test_parse_garbage_returns_none():
    assert parse_alignment("not json at all") is None
    assert parse_alignment("") is None


# --- caching: the key cost-control acceptance (§5.2) -----------------------

def test_cache_prevents_recall():
    enc = _stub_encoder({"BTC": [1.0, 0.0, 0.0], "Bitcoin": [0.97, 0.24, 0.0],
                         "Lakers": [0.0, 1.0, 0.0]})
    cands = candidate_pairs(_df(), encoder=enc, threshold=0.60)
    client = _FakeClient('{"same_event": true, "resolution_match_score": 0.9}')

    # first run: nothing cached -> one call per candidate
    first = align_candidates(client, cands, cached_keys=set())
    assert client.calls == len(cands) and len(first) == len(cands)

    # second run: all keys cached -> ZERO new calls
    calls_before = client.calls
    second = align_candidates(client, cands, cached_keys=set(first.keys()))
    assert client.calls == calls_before  # no re-call for cached pairs
    assert second == {}
    print(f"  candidates={len(cands)}  first-run calls={calls_before}  "
          f"second-run calls={client.calls - calls_before}")


# --- confidence ------------------------------------------------------------

def test_confidence_formula_and_partition():
    high_align = Alignment(True, [], False, 0.95, "")   # strong resolution match
    low_align = Alignment(True, [], False, 0.10, "")    # weak resolution match
    not_same = Alignment(False, [], False, 0.0, "")

    # 0.5*0.92 + 0.3*0.95 + 0.2*1 = 0.945 -> high
    assert abs(composite_confidence(0.92, high_align) - 0.945) < 1e-6

    c = Candidate("pm1", "ks1", "", "", "", "", [], [], embedding_sim=0.92)
    c_low = Candidate("pm2", "ks2", "", "", "", "", [], [], embedding_sim=0.62)
    c_no = Candidate("pm3", "ks3", "", "", "", "", [], [], embedding_sim=0.90)

    buckets = partition([
        score_pair(c, high_align),
        score_pair(c_low, low_align),
        score_pair(c_no, not_same),
    ])
    assert len(buckets["high_confidence"]) == 1
    assert len(buckets["review"]) == 1
    assert len(buckets["rejected"]) == 1
    print(f"  high={len(buckets['high_confidence'])} "
          f"review={len(buckets['review'])} rejected={len(buckets['rejected'])}")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\nAll {len(fns)} tests passed.")
