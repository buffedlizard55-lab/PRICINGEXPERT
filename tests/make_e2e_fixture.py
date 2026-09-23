#!/usr/bin/env python3
"""Deterministic synthetic 3-cycle e2e fixture for PRICINGEXPERT (scratch only).

Runs against an ISOLATED workdir (never the repo's data/): the workdir gets a copy
of scripts/ + data/strategies.json, then:
  data/season-2026 via the real new_season.py bootstrap, then staged:
    cycle-001 (in-season): SYN-A-1 (near-certain YES, tape +4c) + SYN-B-2 (taped +3.5c) with books
    cycle-002 (in-season): SYN-A-1 closed result=yes (official) + SYN-B-2 taped to 0.56
    cycle-003 (post-season, 2027-01-02): same active book — the desk must make NO new
    entries (board freezes at marks; settlements would still post if an official result landed)
  Plus the top-level research corpus: 40 hourly candles for SYN-A-1 + market payload.

Usage: python3 tests/make_e2e_fixture.py [workdir]   (default: a temp dir; printed on exit)
"""
import hashlib
import json
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def market(ticker, status="active", result="", yb=.88, ya=.90, nb=.10, na=.12,
           last=.89, prev=.85, vol=20000, vol24=8000):
    return {"market": {"ticker": ticker, "event_ticker": "SYN-EVT-" + ticker,
                       "title": f"synthetic {ticker}", "yes_sub_title": "up",
                       "no_sub_title": "down", "status": status, "result": result,
                       "rules_primary": "synthetic settlement rule",
                       "yes_bid_dollars": f"{yb:.4f}", "yes_ask_dollars": f"{ya:.4f}",
                       "no_bid_dollars": f"{nb:.4f}", "no_ask_dollars": f"{na:.4f}",
                       "yes_bid_size_fp": "5000", "no_bid_size_fp": "5000",
                       "last_price_dollars": f"{last:.4f}",
                       "previous_price_dollars": f"{prev:.4f}",
                       "volume_fp": f"{vol:.2f}", "volume_24h_fp": f"{vol24:.2f}",
                       "open_interest_fp": "1200", "liquidity_dollars": "9000.0000",
                       "open_time": "2026-08-31T12:00:00Z",
                       "close_time": "2026-12-31T22:00:00Z",
                       "expected_expiration_time": "2026-12-31T21:00:00Z",
                       "updated_time": "2026-09-21T14:00:00Z", "exchange_index": 0}}


def book(ticker, yes, no):
    return {"market_ticker": ticker, "orderbook_fp": {
        "yes_dollars": [[p, q] for p, q in yes],
        "no_dollars": [[p, q] for p, q in no]}}


def stage(work: Path, cycle: str, tid: str, url: str, payload: dict, note: str,
          fetched: str, ticker: str):
    c = work / "data" / "season-2026" / "forward" / cycle / "evidence"
    raw = json.dumps(payload, sort_keys=True).encode()
    (c / "raw").mkdir(parents=True, exist_ok=True)
    (c / "provenance").mkdir(parents=True, exist_ok=True)
    (c / "raw" / f"{tid}.json").write_bytes(raw)
    (c / "provenance" / f"{tid}.meta.json").write_text(json.dumps({
        "id": tid, "url": url, "fetched_at": fetched, "sha256": sha(raw),
        "bytes": len(raw), "http_status": 200, "tool": "e2e-fixture",
        "note": note, "ticker": ticker, "series": "SYN"}))


