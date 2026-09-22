#!/usr/bin/env python3
"""Forward-test desk: one cycle of the Season-2026 paper competition.

Input (all committed, hash-bound evidence — never the network):
  data/season-2026/forward/cycle-NNN/evidence/<id>.json     verbatim API payloads
  data/season-2026/forward/cycle-NNN/evidence/<id>.meta.json provenance
  data/season-2026/forward/state.json                       desk state before the cycle
  data/strategies.json                                       the rule personas
  data/universe.json                                         tradable markets

Output (all committed):
  cycle-NNN/actions.jsonl      every intent, fill, exit, settlement, mark of the cycle
  forward/trades.jsonl         append-only ledger (fills + exits + settlements)
  forward/intents.jsonl        append-only ledger (every proposed trade, incl. blocked)
  forward/marks.jsonl          per-cycle mark-to-market rows
  forward/state.json           updated desk state

Guarantees (checked by scripts/verify.py):
  * a fill price is always a price that appears in the captured order book (or 1 -
    a captured NO/YES bid, per the reciprocal rule);
  * fees and slippage are exact per the engine's documented formulae;
  * cash can never go negative; unfilled remainder is logged, never invented;
  * settlements use the official `result` field of a captured market payload.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paper_engine import (book_quotes, depth, exit_fill, fnum, iso, mark_value,  # noqa: E402
                          parse_ts, settlement_payout, size_and_fill, taker_fee)

EPS = 1e-9


# ------------------------------------------------------------------ evidence loading
def load_evidence(cycle_dir: Path) -> dict[str, tuple[dict, dict]]:
    out = {}
    for meta_path in sorted((cycle_dir / "evidence" / "provenance").glob("*.meta.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        raw = (cycle_dir / "evidence" / "raw" / f"{meta['id']}.json").read_bytes()
        if hashlib.sha256(raw).hexdigest() != meta["sha256"]:
            raise SystemExit(f"evidence hash mismatch: {meta['id']}")
        out[meta["id"]] = (json.loads(raw.decode("utf-8")), meta)
    return out


def evidence_row(evidence: dict, note_prefix: str) -> list[tuple[dict, dict]]:
    rows = []
    for sid, (payload, meta) in evidence.items():
        if meta.get("note", "").startswith(note_prefix):
            rows.append((payload, meta))
    return rows


def norm_market(payload: dict) -> dict:
    m = payload.get("market", payload)
    yb, nb = fnum(m.get("yes_bid_dollars")), fnum(m.get("no_bid_dollars"))
    ya = fnum(m.get("yes_ask_dollars")) or (round(1 - nb, 4) if nb is not None else None)
    na = fnum(m.get("no_ask_dollars")) or (round(1 - yb, 4) if yb is not None else None)
    return {
        "ticker": m.get("ticker"),
        "series": (m.get("event_ticker") or m.get("ticker") or "").split("-")[0],
        "title": m.get("title"),
        "status": m.get("status"),
        "result": (m.get("result") or "").lower() or None,
        "rules_primary": m.get("rules_primary"),
        "yes_bid": yb, "yes_ask": ya, "no_bid": nb, "no_ask": na,
        "yes_bid_size": fnum(m.get("yes_bid_size_fp")),
        "no_bid_size": fnum(m.get("no_bid_size_fp")),
        "last": fnum(m.get("last_price_dollars")),
        "previous": fnum(m.get("previous_price_dollars")),
        "volume": fnum(m.get("volume_fp")) or 0.0,
        "volume_24h": fnum(m.get("volume_24h_fp")) or 0.0,
        "open_ts": parse_ts(m.get("open_time")),
        "close_ts": parse_ts(m.get("close_time")),
        "exp_ts": parse_ts(m.get("expected_expiration_time")) or parse_ts(m.get("close_time")),
        "settlement_ts": parse_ts(m.get("settlement_ts")),
        "updated_ts": parse_ts(m.get("updated_time")),
        "can_close_early": m.get("can_close_early"),
    }


def norm_book(payload: dict) -> dict:
    from paper_engine import parse_book
    return parse_book(payload)


def append_jsonl(path: Path, row: dict):
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


# ------------------------------------------------------------------ rule evaluation
def spread(m: dict, side: str) -> float | None:
    bid, ask = m[f"{side}_bid"], m[f"{side}_ask"]
    if bid is None or ask is None:
        return None
    return round(ask - bid, 4)


def evaluate_strategy(strat: dict, m: dict, book: dict, now_ts: int) -> list[dict]:
    """Return the list of intents a strategy proposes for this market.

    Each intent records the exact observed numbers that triggered it (`trigger`),
    so a reviewer can re-derive every trade from the evidence file alone.
    """
    p = strat["params"]
    intents = []
    quotes = book_quotes(book)
    yes_depth = depth(book, "yes")
    no_depth = depth(book, "no")

    def ask(side):
        return quotes[f"{side}_ask"]

    # --- favourite-longshot family ---------------------------------------------
    if strat["id"] in ("sure-penny", "longshot-fader"):
        if strat["id"] == "sure-penny" and m["volume"] < p["min_volume"]:
            return []
        if strat["id"] == "longshot-fader" and m["volume_24h"] < p["min_volume_24h"]:
            return []
        fav_low = p.get("ask_low", p.get("fav_ask_low"))
        fav_high = p.get("ask_high", p.get("fav_ask_high"))
        for side in ("yes", "no"):
            a = ask(side)
            if a is None or fav_low is None:
                continue
            if fav_low <= a <= fav_high and spread(m, side) is not None and \
               spread(m, side) <= p["max_spread"]:
                if strat["id"] == "longshot-fader":
                    other = "no" if side == "yes" else "yes"
                    if not (p["longshot_ask_low"] <= (ask(other) or -1) <= p["longshot_ask_high"]):
                        continue
                intents.append({
                    "side": side, "action": "buy", "cash_fraction": p["cash_fraction"],
                    "limit": a, "exit": p["exit"],
                    "trigger": {"ask": a, "bid": m[f"{side}_bid"],
                                "other_ask": ask("no" if side == "yes" else "yes"),
                                "volume": m["volume"], "volume_24h": m["volume_24h"]},
                })
        return intents

    # --- expiry convergence -----------------------------------------------------
    if strat["id"] == "last-mile" or strat["id"] == "macro-closer":
        horizon = p["horizon_hours"] * 3600
        if m["exp_ts"] is None or m["exp_ts"] - now_ts > horizon or now_ts >= m["exp_ts"]:
            return []
        if m["volume"] < p["min_volume"]:
            return []
        for side in ("yes", "no"):
            a = ask(side)
            if a is None or not (p["ask_low"] <= a <= p["ask_high"]):
                continue
            s = spread(m, side)
            if s is not None and s > p["max_spread"]:
                continue
            intents.append({
                "side": side, "action": "buy", "cash_fraction": p["cash_fraction"],
                "limit": a, "exit": p["exit"],
                "trigger": {"ask": a, "hours_to_expiry": round((m["exp_ts"] - now_ts) / 3600, 3),
                            "volume": m["volume"]},
            })
        return intents

    # --- momentum / mean reversion on official last vs previous ------------------
    if strat["id"] in ("tape-rider", "deep-fade"):
        if m["last"] is None or m["previous"] is None:
            return []
        move = round(m["last"] - m["previous"], 4)
        if strat["id"] == "tape-rider":
            if abs(move) < p["min_move"]:
                return []
            side = "yes" if move > 0 else "no"
            a = ask(side)
            if a is None:
                return []
            intents.append({
                "side": side, "action": "buy", "cash_fraction": p["cash_fraction"],
                "limit": round(min(0.99, a + p["limit_buffer"]), 4), "exit": p["exit"],
                "exit_target": round(a + p["exit_target_over_entry"], 4),
                "trigger": {"last": m["last"], "previous": m["previous"], "move": move,
                            "ask": a, "market_age_h": (now_ts - (m["open_ts"] or now_ts)) / 3600},
            })
        else:
            if abs(move) < p["min_move"]:
                return []
            if move < 0 and m["last"] <= 0.45:
                side, note = "yes", "fade-the-drop"
            elif move > 0 and m["last"] >= 0.55:
                side, note = "no", "fade-the-run"
            else:
                return []
            a = ask(side)
            if a is None:
                return []
            intents.append({
                "side": side, "action": "buy", "cash_fraction": p["cash_fraction"],
                "limit": round(min(0.99, a + p["limit_buffer"]), 4), "exit": p["exit"],
                "exit_multiple": p["exit_multiple"],
                "trigger": {"last": m["last"], "previous": m["previous"], "move": move,
                            "ask": a, "note": note},
            })
        return intents

    # --- crossed-book arbitrage ---------------------------------------------------
    if strat["id"] == "cross-hunt":
        ya, na = quotes["yes_ask"], quotes["no_ask"]
        if ya is None or na is None:
            return []
        pair_cost = round(ya + na, 4)
        if pair_cost <= p["max_pair_cost"]:
            intents.append({
                "both": True, "cash_fraction": p["cash_fraction"],
                "trigger": {"yes_ask": ya, "no_ask": na, "pair_cost": pair_cost},
            })
        return intents

    # --- depth imbalance ------------------------------------------------------------
    if strat["id"] == "depth-diver":
        if yes_depth <= 0 or no_depth <= 0:
            return []
        imb = round((yes_depth - no_depth) / (yes_depth + no_depth), 4)
        if abs(imb) >= p["min_imbalance"]:
            side = "yes" if imb > 0 else "no"
            a = ask(side)
            if a is None:
                return []
            intents.append({
                "side": side, "action": "buy", "cash_fraction": p["cash_fraction"],
                "limit": round(min(0.99, a + p["limit_buffer"]), 4), "exit": p["exit"],
                "trigger": {"yes_depth": yes_depth, "no_depth": no_depth, "imbalance": imb, "ask": a},
            })
        return intents

    # --- penny lottery ----------------------------------------------------------------
    if strat["id"] == "penny-lotto":
        if m["volume"] <= 0:
            return []
        for side in ("yes", "no"):
            a = ask(side)
            if a is None or not (p["ask_low"] <= a <= p["ask_high"]):
                continue
            intents.append({
                "side": side, "action": "buy", "cash_fraction": p["cash_fraction"],
                "limit": a, "exit": p["exit"], "exit_multiple": p["exit_multiple"],
                "trigger": {"ask": a, "volume": m["volume"]},
            })
        return intents

    # --- commodity drift ----------------------------------------------------------------
    if strat["id"] == "commodity-drift":
        horizon = p["horizon_hours"] * 3600
        if m["exp_ts"] is None or m["exp_ts"] - now_ts > horizon or now_ts >= m["exp_ts"]:
            return []
        prob_yes = m["last"]
        if prob_yes is None:
            return []
        if prob_yes >= p["min_probability"]:
            side = "yes"
        elif 1 - prob_yes >= p["min_probability"]:
            side = "no"
        else:
            return []
        a = ask(side)
        if a is None:
            return []
        intents.append({
            "side": side, "action": "buy", "cash_fraction": p["cash_fraction"],
            "limit": round(min(0.99, a + p["limit_buffer"]), 4), "exit": p["exit"],
            "trigger": {"last": prob_yes, "hours_to_expiry": round((m["exp_ts"] - now_ts) / 3600, 3),
                        "ask": a},
        })
        return intents

    raise SystemExit(f"no rule implementation for strategy {strat['id']!r}")


# ------------------------------------------------------------------ the cycle
def run_cycle(cycle_dir: Path, season_dir: Path, universe: dict, now_ts: int) -> dict:
    state_path = season_dir / "forward" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    strategies = json.loads(Path(sys.argv[3] if len(sys.argv) > 3 else "data/strategies.json")
                            .read_text(encoding="utf-8"))
    evidence = load_evidence(cycle_dir)
    markets = {}
    for payload, meta in evidence_row(evidence, "kind:market"):
        m = norm_market(payload)
        markets[m["ticker"]] = (m, meta)
    books = {}
    for payload, meta in evidence_row(evidence, "kind:orderbook"):
        books[payload.get("market_ticker", meta.get("ticker"))] = norm_book(payload)

    actions: list[dict] = []
    ledger = season_dir / "forward" / "trades.jsonl"
    intents_log = season_dir / "forward" / "intents.jsonl"
    marks_log = season_dir / "forward" / "marks.jsonl"

    def append(path: Path, row: dict):
        append_jsonl(path, row)

    def strat_state(sid):
        return state["strategies"].setdefault(sid, {
            "cash": state["starting_cash"], "positions": {}, "realized_pnl": 0.0,
            "fees_paid": 0.0, "slippage_paid": 0.0, "wins": 0, "losses": 0,
            "fills": 0, "exits": 0, "settlements": 0,
        })

    tradable = [t for t in universe.get("markets", []) if t in markets]
    blocked: list[dict] = []

    for strat in strategies:
        ss = strat_state(strat["id"])
        # 1) settle closed positions first
        for key in list(ss["positions"].keys()):
            ticker, side = key.split("|")
            m = markets.get(ticker)
            m = m[0] if m else None
            if m and m["status"] == "closed" and m["result"] in ("yes", "no"):
                pos = ss["positions"].pop(key)
                payout = settlement_payout(side, pos["contracts"], m["result"])
                ss["cash"] = round(ss["cash"] + payout, 6)
                pnl = round(payout - pos["entry_notional"] - pos["fee_paid"], 6)
                ss["realized_pnl"] = round(ss["realized_pnl"] + pnl, 6)
                if pnl >= 0:
                    ss["wins"] += 1
                else:
                    ss["losses"] += 1
                ss["settlements"] += 1
                row = {
                    "type": "settlement", "cycle": cycle_dir.name, "at": iso(now_ts),
                    "strategy": strat["username"], "ticker": ticker, "side": side,
                    "contracts": pos["contracts"], "result": m["result"], "payout": payout,
                    "entry_notional": pos["entry_notional"], "fee_paid": pos["fee_paid"],
                    "pnl": pnl, "market_status": m["status"], "settlement_ts": m["settlement_ts"],
                    "evidence": f"market:{ticker}:{cycle_dir.name}",
                }
                append(ledger, row)
                actions.append(row)
        # 2) managed exits on open positions (targets), using the current book
        for key in list(ss["positions"].keys()):
            ticker, side = key.split("|")
            mrow = markets.get(ticker)
            mobj = mrow[0] if mrow else None
            if ticker not in books or mobj is None or mobj["status"] != "active":
                continue
            pos = ss["positions"][key]
            bid = book_quotes(books[ticker])[f"{side}_bid"]
            target = pos.get("exit_target") or (pos.get("entry_vwap", 0) * pos.get("exit_multiple", 0)
                                                if pos.get("exit_multiple") else None)
            if target and bid is not None and bid >= target - EPS:
                ex = exit_fill(books[ticker], side, pos["contracts"])
                if ex and ex["filled"] > 0:
                    proceeds = round(ex["notional"] - ex["fee"], 6)
                    ss["cash"] = round(ss["cash"] + proceeds, 6)
                    ss["fees_paid"] = round(ss["fees_paid"] + ex["fee"], 6)
                    slip = round((ex["vwap"] - (bid - ex["slippage_per_contract"])) * ex["filled"], 6) \
                        if ex["slippage_per_contract"] is not None else 0.0
                    ss["slippage_paid"] = round(ss["slippage_paid"] + slip, 6)
                    pnl = round(proceeds - pos["entry_notional"] - pos["fee_paid"], 6)
                    ss["realized_pnl"] = round(ss["realized_pnl"] + pnl, 6)
                    if pnl >= 0:
                        ss["wins"] += 1
                    else:
                        ss["losses"] += 1
                    ss["positions"].pop(key)
                    ss["exits"] += 1
                    proceeds_round = round(ex["notional"] - ex["fee"], 6)
                    row = {
                        "type": "exit", "cycle": cycle_dir.name, "at": iso(now_ts),
                        "strategy": strat["username"], "ticker": ticker, "side": side,
                        "contracts": ex["filled"], "vwap": ex["vwap"], "fee": ex["fee"],
                        "notional": ex["notional"], "proceeds": proceeds_round,
                        "slippage": slip, "pnl": pnl, "bid_at_exit": bid,
                        "entry_vwap": pos["entry_vwap"], "exit_reason": "target",
                        "evidence": f"book:{ticker}:{cycle_dir.name}",
                    }
                    append(ledger, row)
                    actions.append(row)
        # 3) new intents on tradable active markets
        for ticker in tradable:
            m, _meta = markets[ticker]
            if m["status"] != "active":
                continue
            book = books.get(ticker)
            if book is None:
                blocked.append({"strategy": strat["username"], "ticker": ticker,
                                "reason": "no-captured-book"})
                continue
            for intent in evaluate_strategy(strat, m, book, now_ts):
                row_base = {"strategy": strat["username"], "strategy_id": strat["id"],
                            "ticker": ticker, "cycle": cycle_dir.name, "at": iso(now_ts),
                            "title": m["title"], "trigger": intent.get("trigger"),
                            "rules_primary": m["rules_primary"][:200]}
                if intent.get("both"):
                    # crossed-book: buy YES then buy NO, equal size
                    q_yes = size_and_fill(book, "yes", ss["cash"] * intent["cash_fraction"], 1.0,
                                          limit=intent["trigger"]["yes_ask"])
                    if not q_yes or q_yes["filled"] <= 0:
                        blocked.append({**row_base, "type": "intent-blocked",
                                        "reason": "yes-leg-not-fillable"})
                        continue
                    spend = q_yes["notional"] + q_yes["fee"]
                    q_no = size_and_fill(book, "no", ss["cash"] * intent["cash_fraction"], 1.0,
                                         limit=intent["trigger"]["no_ask"])
                    if not q_no or q_no["filled"] <= 0:
                        # unwind the YES leg at its bid (documented leg-risk handling)
                        unw = exit_fill(book, "yes", q_yes["filled"]) if q_yes["filled"] else None
                        if unw:
                            ss["cash"] = round(ss["cash"] - q_yes["notional"] + unw["notional"]
                                               - q_yes["fee"] - unw["fee"], 6)
                            ss["fees_paid"] = round(ss["fees_paid"] + q_yes["fee"] + unw["fee"], 6)
                        blocked.append({**row_base, "type": "intent-blocked",
                                        "reason": "no-leg-not-fillable", "yes_leg": "unwound"})
                        continue
                    _apply_entry(ss, ledger, actions, row_base, q_yes, "yes", m, strat, ss["cash"])
                    _apply_entry(ss, ledger, actions, row_base, q_no, "no", m, strat, ss["cash"])
                    continue
                ex = size_and_fill(book, intent["side"], ss["cash"], intent["cash_fraction"],
                                   limit=intent["limit"])
                if not ex or ex["filled"] <= 0:
                    blocked.append({**row_base, "type": "intent-blocked",
                                    "reason": "not-fillable-at-limit",
                                    "limit": intent.get("limit"), "ask": intent.get("trigger", {}).get("ask")})
                    continue
                _apply_entry(ss, ledger, actions, row_base, ex, intent["side"], m, strat,
                             None, intent.get("exit_target"), intent.get("exit_multiple"))
        # 4) marks
        for key, pos in ss["positions"].items():
            ticker, side = key.split("|")
            mrow = markets.get(ticker)
            m = mrow[0] if mrow else None
            quotes = book_quotes(books[ticker]) if ticker in books else {
                f"{s}_bid": m[f"{s}_bid"] for s in ("yes", "no")} if m else {}
            bid, val = mark_value(quotes, side, pos["contracts"])
            if bid is None and m and m["last"] is not None:
                bid, val = m["last"], round(m["last"] * pos["contracts"], 6)
                mark_kind = "last-trade"
            elif bid is None:
                mark_kind = "unmarked"
            else:
                mark_kind = "bid"
            row = {"cycle": cycle_dir.name, "at": iso(now_ts), "strategy": strat["username"],
                   "ticker": ticker, "side": side, "contracts": pos["contracts"],
                   "mark": bid, "value": val, "mark_kind": mark_kind}
            append(marks_log, row)
            actions.append({"type": "mark", **row})
    state["cycles_completed"] = state.get("cycles_completed", 0) + 1
    state["last_cycle"] = cycle_dir.name
    state["last_cycle_at"] = iso(now_ts)
    state["blocked"] = blocked
    state_path.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    (cycle_dir / "actions.jsonl").write_text(
        "\n".join(json.dumps(a, sort_keys=True) for a in actions) + ("\n" if actions else ""),
        encoding="utf-8")
    return {"actions": len(actions), "blocked": len(blocked),
            "strategies": len(state["strategies"])}


def _apply_entry(ss, ledger, actions, row_base, ex, side, m, strat, cash_snapshot,
                 exit_target=None, exit_multiple=None) -> None:
    fee = ex.get("fee")
    if fee is None:
        fee = taker_fee(ex["vwap"], ex["filled"])
    ss["cash"] = round(ss["cash"] - ex["notional"] - fee, 6)
    if ss["cash"] < -EPS:
        raise SystemExit(f"negative cash for {strat['username']}: {ss['cash']}")
    ss["fees_paid"] = round(ss["fees_paid"] + fee, 6)
    slip = 0.0
    if ex.get("slippage_per_contract") is not None:
        slip = round(ex["slippage_per_contract"] * ex["filled"], 6)
    ss["slippage_paid"] = round(ss["slippage_paid"] + slip, 6)
    ss["fills"] += 1
    key = f"{row_base['ticker']}|{side}"
    pos = ss["positions"].get(key)
    contracts = round(pos["contracts"] + ex["filled"], 2) if pos else ex["filled"]
    notional = round((pos["entry_notional"] if pos else 0) + ex["notional"], 6)
    vwap = round(notional / contracts, 6) if contracts else None
    ss["positions"][key] = {
        "contracts": contracts, "entry_notional": notional, "entry_vwap": vwap,
        "fee_paid": round((pos["fee_paid"] if pos else 0) + fee, 6),
        "entry_cycle": cycle_name(row_base), "entry_at": row_base["at"],
        "rules_primary": row_base["rules_primary"],
        "exit_target": exit_target, "exit_multiple": exit_multiple,
    }
    row = {
        "type": "fill", "cycle": row_base["cycle"], "at": row_base["at"],
        "strategy": row_base["strategy"], "ticker": key.split("|")[0], "side": side,
        "contracts": ex["filled"], "vwap": ex["vwap"], "fee": fee, "slippage": slip,
        "touch": ex.get("touch"), "limit": ex.get("limit"), "unfilled": ex.get("unfilled"),
        "notional": ex["notional"], "cash_after": ss["cash"],
        "fills": ex.get("fills", []),
        "trigger": row_base.get("trigger"), "title": row_base.get("title"),
        "rules_primary": row_base.get("rules_primary"),
        "evidence": f"book:{key.split('|')[0]}:{row_base['cycle']}",
    }
    append_jsonl(ledger, row)
    actions.append(row)


def cycle_name(row_base: dict) -> str:
    return row_base["cycle"]


def main() -> int:
    if len(sys.argv) < 4:
        print("usage: forward_desk.py <cycle_dir> <season_dir> <strategies.json> [now_epoch]")
        return 2
    cycle_dir = Path(sys.argv[1])
    season_dir = Path(sys.argv[2])
    now_ts = int(sys.argv[4]) if len(sys.argv) > 4 else int(datetime.now(timezone.utc).timestamp())
    universe = json.loads((season_dir.parent / "universe.json").read_text(encoding="utf-8"))
    summary = run_cycle(cycle_dir, season_dir, universe, now_ts)
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
