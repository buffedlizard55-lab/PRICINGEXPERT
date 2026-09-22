/* PRICINGEXPERT browser engine — mirrors scripts/paper_engine.py exactly.
 * Pure arithmetic over Kalshi public-API values. No fetches, no invented prices.
 * The node test tests/engine.test.mjs pins this file to the same fixture fills
 * as the Python engine.
 */
(function (global) {
  "use strict";

  const STARTING_CASH = 10000;
  const FEE_GRID = 10000; // $0.0001
  const EPS = 1e-9;

  function fnum(value) {
    if (value === null || value === undefined || value === "") return null;
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function takerFee(price, contracts, multiplier = 1) {
    if (contracts <= 0 || price <= 0 || price >= 1) return 0;
    const raw = 0.07 * contracts * price * (1 - price) * (multiplier || 1);
    return Math.ceil(raw * FEE_GRID - 1e-9) / FEE_GRID;
  }

  function parseBook(payload) {
    const source = payload.orderbook_fp || payload.orderbook || {};
    const out = { yes: [], no: [] };
    for (const side of ["yes", "no"]) {
      let levels = source[side + "_dollars"];
      let scale = 1.0;
      if (levels === undefined || levels === null) {
        levels = source[side] || [];
        scale = 0.01; // legacy integer-cent levels
      }
      const parsed = [];
      for (const level of levels || []) {
        let price, qty;
        if (Array.isArray(level) && level.length >= 2) {
          price = fnum(level[0]); qty = fnum(level[1]);
        } else if (level && typeof level === "object") {
          price = fnum(level.price_dollars !== undefined ? level.price_dollars : level.price);
          qty = fnum(level.quantity_fp !== undefined ? level.quantity_fp : level.quantity);
        } else { continue; }
        if (price === null || qty === null || qty <= 0) continue;
        parsed.push([round4(price * scale), qty]);
      }
      parsed.sort((a, b) => a[0] - b[0]);
      out[side] = parsed;
    }
    return out;
  }

  function round4(x) { return Math.round(x * 10000) / 10000; }

  function bestBid(book, side) {
    const levels = book[side] || [];
    return levels.length ? levels[levels.length - 1][0] : null;
  }

  function bookQuotes(book) {
    const yesBid = bestBid(book, "yes");
    const noBid = bestBid(book, "no");
    return {
      yes_bid: yesBid, no_bid: noBid,
      yes_ask: noBid === null ? null : round4(1 - noBid),
      no_ask: yesBid === null ? null : round4(1 - yesBid),
    };
  }

  function depth(book, side) {
    return (book[side] || []).reduce((s, lv) => s + lv[1], 0);
  }

  /** Cross the ladder as a taker IOC limit order.
   *  buy YES  <- NO bids (exec price 1-p); sell YES -> YES bids
   *  buy NO   <- YES bids (1-p);        sell NO  -> NO bids
   */
  function execute(book, side, action, requested, limit = null) {
    const empty = { fills: [], requested: requested || 0, filled: 0, notional: 0,
      vwap: null, unfilled: requested || 0, touch: null, slippage_per_contract: null, limit };
    if (!["yes", "no"].includes(side) || !["buy", "sell"].includes(action) ||
        requested === null || requested <= 0) return empty;
    const sourceSide = action === "sell" ? side : (side === "yes" ? "no" : "yes");
    const ladder = (book[sourceSide] || []).slice().sort((a, b) => b[0] - a[0]); // best first
    const touch = ladder.length
      ? (action === "sell" ? ladder[0][0] : round4(1 - ladder[0][0]))
      : null;
    let remaining = Number(requested), notional = 0;
    const fills = [];
    for (const [price, qty] of ladder) {
      if (remaining <= EPS) break;
      const execPrice = action === "sell" ? price : round4(1 - price);
      if (limit !== null &&
          ((action === "buy" && execPrice > limit + EPS) ||
           (action === "sell" && execPrice < limit - EPS))) break;
      const take = Math.min(remaining, qty);
      fills.push({ level_price: price, price: execPrice, contracts: round2(take) });
      remaining -= take;
      notional += take * execPrice;
    }
    const filled = Number(requested) - remaining;
    const vwap = filled > 0 ? notional / filled : null;
    let slip = null;
    if (vwap !== null && touch !== null) slip = action === "buy" ? vwap - touch : touch - vwap;
    return { fills, requested: Number(requested), filled: round2(filled),
      notional: round6(notional), vwap: vwap === null ? null : round6(vwap),
      unfilled: round2(Math.max(0, remaining)), touch,
      slippage_per_contract: slip === null ? null : round6(slip), limit };
  }

  function round2(x) { return Math.round(x * 100) / 100; }
  function round6(x) { return Math.round(x * 1e6) / 1e6; }

  /** Largest whole q with q*price + fee <= cash. */
  function affordableContracts(cash, price, multiplier = 1) {
    if (cash <= 0 || price === null || price <= 0) return 0;
    let lo = 0, hi = Math.floor(cash / price) + 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (mid * price + takerFee(price, mid, multiplier) <= cash + EPS) lo = mid;
      else hi = mid;
    }
    return lo;
  }

  /** Simulate placing a taker buy now, against the given book payload.
   *  Returns a full fill report or a blocked report (never a guess). */
  function simulateBuy(payload, side, contracts, limit) {
    const book = parseBook(payload);
    const quotes = bookQuotes(book);
    const ask = side === "yes" ? quotes.yes_ask : quotes.no_ask;
    if (contracts === null || contracts <= 0)
      return { blocked: "invalid-size", book, quotes };
    if (ask === null) return { blocked: "no-executable-ask", book, quotes };
    const lim = limit !== null ? Number(limit) : ask;
    if (ask > lim + EPS)
      return { blocked: "ask-above-limit", ask, limit: lim, book, quotes };
    const ex = execute(book, side, "buy", Number(contracts), lim);
    if (ex.filled <= 0) return { blocked: "not-fillable", ask, limit: lim, book, quotes };
    const fee = takerFee(ex.vwap, ex.filled);
    return {
      ok: true, side, requested: ex.requested, filled: ex.filled, unfilled: ex.unfilled,
      vwap: ex.vwap, touch: ex.touch, notional: ex.notional, fee,
      slippage: ex.slippage_per_contract === null ? 0 : round6(ex.slippage_per_contract * ex.filled),
      total_cost: round6(ex.notional + fee),
      levels: ex.fills.map(f => ({ price: f.price, contracts: f.contracts })),
      ask, limit: lim,
    };
  }

  function markValue(quotes, side, contracts) {
    const bid = quotes[side + "_bid"];
    return bid === null ? null : round6(bid * contracts);
  }

  function settlementPayout(side, contracts, result) {
    if (result !== "yes" && result !== "no")
      throw new Error("cannot settle: missing official result");
    return round6(contracts * (side === result ? 1 : 0));
  }

  global.PX = {
    STARTING_CASH, fnum, takerFee, parseBook, bookQuotes, depth, execute,
    affordableContracts, simulateBuy, markValue, settlementPayout,
  };
})(typeof window !== "undefined" ? window : globalThis);