def main() -> int:
    work = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="px-e2e-"))
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    shutil.copytree(REPO / "scripts", work / "scripts")
    (work / "data").mkdir()
    shutil.copy(REPO / "data" / "strategies.json", work / "data" / "strategies.json")
    S = work / "data" / "season-2026"
    subprocess.run([sys.executable, str(work / "scripts" / "new_season.py"), "2026",
                    "--data", str(work / "data")], check=True, capture_output=True)
    (work / "data" / "universe.json").write_text(json.dumps(
        {"markets": ["SYN-A-1", "SYN-B-2"], "note": "e2e fixture universe (synthetic tickers)"},
        indent=1))

    def plan(cycle):
        (S / "forward" / cycle / "plan.json").write_text(json.dumps(
            {"cycle": cycle, "universe": {"markets": ["SYN-A-1", "SYN-B-2"]}}))

    # --- cycle 1 (in-season) ----------------------------------------------------
    stage(work, "cycle-001", "market-SYN-A-1", "synthetic://markets/SYN-A-1",
          market("SYN-A-1"), "kind:market ", "2026-09-21T14:00:00Z", "SYN-A-1")
    stage(work, "cycle-001", "book-SYN-A-1", "synthetic://markets/SYN-A-1/orderbook",
          book("SYN-A-1", yes=[(0.88, 3000), (0.87, 2000)], no=[(0.11, 3000), (0.10, 2000)]),
          "kind:orderbook", "2026-09-21T14:00:00Z", "SYN-A-1")
    stage(work, "cycle-001", "market-SYN-B-2", "synthetic://markets/SYN-B-2",
          market("SYN-B-2", yb=.50, ya=.51, nb=.49, na=.50, last=.505, prev=.47,
                 vol=5000, vol24=3000),
          "kind:market ", "2026-09-21T14:00:00Z", "SYN-B-2")
    stage(work, "cycle-001", "book-SYN-B-2", "synthetic://markets/SYN-B-2/orderbook",
          book("SYN-B-2", yes=[(0.50, 600), (0.49, 350), (0.48, 250)],
               no=[(0.50, 600), (0.51, 350), (0.52, 250)]),
          "kind:orderbook", "2026-09-21T14:00:00Z", "SYN-B-2")
    plan("cycle-001")

    # --- cycle 2 (in-season): SYN-A-1 closes YES officially, SYN-B-2 tapes up ----
    stage(work, "cycle-002", "market-SYN-A-1", "synthetic://markets/SYN-A-1",
          market("SYN-A-1", status="closed", result="yes", yb=1.0, ya=1.0, nb=0.0, na=0.0,
                 last=1.0, prev=.89),
          "kind:market ", "2026-09-21T14:20:00Z", "SYN-A-1")
    stage(work, "cycle-002", "market-SYN-B-2", "synthetic://markets/SYN-B-2",
          market("SYN-B-2", yb=.56, ya=.58, nb=.43, na=.45, last=.56, prev=.505,
                 vol=6500, vol24=4200),
          "kind:market ", "2026-09-21T14:20:00Z", "SYN-B-2")
    stage(work, "cycle-002", "book-SYN-B-2", "synthetic://markets/SYN-B-2/orderbook",
          book("SYN-B-2", yes=[(0.54, 300), (0.55, 400), (0.56, 700)],
               no=[(0.43, 300), (0.44, 400), (0.45, 600)]),
          "kind:orderbook", "2026-09-21T14:20:00Z", "SYN-B-2")
    plan("cycle-002")

    # --- cycle 3 (post-season 2027-01-02): same active book; no new entries ------
    stage(work, "cycle-003", "market-SYN-B-2", "synthetic://markets/SYN-B-2",
          market("SYN-B-2", yb=.56, ya=.58, nb=.43, na=.45, last=.56, prev=.505,
                 vol=6500, vol24=4200),
          "kind:market ", "2027-01-02T00:10:00Z", "SYN-B-2")
    stage(work, "cycle-003", "book-SYN-B-2", "synthetic://markets/SYN-B-2/orderbook",
          book("SYN-B-2", yes=[(0.54, 300), (0.55, 400), (0.56, 700)],
               no=[(0.43, 300), (0.44, 400), (0.45, 600)]),
          "kind:orderbook", "2027-01-02T00:10:00Z", "SYN-B-2")
    (S / "forward" / "cycle-003" / "plan.json").write_text(json.dumps(
        {"cycle": "cycle-003", "universe": {"markets": ["SYN-B-2"]}}))

    # --- top-level research corpus (candles + market, hash-bound) ----------------
    random.seed(7)
    raw_dir, prov_dir = work / "data" / "raw", work / "data" / "provenance"
    raw_dir.mkdir(parents=True, exist_ok=True)
    prov_dir.mkdir(parents=True, exist_ok=True)
    base = 1756704000  # 2026-09-01T00:00:00Z
    candles, p = [], 0.80
    for i in range(40):
        o = round(p, 4)
        c = round(min(0.97, max(0.03, p + random.uniform(-0.012, 0.014))), 4)
        h = round(max(o, c) + random.uniform(0, 0.008), 4)
        lo = round(min(o, c) - random.uniform(0, 0.008), 4)
        candles.append({"start_time": base + i * 3600, "open": o, "high": h, "low": lo,
                        "close": c, "volume": random.randint(500, 5000)})
        p = c
    payload = {"ticker": "SYN-A-1", "candles": candles}
    raw = json.dumps(payload, sort_keys=True).encode()
    (raw_dir / "candles-SYN-A-1.json").write_bytes(raw)
    (prov_dir / "candles-SYN-A-1.meta.json").write_text(json.dumps({
        "id": "candles-SYN-A-1", "url": "synthetic://series/SYN/candles/SYN-A-1",
        "fetched_at": "2026-09-21T14:00:00Z", "sha256": sha(raw), "bytes": len(raw),
        "http_status": 200, "tool": "e2e-fixture", "note": "kind:candlesticks",
        "ticker": "SYN-A-1", "series": "SYN"}))
    mraw = json.dumps(market("SYN-A-1"), sort_keys=True).encode()
    (raw_dir / "market-SYN-A-1.json").write_bytes(mraw)
    (prov_dir / "market-SYN-A-1.meta.json").write_text(json.dumps({
        "id": "market-SYN-A-1", "url": "synthetic://markets/SYN-A-1",
        "fetched_at": "2026-09-21T14:00:00Z", "sha256": sha(mraw), "bytes": len(mraw),
        "http_status": 200, "tool": "e2e-fixture", "note": "kind:market ",
        "ticker": "SYN-A-1", "series": "SYN"}))
    print(json.dumps({"workdir": str(work), "fixture": "ready", "cycles": 3,
                      "candles": len(candles)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
