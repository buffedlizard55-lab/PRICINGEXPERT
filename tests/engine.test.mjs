/* Engine parity tests: src/engine.js (browser) vs scripts/paper_engine.py (desk).
 * The fixture book is fixed; expected fills are computed by hand from the
 * exchange's documented mechanics, so both implementations are pinned to the
 * same arithmetic. Run: node tests/engine.test.mjs
 */
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";

const src = readFileSync(new URL("../src/engine.js", import.meta.url), "utf8");
const mod = { exports: {} };
const fn = new Function("module", "exports", "globalThis", src + "\n; return exports;");
const PX = new Function("window", src + "\n; return window.PX;")( {} );

const BOOK = {
  orderbook_fp: {
    yes_dollars: [[0.4900, 100], [0.4800, 200], [0.4700, 300]],
    no_dollars:  [[0.4900, 80], [0.4800, 150]],
  },
};

let passed = 0;
function t(name, f) { f(); passed++; console.log("ok -", name); }

t("quotes: reciprocal rule", () => {
  const q = PX.bookQuotes(PX.parseBook(BOOK));
  assert.equal(q.yes_bid, 0.49);
  assert.equal(q.no_bid, 0.49);
  assert.equal(q.yes_ask, 0.51); // 1 - 0.49
  assert.equal(q.no_ask, 0.51);
});

t("buy YES crosses NO bids at 1-p, limit-bounded", () => {
  // limit 0.52: takes NO bid 0.49 (exec 0.51, 80) and NO bid 0.48 (exec 0.52, 150); 200 requested
  const r = PX.execute(PX.parseBook(BOOK), "yes", "buy", 200, 0.52);
  assert.equal(r.filled, 200);
  assert.equal(r.fills.length, 2);
  assert.equal(r.fills[0].price, 0.51);
  assert.equal(r.fills[0].contracts, 80);
  assert.equal(r.fills[1].price, 0.52);
  assert.equal(r.fills[1].contracts, 120);
  const expectNotional = 80 * 0.51 + 120 * 0.52;
  assert.ok(Math.abs(r.notional - expectNotional) < 1e-9);
  const expectVwap = expectNotional / 200;
  assert.ok(Math.abs(r.vwap - expectVwap) < 1e-6);
  assert.equal(r.touch, 0.51);
  assert.ok(Math.abs(r.slippage_per_contract - (expectVwap - 0.51)) < 1e-6);
});

t("buy respects limit: nothing above the limit is taken", () => {
  const r = PX.execute(PX.parseBook(BOOK), "yes", "buy", 500, 0.505);
  // only NO bid 0.49 -> exec 0.51 <= 0.505? NO: 0.51 > 0.505, so zero fills
  assert.equal(r.filled, 0);
  assert.equal(r.unfilled, 500);
});

t("sell YES crosses YES bids at bid prices", () => {
  const r = PX.execute(PX.parseBook(BOOK), "yes", "sell", 250);
  assert.equal(r.filled, 250);
  assert.equal(r.fills[0].price, 0.49);
  assert.equal(r.fills[0].contracts, 100);
  assert.equal(r.fills[1].price, 0.48);
  assert.equal(r.fills[1].contracts, 150);
  assert.equal(r.touch, 0.49);
});

t("fee: exact quadratic formula rounded up to $0.0001", () => {
  // 0.07 * C * P * (1-P)
  assert.equal(PX.takerFee(0.5, 1000), 17.5);   // 0.07*1000*0.25
  assert.equal(PX.takerFee(0.5, 1), 0.0175);    // 0.07*1*0.25
  assert.equal(PX.takerFee(0.3, 3), 0.0441);    // 0.07*3*0.21
  assert.equal(PX.takerFee(0.12, 7), 0.0518);   // 0.051744 -> ceil to $0.0001
  // price >= 1 or <= 0 -> 0 (flagged upstream, never silently priced)
  assert.equal(PX.takerFee(1.0, 10), 0);
  assert.equal(PX.takerFee(0, 10), 0);
});

t("affordable: cash covers notional + exact fee", () => {
  // cash 100, price 0.50: q=199 -> 99.5 + fee(0.5,199)=0.07*199*0.25=3.4825 -> 102.98 > 100
  // q=196 -> 98 + 3.43 = 101.43 > 100; q=195 -> 97.5 + 3.4125 -> 100.9125 > 100
  // q=194 -> 97 + 3.3925 = 100.3925 > 100; q=193 -> 96.5 + 3.3725 = 99.8725 <= 100
  assert.equal(PX.affordableContracts(100, 0.5), 193);
});

t("simulateBuy: blocked when no book support", () => {
  const empty = { orderbook_fp: { yes_dollars: [], no_dollars: [] } };
  const r = PX.simulateBuy(empty, "yes", 10, 0.99);
  assert.ok(r.blocked);
});

t("simulateBuy: full report on a real book", () => {
  const r = PX.simulateBuy(BOOK, "yes", 50, 0.99);
  assert.ok(r.ok);
  assert.equal(r.filled, 50);
  assert.equal(r.vwap, 0.51);
  assert.ok(Math.abs(r.fee - PX.takerFee(0.51, 50)) < 1e-9);
  assert.ok(r.fee > 0);
  assert.equal(r.slippage, 0); // filled entirely at the touch
});

t("settlement payout: $1 winner, $0 loser, refuses unknown result", () => {
  assert.equal(PX.settlementPayout("yes", 100, "yes"), 100);
  assert.equal(PX.settlementPayout("no", 100, "yes"), 0);
  assert.throws(() => PX.settlementPayout("yes", 100, ""));
});

console.log(`\n${passed} browser engine tests passed`);
