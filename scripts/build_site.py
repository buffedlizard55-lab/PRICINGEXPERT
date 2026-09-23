#!/usr/bin/env python3
"""Build the published data files for the GitHub Pages site.

Inputs: committed evidence + desk state + research DB.
Outputs (all under data/site/):
  leaderboard.json    season board (per strategy: equity, return, realized PnL,
                      fees, slippage, fills/open/settled, W-L, curve points)
  strategies/<id>.json  per-strategy page payload (rule, evidence, every trade,
                      equity curve, generated analysis, backtest verdict)
  research.json       research DB for the research page (observations, public
                      info, hypotheses, tests, cross-market relationships)
  verification.json   manifest summary, source registry, irregularities log
  upcoming.json       upcoming (queued/blocked) trades the desk has proposed

Purely offline: reads committed files only, rewrites outputs deterministically.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

DATA = Path(sys.argv[1] if len(sys.argv) > 1 else "data")
SEASON_DIR = DATA / "season-2026"
SITE = DATA / "site"


def jload(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def jdump(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def latest_marks(marks: list[dict]) -> dict[str, dict]:
    """Latest mark row per strategy|ticker key, ordered by cycle then at."""
    best: dict[tuple, dict] = {}
    for row in marks:
        key = (row["strategy"], row["ticker"], row.get("side"))
        prev = best.get(key)
        if prev is None or (row["cycle"], row["at"]) >= (prev["cycle"], prev["at"]):
            best[key] = row
    return {f"{s}|{t}|{side}": row for (s, t, side), row in best.items()}


def strategy_analysis(name: str, agg: dict, backtest_verdicts: list[str]) -> str:
    """Ledger-derived narrative. Every number comes from the ledger; the template
    is fixed so the text can never drift from the data."""
    if agg["fills"] == 0:
        verdict = "No verified fills yet — the strategy has proposed trades but none " \
                  "filled inside displayed depth and limits (see its intents), or its " \
                  "trigger conditions have not met on the captured universe."
    else:
        fill_rate = round(agg["filled_contracts"] / agg["requested_contracts"] * 100, 1) \
            if agg["requested_contracts"] else 0.0
        verdict = (
            f"{agg['fills']} verified fill(s); {agg['filled_contracts']:,.0f} of "
            f"{agg['requested_contracts']:,.0f} requested contracts filled "
            f"({fill_rate:.0f}% — the remainder exceeded displayed depth within the limit "
            f"price). Average entry ${agg['avg_entry']:.3f}. "
            f"{agg['closed']} closed: {agg['wins']} win(s) / {agg['losses']} loss(es), "
            f"realized ${agg['realized_pnl']:+,.2f}. Best trade ${agg['best']:+,.2f}, "
            f"worst ${agg['worst']:+,.2f}. Cost drag so far: ${agg['fees']:.2f} taker fees "
            f"+ ${agg['slippage']:.2f} slippage versus the touch = "
            f"{(agg['fees'] + agg['slippage']) / agg['starting_cash'] * 100:.2f}% of "
            f"starting cash."
        )
        if agg["open_positions"] == 0:
            verdict += " No open positions."
        else:
            verdict += (f" {agg['open_positions']} open position(s) marked at "
                        f"${agg['mark_value']:,.2f}; equity {agg['return_pct']:+.2f}%.")
        if agg["realized_pnl"] > 0:
            verdict += " Verdict: working so far — returns are real ledger entries, and the " \
                       "thesis still holds pending more samples."
        elif agg["realized_pnl"] < 0:
            verdict += " Verdict: losing so far — the ledger is the evidence; the strategy " \
                       "stays on the board because the competition measures returns, not risk, " \
                       "and the sample is still small."
        else:
            verdict += " Verdict: flat so far."
    if backtest_verdicts:
        verdict += " Backtest (candle-replay, verified data only): " + ", ".join(
            backtest_verdicts) + "."
    return verdict


def main() -> int:
    strategies = jload(DATA / "strategies.json", [])
    state = jload(SEASON_DIR / "forward" / "state.json",
                  {"strategies": {}, "starting_cash": 10000})
    marks = jsonl(SEASON_DIR / "forward" / "marks.jsonl")
    trades = jsonl(SEASON_DIR / "forward" / "trades.jsonl")
    intents = jsonl(SEASON_DIR / "forward" / "intents.jsonl")
    backtest = jload(SEASON_DIR / "backtest" / "results.json", {})
    latest = latest_marks(marks)
    competition = jload(SEASON_DIR / "competition.json", {})

    # per-strategy aggregation -------------------------------------------------
    agg: dict[str, dict] = {}
    for strat in strategies:
        sid = strat["id"]
        ss = state["strategies"].get(sid, {})
        g = {"strategy_id": sid, "username": strat["username"]}
        g["starting_cash"] = competition.get("starting_cash", 10000)
        g["cash"] = ss.get("cash", g["starting_cash"])
        g["fills"] = ss.get("fills", 0)
        g["exits"] = ss.get("exits", 0)
        g["settlements"] = ss.get("settlements", 0)
        g["wins"] = ss.get("wins", 0)
        g["losses"] = ss.get("losses", 0)
        g["fees"] = ss.get("fees_paid", 0.0)
        g["slippage"] = ss.get("slippage_paid", 0.0)
        g["realized_pnl"] = ss.get("realized_pnl", 0.0)
        g["open_positions"] = len(ss.get("positions", {}))
        g["requested_contracts"] = 0.0
        g["filled_contracts"] = 0.0
        g["entry_notional_sum"] = 0.0
        g["mark_value"] = 0.0
        g["mark_count"] = 0
        g["trade_pnls"] = []
        g["positions"] = []
        for key, pos in ss.get("positions", {}).items():
            ticker, side = key.split("|")
            mark_row = latest.get(f"{strat['username']}|{ticker}|{side}")
            mark = mark_row["mark"] if mark_row else None
            value = mark * pos["contracts"] if mark is not None else None
            g["mark_value"] = round(g["mark_value"] + (value or 0), 6)
            g["mark_count"] += 1 if value is not None else 0
            g["positions"].append({"ticker": ticker, "side": side,
                                   "contracts": pos["contracts"], "entry_vwap": pos["entry_vwap"],
                                   "entry_at": pos.get("entry_at"), "mark": mark,
                                   "value": value, "mark_kind": mark_row["mark_kind"] if mark_row else "none"})
        eq = g["cash"] + g["mark_value"]
        g["equity"] = round(eq, 6)
        g["return_pct"] = round((eq / g["starting_cash"] - 1) * 100, 4)
        agg[sid] = g

    # ledger walk for curve + fill stats + trade lists --------------------------
    curves: dict[str, list[dict]] = {s["id"]: [] for s in strategies}
    by_user = {s["username"]: s["id"] for s in strategies}
    cycle_order: list[str] = []
    for row in trades:
        sid = by_user.get(row["strategy"])
        if not sid:
            continue
        if row["cycle"] not in cycle_order:
            cycle_order.append(row["cycle"])
        if row["type"] == "fill":
            g = agg[sid]
            g["requested_contracts"] = round(g["requested_contracts"]
                                             + row.get("contracts", 0) + (row.get("unfilled") or 0), 2)
            g["filled_contracts"] = round(g["filled_contracts"] + row.get("contracts", 0), 2)
            g["entry_notional_sum"] = round(g["entry_notional_sum"] + row.get("notional", 0), 6)
        if row["type"] in ("exit", "settlement") and row.get("pnl") is not None:
            agg[sid]["trade_pnls"].append(row["pnl"])
    for sid, g in agg.items():
        if g["fills"]:
            g["avg_entry"] = round(g["entry_notional_sum"] / max(g["filled_contracts"], 1e-9), 6)
        else:
            g["avg_entry"] = None
        g["closed"] = len(g["trade_pnls"])
        g["best"] = max(g["trade_pnls"]) if g["trade_pnls"] else 0.0
        g["worst"] = min(g["trade_pnls"]) if g["trade_pnls"] else 0.0

    # equity curve per strategy, exact: cash is reconstructed cycle by cycle from
    # the ledger (fills deduct notional+fee; exits add proceeds; settlements add
    # payout — identical arithmetic to the desk), plus that cycle's open marks.
    all_cycles: list[str] = []
    for row in marks + trades:
        if row["cycle"] not in all_cycles:
            all_cycles.append(row["cycle"])
    for sid, g in agg.items():
        uid = g["username"]
        cash_delta: dict[str, float] = {}
        for t in trades:
            if t["strategy"] != uid:
                continue
            if t["type"] == "fill":
                delta = -(t.get("notional", 0) + t.get("fee", 0))
            elif t["type"] == "exit":
                delta = t.get("proceeds", t.get("vwap", 0) * t.get("contracts", 0) - t.get("fee", 0))
            elif t["type"] == "settlement":
                delta = t.get("payout", 0)
            else:
                continue
            cash_delta[t["cycle"]] = cash_delta.get(t["cycle"], 0.0) + delta
        mark_by_cycle: dict[str, float] = {}
        at_by_cycle: dict[str, str] = {}
        for r in marks:
            if r["strategy"] != uid:
                continue
            mark_by_cycle[r["cycle"]] = mark_by_cycle.get(r["cycle"], 0.0) + (r.get("value") or 0.0)
            at_by_cycle[r["cycle"]] = r["at"]
        running = 0.0
        pts = []
        for c in all_cycles:
            running += cash_delta.get(c, 0.0)
            mv = mark_by_cycle.get(c, 0.0)
            pts.append({"cycle": c, "at": at_by_cycle.get(c),
                        "equity": round(g["starting_cash"] + running + mv, 6),
                        "open_mark": round(mv, 6), "cash": round(g["starting_cash"] + running, 6)})
        g["curve"] = pts

    board = sorted(agg.values(), key=lambda g: (-g["equity"], g["username"]))
    leaderboard = {
        "season": "2026",
        "competition": competition,
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cycles_completed": state.get("cycles_completed", 0),
        "last_cycle": state.get("last_cycle"),
        "last_cycle_at": state.get("last_cycle_at"),
        "rows": board,
    }
    jdump(SITE / "leaderboard.json", leaderboard)

    # per-strategy pages ----------------------------------------------------------
    bt = backtest.get("per_strategy", {})
    for strat in strategies:
        sid = strat["id"]
        g = agg[sid]
        bt_rows = []
        for key, val in bt.items():
            if val["username"] == strat["username"]:
                bt_rows.append({"market": key.split("::")[-1], "verdict": val["verdict"],
                                "candles": val["candles"], "n": val["full"]["n"],
                                "realized_pnl": val["full"]["realized_pnl"],
                                "return_pct": val["full"]["return_pct"],
                                "fees": val["full"]["fees"],
                                "validation_n": (val.get("validation") or {}).get("n", 0),
                                "validation_pnl": (val.get("validation") or {}).get("realized_pnl", 0)})
        strat_trades = [t for t in trades if t["strategy"] == strat["username"]]
        strat_intents = [t for t in intents if t.get("strategy") == strat["username"]]
        payload = {
            "id": sid, "username": strat["username"], "name": strat["name"], "tag": strat["tag"],
            "status": strat["status"], "rule": strat["rule"], "why": strat["why"],
            "failure_mode": strat["failure_mode"], "evidence": strat["evidence"],
            "params": strat["params"],
            "stats": {k: g[k] for k in ("starting_cash", "cash", "equity", "return_pct", "fills",
                                        "exits", "settlements", "wins", "losses", "fees",
                                        "slippage", "realized_pnl", "open_positions", "avg_entry")},
            "positions": g["positions"],
            "trades": strat_trades,
            "intents": strat_intents[-20:],
            "curve": g["curve"],
            "backtest": bt_rows,
            "analysis": strategy_analysis(strat["username"], g,
                                          [f"{b['market']}: {b['verdict']}" for b in bt_rows]),
        }
        jdump(SITE / "strategies" / f"{sid}.json", payload)

    # flat ledger rows for the ledger tab -------------------------------------------
    jdump(SITE / "ledger-rows.json", {"built_at": leaderboard["built_at"],
                                      "rows": trades[-2000:]})

    # live desk fallback snapshot: latest committed market + book per universe market
    universe = jload(DATA / "universe.json", {"markets": []})
    cycles = sorted((SEASON_DIR / "forward").glob("cycle-*"))
    snap = {"captured_at": None, "cycle": None, "markets": [], "books": {}}
    if cycles:
        last_cycle = cycles[-1]
        snap["cycle"] = last_cycle.name
        for meta_path in sorted((last_cycle / "evidence" / "provenance").glob("*.meta.json")):
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            ts_text = meta.get("fetched_at", "")
            if len(ts_text) >= 19:
                snap["captured_at"] = max(snap["captured_at"] or ts_text, ts_text)[:19] + "Z"
            raw_path = last_cycle / "evidence" / "raw" / f"{meta['id']}.json"
            if not raw_path.exists():
                continue
            payload = json.loads(raw_path.read_text(encoding="utf-8"))
            note = meta.get("note", "")
            if note.startswith("kind:market "):
                m = payload.get("market", payload)
                if m.get("ticker") in universe.get("markets", []):
                    snap["markets"].append(m)
            elif note.startswith("kind:orderbook"):
                t = payload.get("market_ticker") or meta.get("ticker", "")
                if t in universe.get("markets", []):
                    snap["books"][t] = payload
    jdump(SITE / "live-snapshot.json", snap)

    # upcoming trades (intents not yet filled / blocked) ---------------------------
    filled_keys = {(t["strategy"], t["ticker"], t["cycle"])
                   for t in trades if t["type"] == "fill"}
    upcoming = []
    for line_no, row in enumerate(intents, 1):
        if row.get("type") == "intent-blocked":
            upcoming.append({"kind": "blocked", "line": line_no, **row})
        elif (row["strategy"], row["ticker"], row["cycle"]) not in filled_keys:
            upcoming.append({"kind": "proposed", "line": line_no, **row})
    jdump(SITE / "upcoming.json", {"built_at": leaderboard["built_at"],
                                   "blocked": state.get("blocked", []),
                                   "rows": upcoming[-200:]})

    # research DB ------------------------------------------------------------------
    research = {
        "built_at": leaderboard["built_at"],
        "observations": [json.loads(l) for l in (DATA / "observations.jsonl")
                         .read_text(encoding="utf-8").splitlines() if l.strip()]
                         if (DATA / "observations.jsonl").exists() else [],
        "public_info": jsonl(DATA / "public-info.jsonl"),
        "hypotheses": jload(DATA / "research" / "hypotheses.json", []),
        "tests": jload(DATA / "research" / "tests.json", []),
        "relationships": jload(DATA / "research" / "relationships.json", []),
    }
    jdump(SITE / "research.json", research)

    # verification summary -----------------------------------------------------------
    manifest = jload(DATA / "manifest.json", {})
    verification = {
        "built_at": leaderboard["built_at"],
        "manifest": manifest,
        "source_registry": jload(DATA / "source-registry.json", []),
        "irregularities": jsonl(DATA / "irregularities.jsonl"),
        "master_site_projects": jload(DATA / "master-site-projects.json", []),
    }
    jdump(SITE / "verification.json", verification)
    print(json.dumps({"strategies": len(strategies), "rows": len(board),
                      "trades": len(trades), "intents": len(intents),
                      "upcoming": len(upcoming),
                      "observations": len(research["observations"]),
                      "hypotheses": len(research["hypotheses"]),
                      "relationships": len(research["relationships"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
