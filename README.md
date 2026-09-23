# PRICINGEXPERT — Kalshi Paper-Trading Competition & Research Desk

An **evidence-first** paper-trading competition and research database for Kalshi event
contracts. A field of strategy "users" (each a unique username with one exact rule)
competes on paper over a calendar-year season. Every fill consumes a **captured official
Kalshi order book** (depth-bounded, limit-bounded, exact fees, slippage measured), every
settlement uses the **official result**, and every number on the site traces to a
**committed, SHA-256-bound evidence file** with its exact API URL and timestamp.

> **Simulation only.** Nothing in this repository submits orders to Kalshi. No API key
> exists anywhere in the project. All market data comes from the unauthenticated public
> API at `https://api.elections.kalshi.com/trade-api/v2`.

Site (after merge to `main` + Pages deploy): <https://buffedlizard55-lab.github.io/PRICINGEXPERT/>
· Repo: <https://github.com/buffedlizard55-lab/PRICINGEXPERT>

---

## 1. What it does (requirement → where)

| Requirement | Where |
|---|---|
| Research a topic, understand pricing/public info/timing/market behavior, generate strategies | `data/research/` (hypotheses, relationships), `data/public-info.jsonl`, strategy personas in `data/strategies.json`; research page on the site |
| Backtest only on real verified pricing | `scripts/backtest.py` replays rules over committed candle payloads (each candle row cites the API payload SHA-256); results in `data/season-2026/backtest/results.json` + mirrored into the research DB as tests |
| Forward-test when backtest data is unavailable | The forward desk (`scripts/forward_desk.py`) trades the captured books of open markets every cycle; every fill is logged with its evidence pointer |
| Cross-market: does a pattern transfer, or is similarity coincidental? | `data/research/relationships.json` — every claimed similarity carries an **independent transfer test on the second market's own verified data** and a verdict (supported / refuted / coincidental / untested). Untested stays untested |
| Expanding research & idea database (verified pricing, public info, timing, behavior, strategies, results, market relationships) | `data/observations.jsonl`, `data/public-info.jsonl` (source + timestamp + market), `data/research/*`, searchable on the site's Research DB tab (verified evidence and hypotheses are stored and labelled separately) |
| A section explicitly for simulating placing real trades on open Kalshi contracts with real verified pricing, dates, liquidity | The **Live Desk** tab: reads live official prices in the browser (or falls back to the latest committed snapshot, clearly labelled), shows the order book with sizes, and simulates a depth-bounded fill with the exact Kalshi fee |
| Track every strategy's trades with verified pricing, dates, entries, exits, PnL; slippage, bid sizing, liquidity in real time | Append-only ledgers `data/season-2026/forward/trades.jsonl` (fills/exits/settlements with VWAP, touch, fee, slippage, PnL, evidence SHA pointer), `intents.jsonl` (upcoming trades), `marks.jsonl` (per-cycle marks); Trade Ledger tab + per-strategy pages |
| Read and review all placed trades and all upcoming trades | Trade Ledger tab (placed) + Live Desk tab "Upcoming trades" (intents + blocked intents) + per-strategy pages |
| Paper-trading competition leaderboard, one competition per year, real-time prices, proper liquidity, market-making and pricing, official Kalshi API + other official free commodity APIs | `data/season-2026/competition.json` (season = calendar year, UTC), leaderboard tab, `src/live.js` (live pricing + simulated market interaction), source registry entries for FRED/NWS/BLS (official free APIs; see §7 limitations for keys) |
| Unique usernames, each with a specific strategy + explanation of why it worked / didn't | `data/strategies.json` (10 personas) + per-strategy pages with a **ledger-derived analysis** (the text is generated from the ledger numbers, never hand-written) |
| Reverse-engineered from public paper-trading competitions (Kalshi, TradingView LEAP, Trade-Ideas, CandleCharts) | Board tab "Reference competitions" with official rule links; design basis recorded in `data/source-registry.json` |
| Verify prices/dates/entries/exits with real verified official pricing; links for manual review | Every evidence file is a verbatim API response; `data/manifest.json` lists file → URL → fetched_at → SHA-256; the Verification tab renders it; `scripts/probe_links.py` and `scripts/probe_endpoints.py` run in CI |
| Track social-media strategies for forward testing | Strategy evidence entries carry status tags (`literature-linked`, `hypothesis-only`, `verified-api-doc`); third-party claims (e.g. Reddit backtest posts) may only enter as discovery-only inputs, never as price sources — the forward ledger *is* the test |
| No manual input; flag irregularities; no hallucinations | The collector is automated (`data-collect.yml`, `forward-desk.yml`); `data/irregularities.jsonl` logs flagged items (IRR-01…); the verification gate refuses to pass on any mismatch (§4) |

## 2. Repository layout

