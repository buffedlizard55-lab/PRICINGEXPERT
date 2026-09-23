#!/usr/bin/env python3
"""Generate the collect plan for the next forward-desk cycle.

Reads data/universe.json and writes the plan to the cycle's evidence directory:
  data/season-2026/forward/cycle-NNN/plan.json

Every universe market gets a market + orderbook read (the captured books are the
ONLY price evidence the desk may fill against). Settlement re-checks for recently
closed universe markets ride along so settlements can be verified against the
official API after the fact.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    data = Path(sys.argv[1] if len(sys.argv) > 1 else "data")
    season = data / "season-2026" / "forward"
    season.mkdir(parents=True, exist_ok=True)
    state = json.loads((season / "state.json").read_text(encoding="utf-8")) \
        if (season / "state.json").exists() else {}
    n = int(state.get("cycles_completed", 0)) + 1
    cycle_name = f"cycle-{n:03d}"
    cycle_dir = season / cycle_name
    (cycle_dir / "evidence" / "raw").mkdir(parents=True, exist_ok=True)
    (cycle_dir / "evidence" / "provenance").mkdir(parents=True, exist_ok=True)

    universe = json.loads((data / "universe.json").read_text(encoding="utf-8"))
    endpoints = [
        {"id": "cycle-exchange-status", "kind": "exchange_status",
         "note": "kind:exchange_status — liveness probe, stored for the cycle record"},
        {"id": "cycle-historical-cutoff", "kind": "historical_cutoff",
         "note": "kind:historical_cutoff — data availability boundary for backtests"},
    ]
    for cfg in universe.get("series", []):
        s = cfg["series"]
        endpoints.append({"id": f"pick-{s}", "kind": "series_pick",
                          "params": {"series_ticker": s, "limit": 50,
                                     "top_n": cfg.get("top_n", 1),
                                     "close_margin_sec": cfg.get("close_margin_sec", 5400),
                                     "depth": 50},
                          "note": f"kind:series_pick — rolling contract selection for series {s} "
                                  "(policy: scripts/market_pick.py; selection recorded in the "
                                  "markets-list meta and re-derived by desk + verify)"})
    for t in universe.get("markets", []):
        endpoints.append({"id": f"market-{t}", "kind": "market",
                          "params": {"ticker": t},
                          "note": f"kind:market — market object for {t}"})
        endpoints.append({"id": f"book-{t}", "kind": "orderbook",
                          "params": {"ticker": t, "depth": 50},
                          "note": f"kind:orderbook — captured book for {t} (fill evidence)"})
    for t in universe.get("recent_closed", []):
        endpoints.append({"id": f"settlecheck-{t}", "kind": "market",
                          "params": {"ticker": t},
                          "note": f"kind:settlecheck — settlement re-read for {t}"})
    plan = {"plan_version": 3, "cycle": cycle_name,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "endpoints": endpoints}
    (cycle_dir / "plan.json").write_text(json.dumps(plan, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"cycle": cycle_name, "endpoints": len(endpoints)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
