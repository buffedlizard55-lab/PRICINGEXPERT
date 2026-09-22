#!/usr/bin/env python3
"""Backtest engine: replay strategy rules over committed, hash-bound candle data.

Price model (documented, conservative, no look-ahead):
  * Candle close c is the YES price. A YES order executes at the next bar's open
    O; a NO order executes at 1 - O. A signal is computed on bar t's close and may
    only execute at bar t+1.
  * Buy limits: YES limit = c_t + buffer; NO limit = (1 - c_t) + buffer, each
    capped at 0.99. The touch (for slippage) is the same proxy without the cap:
    c_t for YES, 1 - c_t for NO. A backtest fill is "candle-proxied", never
    "book-verified" — that distinction is shown on the site.
  * Fees: the exact Kalshi quadratic formula at each executed side price.
  * Exits: a target is checked against side prices on bar closes and executed at
    the next bar's open (YES at O, NO at 1-O). No target => settlement at the
    OFFICIAL result from the committed market payload ($1/$0, no settlement fee).
  * Sizing: whole contracts, 25% of the $10,000 start per position, cash >= 0.
  * Walk-forward: first 60% of bars = development, last 40% = validation;
    parameters are fixed in data/strategies.json and never refit between windows.

Pre-registered verdicts (no post-hoc storytelling):
  supported    validation PnL > 0 and validation trades >= 5 (net of fees)
  refuted      validation PnL < 0 and validation trades >= 5
  inconclusive  anything else (including < 5 validation trades)
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paper_engine import STARTING_CASH, parse_ts, taker_fee  # noqa: E402

EPS = 1e-9
CASH_FRACTION = 0.25


def load_candles(csv_path: Path) -> list[dict]:
    rows = []
    with csv_path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            rows.append({
                "ts": int(row["ts"]),
                "open": float(row["open"]), "high": float(row["high"]),
                "low": float(row["low"]), "close": float(row["close"]),
                "volume": float(row["volume"] or 0),
                "provenance": row["provenance"], "sha256": row["sha256"],
            })
    rows.sort(key=lambda r: r["ts"])
    return rows


def market_summary(payload: dict) -> dict:
    m = payload.get("market", payload)
    return {
        "ticker": m.get("ticker"),
        "title": m.get("title"),
        "result": (m.get("result") or "").lower() or None,
        "status": m.get("status"),
        "open_ts": parse_ts(m.get("open_time")),
        "close_ts": parse_ts(m.get("close_time")),
        "rules_primary": m.get("rules_primary"),
        "volume": float(m.get("volume_fp") or 0),
    }


def side_price(close_yes: float, open_yes: float, side: str) -> tuple[float, float]:
    """(touch, exec_price) for a side given bar t close and bar t+1 open (YES prices)."""
    touch = close_yes if side == "yes" else 1.0 - close_yes
    exec_price = open_yes if side == "yes" else 1.0 - open_yes
    return touch, exec_price


def signal_for(strat: dict, prev_close: float, close: float, hours_to_expiry: float | None,
               volume: float, min_age_h: float) -> tuple[str | None, dict]:
    """(side, trigger) or (None, {}) using only information available at `close`."""
    p = strat["params"]
    sid = strat["id"]
    move = round(close - prev_close, 4)
    if sid == "sure-penny":
        if volume < p["min_volume"] or close < 0.5:
            return None, {}
        if p["ask_low"] - 0.02 <= close <= p["ask_high"]:
            return "yes", {"close": close, "prev_close": prev_close, "volume": volume}
        return None, {}
    if sid == "longshot-fader":
        if volume < p.get("min_volume_24h", 0):
            return None, {}
        if p["longshot_ask_low"] <= close <= p["longshot_ask_high"]:
            return "no", {"close": close, "note": "fade-longshot-yes-side"}
        if p["longshot_ask_low"] <= (1 - close) <= p["longshot_ask_high"]:
            return "yes", {"close": close, "note": "fade-longshot-no-side"}
        return None, {}
    if sid in ("last-mile", "macro-closer"):
        if hours_to_expiry is None or hours_to_expiry > p["horizon_hours"] or hours_to_expiry <= 0:
            return None, {}
        if close >= 0.5 and p["ask_low"] <= close <= p["ask_high"]:
            return "yes", {"close": close, "hours_to_expiry": hours_to_expiry}
        if close < 0.5 and p["ask_low"] <= (1 - close) <= p["ask_high"]:
            return "no", {"close": close, "hours_to_expiry": hours_to_expiry}
        return None, {}
    if sid == "commodity-drift":
        if hours_to_expiry is None or hours_to_expiry > p["horizon_hours"] or hours_to_expiry <= 0:
            return None, {}
        if close >= p["min_probability"]:
            return "yes", {"close": close, "hours_to_expiry": hours_to_expiry}
        if 1 - close >= p["min_probability"]:
            return "no", {"close": close, "hours_to_expiry": hours_to_expiry}
        return None, {}
    if sid == "tape-rider":
        if min_age_h < 24 or abs(move) < p["min_move"]:
            return None, {}
        side = "yes" if move > 0 else "no"
        return side, {"close": close, "prev_close": prev_close, "move": move}
    if sid == "deep-fade":
        if abs(move) < p["min_move"]:
            return None, {}
        if move < 0 and close <= 0.45:
            return "yes", {"close": close, "move": move, "note": "fade-the-drop"}
        if move > 0 and close >= 0.55:
            return "no", {"close": close, "move": move, "note": "fade-the-run"}
        return None, {}
    raise SystemExit(f"no candle rule for {sid!r}")


def backtest_strategy(strat: dict, candles: list[dict], market: dict) -> dict:
    p = strat["params"]
    buffer = p.get("limit_buffer", 0.02)
    use_expiry = "hours_to_expiry" in p
    exp_ts = market.get("close_ts")
    trades: list[dict] = []
    skips: list[dict] = []
    cash = STARTING_CASH
    held: dict | None = None
    for i in range(1, len(candles)):
        bar, nxt = candles[i - 1], candles[i]
        if held is None:
            if use_expiry:
                hte = None if not exp_ts else (exp_ts - bar["ts"]) / 3600
            else:
                hte = None
            min_age_h = ((bar["ts"] - market["open_ts"]) / 3600) if market.get("open_ts") else 999.0
            side, trigger = signal_for(strat, candles[i - 2]["close"] if i >= 2 else bar["open"],
                                       bar["close"], hte, market.get("volume", 0), min_age_h)
            if side is None:
                continue
            touch, exec_price = side_price(bar["close"], nxt["open"], side)
            limit = min(0.99, touch + buffer)
            if exec_price > limit + EPS or exec_price >= 1 or exec_price <= 0:
                skips.append({"at": nxt["ts"], "side": side, "reason": "open-worse-than-limit",
                              "exec_price": round(exec_price, 4), "limit": round(limit, 4)})
                continue
            price = round(exec_price, 4)
            budget = cash * CASH_FRACTION
            q = 0
            for cand in range(int(budget / price) + 1, 0, -1):
                if cand * price + taker_fee(price, cand) <= budget + EPS:
                    q = cand
                    break
            if q <= 0:
                skips.append({"at": nxt["ts"], "side": side, "reason": "unaffordable"})
                continue
            fee = taker_fee(price, q)
            cash = round(cash - q * price - fee, 6)
            held = {"entry_ts": nxt["ts"], "side": side, "contracts": q, "price": price,
                    "touch": round(touch, 4), "fee": fee,
                    "slippage": round((price - touch) * q, 6), "trigger": trigger,
                    "bar_provenance": bar["provenance"], "bar_sha256": bar["sha256"],
                    "limit": round(limit, 4)}
        else:
            close_yes = bar["close"]
            side_close = close_yes if held["side"] == "yes" else 1.0 - close_yes
            entry = held["price"]
            multiple = p.get("exit_multiple")
            target_over = p.get("exit_target_over_entry")
            hit = (multiple and side_close >= entry * multiple) or \
                  (target_over and side_close >= entry + target_over)
            if hit and i + 1 < len(candles):
                _, exit_price = side_price(bar["close"], candles[i + 1]["open"], held["side"])
                exit_price = min(max(exit_price, 0.01), 0.99)
                fee = taker_fee(exit_price, held["contracts"])
                proceeds = round(held["contracts"] * exit_price - fee, 6)
                cash = round(cash + proceeds, 6)
                pnl = round(proceeds - held["contracts"] * entry - held["fee"], 6)
                trades.append({**held, "exit_ts": candles[i + 1]["ts"],
                               "exit_price": round(exit_price, 4), "exit_fee": fee,
                               "pnl": pnl, "exit_slippage": round((exit_price - side_close) * held["contracts"], 6),
                               "exit_reason": "target"})
                held = None
            elif market.get("status") == "closed" and market.get("result") in ("yes", "no") \
                    and market.get("close_ts") and bar["ts"] >= market["close_ts"]:
                win = held["side"] == market["result"]
                payout = held["contracts"] * (1.0 if win else 0.0)
                cash = round(cash + payout, 6)
                pnl = round(payout - held["contracts"] * entry - held["fee"], 6)
                trades.append({**held, "exit_ts": market["close_ts"],
                               "exit_price": 1.0 if win else 0.0, "exit_fee": 0.0,
                               "pnl": pnl, "exit_slippage": 0.0, "exit_reason": "settlement",
                               "official_result": market["result"]})
                held = None
    if held is not None:  # open at data end: mark at last close, not counted as realized
        last_close = candles[-1]["close"]
        mark = last_close if held["side"] == "yes" else 1.0 - last_close
        trades.append({**held, "exit_ts": candles[-1]["ts"], "exit_price": round(mark, 4),
                       "exit_fee": 0.0, "pnl": None, "exit_slippage": 0.0,
                       "exit_reason": "open-at-data-end", "marked": True})
    realized = [t for t in trades if t["pnl"] is not None]
    wins = [t for t in realized if t["pnl"] >= 0]
    fees = round(sum(t["fee"] + t.get("exit_fee", 0) for t in trades), 6)
    slippage = round(sum(t["slippage"] + t.get("exit_slippage", 0) for t in trades), 6)
    equity = cash
    return {"trades": trades, "skips": skips, "cash": round(cash, 6),
            "n": len(realized), "wins": len(wins), "losses": len(realized) - len(wins),
            "realized_pnl": round(sum(t["pnl"] for t in realized), 6),
            "fees": fees, "slippage": slippage,
            "return_pct": round((equity / STARTING_CASH - 1) * 100, 4)}


def walk_forward_split(candles: list[dict]) -> tuple[list[dict], list[dict]]:
    cut = max(1, int(len(candles) * 0.6))
    return candles[:cut], candles[cut:]


def verdict(val_res: dict | None) -> str:
    if not val_res:
        return "inconclusive"
    if val_res["n"] >= 5 and val_res["realized_pnl"] > 0:
        return "supported"
    if val_res["n"] >= 5 and val_res["realized_pnl"] < 0:
        return "refuted"
    return "inconclusive"


def main() -> int:
    data = Path(sys.argv[1] if len(sys.argv) > 1 else "data")
    season = data / "season-2026" / "backtest"
    strategies = json.loads((data / "strategies.json").read_text(encoding="utf-8"))
    out = {"method": "candle-replay",
           "assumptions": [
               "candle close = YES price; signal on bar t close, fill at bar t+1 open (no look-ahead)",
               "touch = close(t) for YES / 1-close(t) for NO (candle-proxied, NOT book-verified)",
               "limit = touch + strategy limit buffer (default 2c), capped at 0.99",
               "fees: exact Kalshi quadratic formula at each executed side price",
               "targets checked on closes, executed at next open; no target => official settlement",
               "sizing: 25% of $10,000 start per position, whole contracts, cash >= 0 enforced",
               "walk-forward: first 60% of bars development, last 40% validation, fixed params",
           ],
           "pre_registered_verdicts": {
               "supported": "validation PnL > 0 and validation trades >= 5 (net of fees)",
               "refuted": "validation PnL < 0 and validation trades >= 5",
               "inconclusive": "anything else (incl. < 5 validation trades)"},
           "per_strategy": {}}
    for csv_path in sorted((data / "candles").glob("*.csv")):
        candles = load_candles(csv_path)
        if len(candles) < 5:
            continue
        stem = csv_path.name[:-4]
        # file naming: <series>-<market-ticker>.csv; ticker = everything after first '-'
        ticker = stem.split("-", 1)[1] if "-" in stem else stem
        market = None
        for raw_path in sorted((data / "raw").glob("*.json")):
            try:
                m = json.loads(raw_path.read_text(encoding="utf-8")).get("market")
            except (json.JSONDecodeError, AttributeError):
                continue
            if m and m.get("ticker") == ticker:
                market = market_summary(m)
                break
        if market is None:
            print(f"warning: no committed market payload for {ticker}; "
                  f"settling disabled for that series (flagged, not guessed)", file=sys.stderr)
            market = {"ticker": ticker, "result": None, "status": "no-verified-market-payload",
                      "open_ts": candles[0]["ts"], "close_ts": candles[-1]["ts"],
                      "rules_primary": None, "volume": 0}
        dev, val = walk_forward_split(candles)
        for strat in strategies:
            if not strat.get("backtest_eligible"):
                continue
            full = backtest_strategy(strat, candles, market)
            dev_res = backtest_strategy(strat, dev, market) if len(dev) >= 5 else None
            val_res = backtest_strategy(strat, val, market) if len(val) >= 3 else None
            out["per_strategy"][f"{strat['id']}::{ticker}"] = {
                "username": strat["username"], "candles": len(candles),
                "bar_span": [candles[0]["ts"], candles[-1]["ts"]],
                "provenance": sorted({c["provenance"] for c in candles}),
                "market": {k: market.get(k) for k in ("result", "status", "close_ts", "volume")},
                "full": full, "development": dev_res or {}, "validation": val_res or {},
                "verdict": verdict(val_res),
            }
    season.mkdir(parents=True, exist_ok=True)
    (season / "results.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")

    # mirror every backtest into the research DB (honest, append-safe)
    research_dir = data / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    tests_path = research_dir / "tests.json"
    tests = json.loads(tests_path.read_text(encoding="utf-8")) if tests_path.exists() else []
    existing = {t.get("id") for t in tests}
    for key, val in out["per_strategy"].items():
        sid, ticker = key.split("::", 1)
        tid = f"BT-{sid}-{ticker}"
        entry = {
            "id": tid,
            "hypothesis": sid,
            "market": ticker,
            "method": "candle-replay (signal on close, fill at next open, documented buffer)",
            "data_range": f"{val['bar_span'][0]}..{val['bar_span'][1]} (epoch s), {val['candles']} bars",
            "data_files": [f"data/season-2026/backtest/results.json",
                           "candle rows cite: " + ", ".join(val["provenance"])],
            "result": (f"full: n={val['full']['n']} W/L={val['full']['wins']}/{val['full']['losses']} "
                       f"PnL={val['full']['realized_pnl']} fees={val['full']['fees']} "
                       f"slip={val['full']['slippage']}; validation: n="
                       f"{(val.get('validation') or {}).get('n', 0)} PnL="
                       f"{(val.get('validation') or {}).get('realized_pnl', 0)}"),
            "verdict": val["verdict"],
            "caveat": "candle-proxied fills, NOT book-verified; walk-forward split 60/40",
        }
        if tid in existing:
            tests = [t for t in tests if t.get("id") != tid]
        tests.append(entry)
    tests_path.write_text(json.dumps(sorted(tests, key=lambda t: t.get("id", "")),
                                     indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: v["verdict"] for k, v in out["per_strategy"].items()}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