```
index.html, strategy.html, styles.css, src/     GitHub Pages site (SPA + per-strategy pages)
scripts/
  paper_engine.py      shared execution math (reciprocal quotes, ladder walk, fees, slippage)
  kalshi_client.py     read-only public-API client with call log (URL, sha256 per call)
  collect.py           plan-driven evidence collector (verbatim raw + provenance sidecars)
  make_cycle_plan.py   generates the next cycle's evidence plan from the universe
  forward_desk.py      one desk cycle: intents → fills → exits → settlements → marks
  backtest.py          candle-replay backtest with walk-forward split + pre-registered verdicts
  derive.py            raw payloads (research corpus + every desk-cycle evidence
                       dir) → observations.jsonl (verified price time series) /
                       candle CSVs (hash-checked)
  build_site.py        rebuilds all data/site/*.json deterministically
  make_manifest.py     regenerates data/manifest.json
  verify.py            the verification gate (V1–V13)
  probe_links.py       CI: reachability of every external URL in site assets
  probe_endpoints.py   CI: liveness + field-presence of every official endpoint used
data/
  raw/ + provenance/   verbatim API payloads + (url, fetched_at, sha256) sidecars
  manifest.json        file → URL → timestamp → SHA-256
  universe.json        tradable tickers (desk trades ONLY these; V4 enforces)
  strategies.json      the 10 competition personas (rule + why + failure mode + evidence)
  observations.jsonl   derived verified price observations (each cites its payload)
  public-info.jsonl    public information: source, timestamp, market, relationship
  research/            hypotheses.json, tests.json, relationships.json (cross-market)
  irregularities.jsonl flagged findings for human review
  season-2026/
    competition.json   season config (calendar year, UTC, $10,000 start, max-return scoring)
    forward/           cycle-NNN/evidence/* (verbatim books+markets per cycle),
                       trades.jsonl, intents.jsonl, marks.jsonl, state.json
    backtest/          results.json (candle-replay, walk-forward)
  site/                published JSON for the site (leaderboard, strategy pages,
                       research, verification, upcoming, live snapshot fallback)
.github/workflows/
  pages.yml            "Pages gate": tests + V1-V13 gate + deterministic rebuild
                       re-check + remote probes on every push to main
  forward-desk.yml     cron :07/:37 UTC — collect evidence, run cycle, verify, commit
  data-collect.yml     plan-triggered collection (fires only when
                       data/collect_plan.json changes — no loop) + manual dispatch
# Site deploy: the repo's Pages source is "Deploy from a branch" (main, /) — the
# committed site IS the main branch. The Pages gate workflow verifies what main is
# about to serve. (If the source is switched to "GitHub Actions", pages.yml's
# rebuild job becomes the published artifact.)
tests/                 engine parity (node) + engine (python) + forward-desk cycle tests
```

## 3. Execution model (the exchange's documented mechanics, implemented)

1. **Reciprocal quoting.** Kalshi binary books are reported as YES bids and NO bids.
   A YES ask is `1 − best NO bid`; a NO ask is `1 − best YES bid`.
2. **Taker crosses.** A taker BUY of side S consumes the *opposite* side's bid ladder
   (buying YES at `1 − p` against a NO bid `p`); a taker SELL consumes S's own bid
   ladder.
3. **Fees.** The published quadratic taker fee `0.07 · C · P · (1−P)`, rounded **up**
   to `$0.0001`. Settlement pays $1.00/$0.00 with no settlement fee.
4. **Limits & depth.** Orders are immediate-or-cancel limits. Levels worse than the
   limit are never touched; depth beyond the order is reported `unfilled`, never
   invented. **No captured book ⇒ no fill, ever** (blocked intents are logged).
5. **Slippage.** Cost versus the touch, where the touch is the best executable price
   at send time from the same captured book: a buy pays `(VWAP − touch) · contracts`;
   a sell pays `(touch − VWAP) · contracts` (touch = best bid). Both are ≥ 0 by
   construction — slippage is a cost, never a gain.
6. **Marks.** Open positions mark at the captured best bid (`mark_kind: bid`); the
   last-trade mark is used only when no bid exists and is flagged `mark_kind: last-trade`.

Parity: `tests/engine.test.mjs` (browser) and `tests/test_paper_engine.py` (desk) pin
both implementations to the same hand-computed fixture fills.

## 4. Verification contract (no hallucinations)

- **V1–V2** every raw file's SHA-256 matches its provenance sidecar; the manifest lists
  exactly the evidence present.
- **V3** every candle CSV row cites a provenance id whose hash matches.
- **V4** every ledger ticker is in `universe.json` (which is populated only from
  verified API payloads).
- **V5** each strategy's cash is **recomputed from the append-only ledger** and must
  equal `state.json`.
- **V6** every ledger fee equals the exact formula. **V9** slippage matches
  `(vwap − touch)·contracts` for fills and `(bid − vwap)·contracts` for exits.
  **V7** every per-level fill price exists in the captured book (or is `1 −` a
  captured level, per the reciprocal rule).
- **V8** settlements use the official `result` field only; payout = contracts × winner.
- **V10** every active strategy has a rule implementation (no dead personas).
- **V11** season config sanity. **V12** link well-formedness locally; CI probes
  remote reachability of every external URL. **V13** ledger JSONL hygiene (no dupes).

