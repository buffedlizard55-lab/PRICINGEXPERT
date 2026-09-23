#!/usr/bin/env python3
"""PRICINGEXPERT paper-trading engine (Python, stdlib only).

Pure arithmetic over values returned by Kalshi's public, unauthenticated Trade API.
Nothing in this module fetches data or invents a price. Every number that enters a
fill must come from a committed, hash-bound evidence file (see data/provenance/).

Exchange mechanics implemented here (each one is documented in the Kalshi API docs
and re-checked in scripts/verify.py against a committed API payload):

  1. Binary order books are reported as YES bids and NO bids. A YES ask is derived
     as 1 - best NO bid and a NO ask as 1 - best YES bid (reciprocal quoting).
  2. A taker BUY of side S crosses the opposite side's bid ladder (buy YES takes
     NO bids at execution price 1 - p; buy NO takes YES bids at 1 - p). A taker
     SELL of side S crosses S's own bid ladder at the quoted bid price.
  3. Taker fee: 0.07 * contracts * price * (1 - price) * fee_multiplier, rounded
     UP to the next $0.0001 (Kalshi's documented quadratic formula; the balance
     grid is one ten-thousandth of a dollar). Settlement of a binary contract pays
     $1.00 to the winning side and $0.00 to the losing side with no settlement fee.
  4. Slippage of a fill is reported as VWAP - touch, where the touch is the best
     executable price at the moment the order would have been sent (the top of the
     crossed ladder, or 1 - top of the opposite ladder for a buy).

The same conventions are mirrored in src/engine.js so the in-browser simulator and
the collector produce identical fills for identical evidence. Tests in
tests/engine.test.mjs and tests/test_paper_engine.py pin both implementations to
the same fixture fills.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

STARTING_CASH = 10_000.0
FEE_GRID = 10_000  # $0.0001
EPS = 1e-9


def fnum(value):
    """Parse Kalshi fixed-point strings ("0.6900", "123.45") or numbers; None if absent/invalid."""
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def parse_ts(value) -> int | None:
    """ISO-8601 (with Z / fractional seconds) or epoch seconds -> epoch seconds."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value if value < 1e12 else value / 1000)
    text = str(value).strip()
    if text.isdigit():
        v = int(text)
        return v if v < 1_000_000_000_000 else v // 1000  # tolerate millisecond strings
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return int(datetime.fromisoformat(text).timestamp())
    except ValueError:
        return None


def iso(ts) -> str | None:
    """Epoch seconds -> 'YYYY-MM-DDTHH:MM:SSZ' (second precision)."""
    if ts is None:
        return None
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def taker_fee(price: float, contracts: float, multiplier: float = 1.0) -> float:
    """Kalshi's published quadratic taker fee, rounded up to $0.0001.

    Fee = 0.07 * C * P * (1-P) * multiplier.  Zero when the formula cannot apply
    (price at/above 1.00 or non-positive size) — those cases are flagged upstream,
    never silently priced.
    """
    if contracts <= 0 or price <= 0 or price >= 1:
        return 0.0
    raw = 0.07 * contracts * price * (1.0 - price) * (multiplier or 1.0)
    return math.ceil(raw * FEE_GRID - 1e-9) / FEE_GRID


def series_of_ticker(ticker: str) -> str:
    """Kalshi market tickers are SERIES-EVENT-MARKET; the first dash segment is the series."""
    return (ticker or "").split("-")[0]


def parse_book(payload: dict) -> dict:
    """Normalize an orderbook response into {'yes': [(price, qty)...], 'no': [...]} ascending.

    Accepts the fixed-point shape (lists of [price_dollars, quantity_fp] under
    'orderbook_fp') and the legacy integer-cent shape ('orderbook' with [cents, qty]).
    Levels with missing prices or non-positive size are dropped, and the remaining
    levels are sorted best-ward last so index -1 is the best level.
    """
    source = payload.get("orderbook_fp") or payload.get("orderbook") or {}
    out: dict[str, list[tuple[float, float]]] = {}
    for side in ("yes", "no"):
        levels = source.get(f"{side}_dollars")
        scale = 1.0
        if levels is None:
            levels = source.get(side) or []
            scale = 0.01  # legacy integer-cent levels
        parsed = []
        for level in levels or []:
            if isinstance(level, (list, tuple)) and len(level) >= 2:
                price, qty = fnum(level[0]), fnum(level[1])
            elif isinstance(level, dict):
                price = fnum(level.get("price_dollars", level.get("price")))
                qty = fnum(level.get("quantity_fp", level.get("quantity")))
            else:
                continue
            if price is None or qty is None or qty <= 0:
                continue
            parsed.append((round(price * scale, 4), qty))
        parsed.sort(key=lambda lv: lv[0])
        out[side] = parsed
    return out


def best_bid(book: dict, side: str) -> float | None:
    levels = book.get(side) or []
    return levels[-1][0] if levels else None


def book_quotes(book: dict) -> dict:
    """Best quotes using the reciprocal rule (see module docstring, rule 1)."""
    yes_bid, no_bid = best_bid(book, "yes"), best_bid(book, "no")
    return {
        "yes_bid": yes_bid,
        "no_bid": no_bid,
        "yes_ask": None if no_bid is None else round(1.0 - no_bid, 4),
        "no_ask": None if yes_bid is None else round(1.0 - yes_bid, 4),
    }


def depth(book: dict, side: str) -> float:
    return sum(qty for _, qty in book.get(side) or [])


