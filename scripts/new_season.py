#!/usr/bin/env python3
"""Bootstrap a competition season directory (calendar year = one competition).

Usage: python3 scripts/new_season.py 2026 [--data data]

Creates:
  data/season-2026/competition.json   season config (UTC span, capital, rules)
  data/season-2026/forward/state.json initial desk state (one entry per strategy)
  data/season-2026/forward/*.jsonl    empty append-only ledgers
  data/season-2026/backtest/          (directory)

The competition clock is UTC; the season runs from Jan 1 00:00Z to the next
Jan 1 00:00Z, mirroring the calendar-year competition model used by public
paper-trading competitions (one season per year).
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from paper_engine import STARTING_CASH


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("year", type=int)
    ap.add_argument("--data", default="data")
    args = ap.parse_args()
    data = Path(args.data)
    year = args.year
    season = data / f"season-{year}"
    forward = season / "forward"
    forward.mkdir(parents=True, exist_ok=True)
    (season / "backtest").mkdir(parents=True, exist_ok=True)

    strategies = json.loads((data / "strategies.json").read_text(encoding="utf-8"))
    start_ts = int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp())
    end_ts = int(datetime(year + 1, 1, 1, tzinfo=timezone.utc).timestamp())
    competition = {
        "season": str(year),
        "start_ts": start_ts,
        "end_ts": end_ts,
        "start": f"{year}-01-01T00:00:00Z",
        "end": f"{year + 1}-01-01T00:00:00Z",
        "timezone": "UTC",
        "starting_cash": STARTING_CASH,
        "objective": "highest return (risk management is deliberately NOT a criterion; "
                     "the board ranks raw return)",
        "scoring": "equity = cash + open positions marked at the captured best bid "
                   "(last-trade mark only when no bid exists, flagged mark_kind)",
        "fill_model": "every forward fill consumes a captured official order book "
                      "(depth-bounded, limit-bounded); fees use the exact Kalshi "
                      "quadratic formula; slippage = (vwap - touch) * contracts",
        "settlement": "official market `result` field only ($1/$0, no settlement fee)",
        "season_rollover": "at season end all open positions are marked and the final "
                           "board is frozen; the next season starts fresh state with the "
                           "same strategies",
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    (season / "competition.json").write_text(json.dumps(competition, indent=1) + "\n",
                                             encoding="utf-8")
    state = {
        "season": str(year),
        "starting_cash": STARTING_CASH,
        "cycles_completed": 0,
        "strategies": {s["id"]: {"cash": STARTING_CASH, "positions": {},
                                 "realized_pnl": 0.0, "fees_paid": 0.0, "slippage_paid": 0.0,
                                 "wins": 0, "losses": 0, "fills": 0, "exits": 0,
                                 "settlements": 0} for s in strategies},
    }
    (forward / "state.json").write_text(json.dumps(state, indent=1, sort_keys=True) + "\n",
                                        encoding="utf-8")
    for name in ("trades.jsonl", "intents.jsonl", "marks.jsonl"):
        p = forward / name
        if not p.exists():
            p.write_text("", encoding="utf-8")
    print(json.dumps({"season": str(year), "strategies": len(strategies),
                      "start": competition["start"], "end": competition["end"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