**Manual review, line by line:** open the Verification tab → each manifest row gives the
exact URL (e.g. `https://api.elections.kalshi.com/trade-api/v2/markets/{ticker}`), the
UTC capture time, and the SHA-256. Re-fetch the URL (settled data is immutable; quotes
move, so compare the committed body's hash for the fields that don't change) or open the
market on <https://www.kalshi.com> and compare dates/rules. Every trade row on the site
carries its `evidence` pointer into the cycle directory.

## 5. Running it

```bash
python3 tests/test_paper_engine.py      # engine unit tests
node tests/engine.test.mjs              # browser engine parity
python3 tests/test_forward_desk.py      # full cycle lifecycle on synthetic evidence
bash tests/run_e2e.sh                   # full 3-cycle e2e proof in an isolated
                                        # workdir: 2 in-season cycles + 1 post-season
                                        # (season-freeze check) -> derive -> backtest
                                        # -> build -> verify (must be 0 errors)
python3 scripts/new_season.py 2026      # bootstrap a season
python3 scripts/make_cycle_plan.py data # next cycle plan (universe-driven)
python3 scripts/collect.py --plan <plan> --out data   # collect evidence (needs egress)
python3 scripts/forward_desk.py <cycle_dir> data/season-2026 data/strategies.json [now_epoch]
python3 scripts/derive.py data && python3 scripts/backtest.py data
python3 scripts/make_manifest.py data && python3 scripts/build_site.py data
python3 scripts/verify.py data          # the gate — must pass before publishing
python3 -m http.server 8000             # local preview of the site
```

Automated: after merge to `main`, `forward-desk.yml` runs at :07/:37 UTC — plan →
collect → cycle → derive → backtest → build → verify → commit. The site always renders
the last **committed** state; the Live Desk tab additionally attempts live reads in the
browser and tells you which mode it is in (LIVE vs COMMITTED SNAPSHOT).

## 6. Competition rules (Season 2026)

- One competition per calendar year: **2026-01-01T00:00Z → 2027-01-01T00:00Z** (UTC).
- Every strategy starts with **$10,000** and trades the verified universe only.
- Objective: **highest return.** Risk management is deliberately not a criterion — the
  board ranks raw return, and PennyLotto (the penny-longshot lottery) exists precisely
  so the board can show what naive return-seeking costs.
- Scoring: equity = cash + open positions marked per §3.6. Fees and slippage are
  tracked per strategy and shown next to every return.
- Season end: open positions are marked, the board freezes, and the next season starts
  fresh state with the same personas (rollover design; `competition.json` documents it).

## 7. Known limitations (honest list, for the next session)

1. **Browser CORS to the Kalshi API is unverified from this sandbox.** The Live Desk
   attempts live reads and degrades to the committed snapshot with a visible label; if
   the API disallows cross-origin reads from GitHub Pages, the desk will run in
   SNAPSHOT mode for visitors (the collector keeps updating the snapshot every 30 min).
   First visitor browser result is the test; it self-reports in the pill row.
2. **No API key anywhere (by design).** Account-level data (your own orders) is out of
   scope; this is a public-market research desk.
3. **Candle-history depth.** The backtest can only replay what the candle endpoint
   returns for committed markets. The collector's candle reads (hourly, per universe
   market) build the archive cycle by cycle; longer histories arrive as they are
   captured. Backtest fills are candle-proxied (signal on close, fill at next open,
   2c buffer) and are labelled as such — they are never presented as book-verified.
4. **Other official free commodity APIs.** FRED needs a free API key (none stored);
   NWS (`api.weather.gov`) and BLS public endpoints are keyless — both are registered in
   `data/source-registry.json` with the rule that any use must commit verbatim payloads
   with provenance. No price in the site today comes from anything but the Kalshi API.
5. **Session network constraint (IRR-01/02).** This build session collected evidence via
   the platform fetch route / the plan-triggered Actions collector because the sandbox's
   egress is allowlisted; the permanent route is the Actions collector, which runs on
   runners with full egress and is covered by the endpoint probe in CI.
6. **Sample sizes.** With a young season, verdicts are mostly `inconclusive` by design
   (pre-registered: ≥5 validation trades needed for supported/refuted). The board is a
   living object; re-read it after each cycle.

## 8. Sources (verified in this session unless marked)

- Kalshi public API — live-verified: <https://api.elections.kalshi.com/trade-api/v2/exchange/status>
- Kalshi API reference: <https://trading-api.readme.io/reference>
- Kalshi (manual review portal): <https://www.kalshi.com>
- TradingView The Leap rules: <https://www.tradingview.com/the-leap/tradestation-july-2026/rules/>
- Trade Ideas competition: <https://www.trade-ideas.com/stock-trading-competition/>
- CandleCharts contest: <https://specials.candlecharts.com/contest/> (IRR-03: not fully retrieved this session)
- CEPR/VoxEU prediction-market column (literature-linked, not a price source): <https://cepr.org/voxeu/columns/economics-kalshi-prediction-market>
- MasterSite directory: <https://buffedlizard55-lab.github.io/MasterSite/>
- Sister project (referenced line-by-line): <https://github.com/buffedlizard55-lab/Commodities>