def execute(book: dict, side: str, action: str, requested: float, limit: float | None = None) -> dict:
    """Cross the ladder as a taker (immediate-or-cancel limit order).

    buy  YES consumes NO bids (execution price 1 - p);  sell YES consumes YES bids.
    buy  NO  consumes YES bids (1 - p);                  sell NO  consumes NO bids.
    Levels worse than `limit` (above it for buys, below it for sells) are never
    touched; the rest of the quantity is reported as unfilled, never invented.
    """
    empty = {"fills": [], "requested": requested or 0, "filled": 0.0, "notional": 0.0,
             "vwap": None, "unfilled": requested or 0, "touch": None,
             "slippage_per_contract": None, "limit": limit}
    if side not in ("yes", "no") or action not in ("buy", "sell") or requested is None or requested <= 0:
        return empty
    source_side = side if action == "sell" else ("no" if side == "yes" else "yes")
    ladder = sorted(book.get(source_side) or [], key=lambda lv: -lv[0])  # best first
    touch = None if not ladder else (ladder[0][0] if action == "sell" else round(1.0 - ladder[0][0], 4))
    remaining, notional, fills = float(requested), 0.0, []
    for price, qty in ladder:
        if remaining <= EPS:
            break
        exec_price = price if action == "sell" else round(1.0 - price, 4)
        if limit is not None and (
            (action == "buy" and exec_price > limit + EPS)
            or (action == "sell" and exec_price < limit - EPS)
        ):
            break
        take = min(remaining, qty)
        fills.append({"level_price": price, "price": exec_price, "contracts": round(take, 2)})
        remaining -= take
        notional += take * exec_price
    filled = float(requested) - remaining
    vwap = notional / filled if filled > 0 else None
    slip = None
    if vwap is not None and touch is not None:
        slip = (vwap - touch) if action == "buy" else (touch - vwap)
    return {"fills": fills, "requested": float(requested), "filled": round(filled, 2),
            "notional": round(notional, 6), "vwap": None if vwap is None else round(vwap, 6),
            "unfilled": round(max(0.0, remaining), 2), "touch": touch,
            "slippage_per_contract": None if slip is None else round(slip, 6), "limit": limit}


def affordable_contracts(cash: float, price: float, multiplier: float = 1.0) -> int:
    """Largest integer q with q*price + fee(q, price) <= cash (binary search, exact fee)."""
    if cash <= 0 or price is None or price <= 0:
        return 0
    low, high = 0, int(cash / price) + 1
    while high - low > 1:
        mid = (low + high) // 2
        if mid * price + taker_fee(price, mid, multiplier) <= cash + EPS:
            low = mid
        else:
            high = mid
    return low


def size_and_fill(book: dict, side: str, cash: float, fraction: float, multiplier: float = 1.0,
                  limit: float | None = None, max_contracts: float | None = None) -> dict | None:
    """Entry execution for a taker buy sized at `fraction` of cash.

    Immediate-or-cancel limit at `limit` (default: the rule's own price bound or the
    touch). Quantity is the largest whole number of contracts whose notional plus the
    exact fee fits the cash budget. Depth beyond the budget or the limit is reported
    as `unfilled` — it is never filled at a price that was not displayed.
    Returns None when the touch is missing, at/above 1, above the limit, or nothing
    is affordable.
    """
    quotes = book_quotes(book)
    ask = quotes["yes_ask"] if side == "yes" else quotes["no_ask"]
    if ask is None or ask <= 0 or ask >= 1:
        return None
    if limit is None:
        limit = ask
    if ask > limit + EPS:
        return None
    budget = cash * fraction
    if budget <= 0:
        return None
    ceiling = affordable_contracts(budget, ask, multiplier)
    if max_contracts is not None:
        ceiling = min(ceiling, int(max_contracts))
    if ceiling <= 0:
        return None

    def cost(q):
        ex = execute(book, side, "buy", q, limit)
        if ex["filled"] <= 0 or ex["vwap"] is None:
            return None, ex
        return ex["notional"] + taker_fee(ex["vwap"], ex["filled"], multiplier), ex

    total, execution = cost(ceiling)
    if total is None:
        return None
    if total > budget + EPS:
        low, high = 0, ceiling  # cost is monotonic in q
        while high - low > 1:
            mid = (low + high) // 2
            mid_total, _ = cost(mid)
            if mid_total is not None and mid_total <= budget + EPS:
                low = mid
            else:
                high = mid
        if low <= 0:
            return None
        total, execution = cost(low)
        if total is None or total > budget + EPS:
            return None
    execution["fee"] = taker_fee(execution["vwap"], execution["filled"], multiplier)
    execution["side"] = side
    return execution


def exit_fill(book: dict, side: str, contracts: float) -> dict | None:
    """Sell everything held into the current bid ladder (no limit: exit orders are
    immediate-or-cancel at market; unfilled remainder stays open and is re-marked)."""
    if contracts <= 0:
        return None
    ex = execute(book, side, "sell", contracts)
    if ex["filled"] <= 0:
        return None
    ex["fee"] = taker_fee(ex["vwap"], ex["filled"])
    ex["side"] = side
    return ex


def mark_value(book_or_quotes: dict, side: str, contracts: float) -> tuple[float | None, float | None]:
    """Conservative mark: current best bid of the held side (None when no bid exists)."""
    bid = book_or_quotes.get(f"{side}_bid")
    if bid is None:
        return None, None
    return bid, round(bid * contracts, 6)


def settlement_payout(side: str, contracts: float, result: str) -> float:
    """Binary settlement: $1.00 per winning contract, $0.00 otherwise, no fee."""
    if result not in ("yes", "no"):
        raise ValueError(f"cannot settle: missing/unknown result {result!r}")
    return round(contracts * (1.0 if side == result else 0.0), 6)
