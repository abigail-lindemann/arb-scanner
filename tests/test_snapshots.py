"""Offline tests for Phase 5 snapshots/alerts (§8).

No DB, no SMTP: detect_alerts is pure, and dispatch uses a capturing send_fn.
Run: python tests/test_snapshots.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ml.snapshots import (
    Alert,
    PairSnapshot,
    detect_alerts,
    dispatch_alerts,
)


def _snap(pair_id, net, title="Evt"):
    return PairSnapshot(
        pair_id=pair_id, category="politics", pm_mid=0.5, kalshi_mid=0.5,
        gross_spread=net + 0.02, net_spread=net, title=title,
        link_pm="http://pm", link_kalshi="http://k",
    )


def test_jump_over_threshold_alerts():
    snaps = [_snap(1, 0.13)]            # was 0.02 -> +0.11
    prior = {1: 0.02}
    alerts = detect_alerts(snaps, prior)
    assert len(alerts) == 1
    a = alerts[0]
    assert abs(a.delta - 0.11) < 1e-9 and a.old_net == 0.02 and a.new_net == 0.13


def test_small_move_no_alert():
    # exactly the dedup case: prior is already the post-jump value
    snaps = [_snap(1, 0.13)]
    prior = {1: 0.13}
    assert detect_alerts(snaps, prior) == []
    # sub-threshold drift
    assert detect_alerts([_snap(1, 0.07)], {1: 0.02}) == []


def test_new_pair_not_alerted():
    # no prior snapshot -> no move to measure
    assert detect_alerts([_snap(99, 0.40)], {}) == []


def test_downward_move_alerts():
    alerts = detect_alerts([_snap(2, 0.01)], {2: 0.15})  # -0.14
    assert len(alerts) == 1 and alerts[0].delta < 0
    assert alerts[0].subject.startswith("\u26a0")


def test_dispatch_uses_send_fn():
    sent = []
    def fake_send(subject, html):
        sent.append((subject, html))
    alerts = detect_alerts([_snap(1, 0.13, "BTC 100k")], {1: 0.02})
    n = dispatch_alerts(alerts, fake_send)
    assert n == 1 and len(sent) == 1
    subject, html = sent[0]
    assert "BTC 100k" in subject and "net spread" in html
    print(f"  subject: {subject}")


def test_dispatch_failsoft():
    def boom(subject, html):
        raise RuntimeError("smtp down")
    alerts = detect_alerts([_snap(1, 0.13)], {1: 0.02})
    # a failing send must not raise
    assert dispatch_alerts(alerts, boom) == 0


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\nAll {len(fns)} tests passed.")
