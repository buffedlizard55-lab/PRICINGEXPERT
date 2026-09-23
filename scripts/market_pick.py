#!/usr/bin/env python3
"""Series contract selection policy — the ONE rule that turns a verified
series market list into the specific contracts a desk cycle may trade.

Used in two places, and the two MUST agree:
  * scripts/collect.py (kind:series_pick) — decides whose orderbooks to capture
  * scripts/forward_desk.py — re-derives the same tickers from the same
    committed payload; collect records its selection + the timestamp it used
    in the provenance meta, and scripts/verify.py re-checks both.

Policy (deterministic; no randomness, no network):
  candidate = market with
      status == "active"
      close_time (or expected_expiration_time) > now + close_margin_sec
      open_interest_fp > 0            (real positioning exists — every market
                                       with genuine volume in the verified
                                       plan-6 probe data carried oi > 0; the
                                       entire thin long tail has oi == 0)
  ranking  = open_interest_fp desc, then volume_24h_fp desc, then ticker asc
  take     = top_n (0 = discovery only: capture the list, pick nothing)

Rationale for oi-desc (verified 2026-09-23 from data/raw/probe-*.json): the
liquid markets (KXGOLDD strikes, KXTEMPMIAH/NYCHS/CHIHS/LAXHS, KXSILVERD,
KXDJI) rank in exactly open-interest order; volume_24h is a tiebreaker because
a fresh contract can carry high volume without open interest. close_margin
keeps the desk from filling a contract that will settle before the next
cycle can manage it (default 90 min = one full 30-min cycle + buffer).
"""
from __future__ import annotations

DEFAULT_MARGIN_SEC = 5400  # 90 minutes


def _ts(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        from paper_engine import parse_ts  # local import: keep module importable standalone
        t = parse_ts(value)
        return int(t) if t is not None else None
    except Exception:  # noqa: BLE001
        return None


def select_tickers(markets_payload: dict, now_ts: int,
                   close_margin_sec: int = DEFAULT_MARGIN_SEC,
                   top_n: int = 1) -> list[str]:
    """Pure function of (payload, now_ts, config) -> ordered ticker list."""
    if top_n <= 0:
        return []
    margin = int(close_margin_sec)
    cutoff = now_ts + margin
    ranked = []
    for m in markets_payload.get("markets", []):
        if m.get("status") != "active":
            continue
        close = _ts(m.get("close_time")) or _ts(m.get("expected_expiration_time"))
        if close is None or close <= cutoff:
            continue
        try:
            oi = float(m.get("open_interest_fp") or 0)
            v24 = float(m.get("volume_24h_fp") or 0)
        except (TypeError, ValueError):
            continue
        if oi <= 0:
            continue
        ranked.append((-oi, -v24, str(m.get("ticker") or ""), m.get("ticker")))
    ranked.sort(key=lambda t: (t[0], t[1], t[2]))
    return [t[3] for t in ranked[:top_n]]
