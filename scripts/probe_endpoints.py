#!/usr/bin/env python3
"""Probe the official Kalshi endpoints this project relies on (runs in CI, which
has network egress). Each probe records HTTP status + the presence of the fields
the engine consumes. A failing probe is a hard error for the publish job — the
site must never claim an endpoint it cannot verify.
Writes data/site/endpoint-probe.json.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kalshi_client import KalshiClient, KalshiError  # noqa: E402


def main() -> int:
    data = Path(sys.argv[1] if len(sys.argv) > 1 else "data")
    client = KalshiClient()
    results = []
    ok_all = True

    def record(name, fn, required_fields_of_first: list[str] | None = None):
        nonlocal ok_all
        entry = {"name": name, "status": None, "ok": False,
                 "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        try:
            payload, raw, url = fn()
            entry["status"] = 200
            entry["url"] = url
            entry["bytes"] = len(raw)
            if required_fields_of_first:
                obj = payload
                if isinstance(payload, dict):
                    for key in ("market", "orderbook", "orderbook_fp", "candles", "trades",
                                "markets", "series"):
                        if key in payload and isinstance(payload[key], (dict, list)):
                            obj = payload[key]
                            break
                if isinstance(obj, dict):
                    entry["missing_fields"] = [f for f in required_fields_of_first
                                               if f not in obj]
                elif isinstance(obj, list) and obj:
                    entry["missing_fields"] = [f for f in required_fields_of_first
                                               if f not in obj[0]]
                    entry["sample"] = {k: obj[0].get(k) for k in required_fields_of_first[:8]}
                else:
                    entry["missing_fields"] = list(required_fields_of_first)
            else:
                entry["missing_fields"] = []
            entry["ok"] = True
        except KalshiError as e:
            entry["error"] = str(e)[:300]
            entry["ok"] = False
        except Exception as e:  # noqa: BLE001
            entry["error"] = f"{type(e).__name__}: {e}"[:300]
            entry["ok"] = False
        ok_all = ok_all and entry["ok"]
        results.append(entry)

    record("exchange/status", lambda: client.get("exchange/status"),
           ["exchange_active", "trading_active"])
    record("historical/cutoff", lambda: client.get("historical/cutoff"), None)
    record("series (list)", lambda: client.get("series", {"limit": 5}), None)
    record("markets (list)", lambda: client.get("markets", {"limit": 3}),
           ["ticker", "status", "close_time", "yes_bid_dollars", "yes_ask_dollars",
            "no_bid_dollars", "no_ask_dollars", "volume_fp", "previous_price_dollars"])
    # single market / book / candles / trades probes use the first live market
    try:
        first, raw, url = client.get("markets", {"status": "active", "limit": 3})
        markets = [m for m in first.get("markets", [])
                   if m.get("status") == "active" and m.get("ticker")]
        if markets:
            ticker = markets[0]["ticker"]
            series = ticker.split("-")[0]
            record(f"markets/{ticker}", lambda: client.market(ticker),
                   ["ticker", "yes_bid_dollars", "close_time", "rules_primary"])
            record(f"markets/{ticker}/orderbook",
                   lambda: client.orderbook(ticker, depth=10), None)
            record("markets/trades (tape)",
                   lambda: client.trades(ticker=ticker, limit=10),
                   ["trade_ts", "price_dollars", "quantity_fp"])
            import time as _t
            ts = int(_t.time())

            def ts_of(iso_str):
                from datetime import datetime as _dt
                return int(_dt.fromisoformat(iso_str.replace("Z", "+00:00")).timestamp())

            st = ts_of(markets[0]["open_time"]) if markets[0].get("open_time") else ts - 86400
            record(f"candlesticks {ticker}",
                   lambda: client.candlesticks(series, ticker, st, ts, 3600), None)
        else:
            results.append({"name": "single-market probes", "ok": False,
                            "error": "no active market returned by the list probe"})
            ok_all = False
    except Exception as e:  # noqa: BLE001
        results.append({"name": "single-market probes", "ok": False,
                        "error": f"{type(e).__name__}: {e}"[:300]})
        ok_all = False

    out = data / "site" / "endpoint-probe.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"probed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                               "all_ok": ok_all, "results": results}, indent=1) + "\n",
                   encoding="utf-8")
    print(json.dumps({"all_ok": ok_all,
                      "results": [(r["name"], r["ok"], r.get("status")) for r in results]}))
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
