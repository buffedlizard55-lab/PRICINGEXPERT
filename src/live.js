/* PRICINGEXPERT live desk — the explicit "simulate placing real trades" section.
 *
 * Pricing sources, in strict order of truth (no silent fallbacks):
 *   1. LIVE: unauthenticated GETs from the official Kalshi public API in the
 *      browser (the same calls the collector uses). If your network allows them,
 *      every price you see is the exchange's current quote, fetched seconds ago.
 *   2. COMMITTED SNAPSHOT: when the browser cannot reach the API (CORS/network),
 *      the desk loads the latest committed cycle evidence and labels every price
 *      with its capture timestamp. It never presents a stale snapshot as live.
 *
 * Simulated fills walk the captured/live book exactly like scripts/paper_engine.py
 * (depth-bounded, limit-bounded, exact Kalshi fee). A simulated fill is logged to
 * this browser's localStorage only — it never touches the competition ledger.
 */
(function () {
  "use strict";
  const API = "https://api.elections.kalshi.com/trade-api/v2";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s === null || s === undefined ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  let mode = "pending"; // live | snapshot | error
  let liveBooks = {};   // ticker -> orderbook payload (live)
  let marketsList = []; // live active markets (or snapshot)
  let currentBook = null;
  let currentMarket = null;

  function setMode(m, detail) {
    mode = m;
    const dot = m === "live" ? "ok" : m === "snapshot" ? "pending" : "err";
    const text = m === "live"
      ? "LIVE official pricing — browser is reading api.elections.kalshi.com directly"
      : m === "snapshot"
        ? `COMMITTED SNAPSHOT — latest verified capture ${detail || ""} (API not reachable from this browser)`
        : `API UNREACHABLE from this browser — showing committed snapshot if available (${detail || "no detail"})`;
    $("#live-status").innerHTML =
      `<span class="pill"><span class="status-dot ${dot}"></span>${esc(text)}</span>` +
      `<span class="pill">source: <b>official Kalshi public API</b> (unauthenticated reads only)</span>` +
      `<span class="pill">your simulated fills are stored <b>in this browser only</b></span>`;
  }

  async function fetchJson(url) {
    const r = await fetch(url, { cache: "no-store" });
    if (!r.ok) throw new Error(url + " -> HTTP " + r.status);
    return r.json();
  }

  async function refreshList() {
    try {
      const payload = await fetchJson(API + "/markets?status=active&limit=100");
      marketsList = (payload.markets || [])
        .filter((m) => parseFloat(m.liquidity_dollars || 0) > 0 || parseFloat(m.volume_fp || 0) > 0)
        .slice(0, 60);
      setMode("live", new Date().toISOString().slice(0, 19) + "Z");
    } catch (e) {
      try {
        const snap = await fetchJson("data/site/live-snapshot.json");
        marketsList = snap.markets || [];
        liveBooks = snap.books || {};
        setMode("snapshot", snap.captured_at ? new Date(snap.captured_at * 1000).toISOString().slice(0, 19) + "Z" : "");
      } catch (e2) {
        setMode("error", e2.message);
        return;
      }
    }
    const list = $("#market-list");
    list.innerHTML = marketsList.map((m) =>
      `<option value="${esc(m.ticker)}">${esc(m.ticker)} — ${esc((m.yes_sub_title || m.title || "").slice(0, 60))}</option>`
    ).join("");
    if ($("#market-picker").value) loadMarket($("#market-picker").value);
  }

  async function loadMarket(ticker) {
    const box = $("#market-detail");
    box.innerHTML = '<span class="dim">Loading ' + esc(ticker) + '…</span>';
    try {
      if (mode === "live") {
        const [m, b] = await Promise.all([
          fetchJson(API + "/markets/" + encodeURIComponent(ticker)),
          fetchJson(API + "/markets/" + encodeURIComponent(ticker) + "/orderbook?depth=25"),
        ]);
        currentMarket = m.market;
        currentBook = b;
        liveBooks[ticker] = b;
      } else {
        const snap = await fetchJson("data/site/live-snapshot.json");
        currentMarket = (snap.markets || []).find((m) => m.ticker === ticker) || null;
        currentBook = (snap.books || {})[ticker] || null;
      }
      renderMarketDetail();
    } catch (e) {
      box.innerHTML = `<span class="warn">Failed to load ${esc(ticker)}: ${esc(e.message)}.
        Verify manually: <a href="https://kalshi.com/markets" target="_blank" rel="noopener">kalshi.com ↗</a></span>`;
    }
  }

  function renderMarketDetail() {
    const m = currentMarket;
    const box = $("#market-detail");
    if (!m) { box.innerHTML = '<span class="dim">Market not in the current snapshot.</span>'; return; }
    const f = (x) => (x === null || x === undefined) ? "—" : "$" + x;
    const d = (ts) => ts ? new Date(ts).toISOString() : "—";
    const book = currentBook ? PX.bookQuotes(PX.parseBook(currentBook)) : null;
    box.innerHTML = `
      <div class="row-flex"><b class="mono" style="font-size:12px">${esc(m.ticker)}</b>
        <span class="tag ${m.status === "active" ? "verified" : "blocked"}">${esc(m.status)}</span></div>
      <div style="font-size:13px;margin:6px 0">${esc(m.title || "")}</div>
      <table class="compact"><tbody>
        <tr><td>YES bid / ask</td><td><b>${f(book ? book.yes_bid : m.yes_bid_dollars)}</b> / ${f(book ? book.yes_ask : m.yes_ask_dollars)}</td>
        <td>NO bid / ask</td><td><b>${f(book ? book.no_bid : m.no_bid_dollars)}</b> / ${f(book ? book.no_ask : m.no_ask_dollars)}</td></tr>
        <tr><td>Last trade</td><td>${f(m.last_price_dollars)}</td>
        <td>Previous close</td><td>${f(m.previous_price_dollars)}</td></tr>
        <tr><td>Volume (contracts)</td><td>${Number(m.volume_fp || 0).toLocaleString()}</td>
        <td>Volume 24h</td><td>${Number(m.volume_24h_fp || 0).toLocaleString()}</td></tr>
        <tr><td>Open interest</td><td>${Number(m.open_interest_fp || 0).toLocaleString()}</td>
        <td>Displayed liquidity</td><td>${f(m.liquidity_dollars)}</td></tr>
        <tr><td>Open time</td><td class="faint">${d(m.open_time)}</td>
        <td>Close / settle</td><td class="faint">${d(m.close_time)} ${m.settlement_ts ? "· " + d(m.settlement_ts) : ""}</td></tr>
      </tbody></table>
      <div class="faint" style="font-size:11px;margin-top:6px">
        ${mode === "live" ? "fetched from the official API seconds ago in your browser" :
          "from the latest committed cycle evidence — not live"} ·
        rules: ${esc(String(m.rules_primary || "—").slice(0, 180))}${String(m.rules_primary || "").length > 180 ? "…" : ""}
      </div>`;
    if (currentBook) {
      const b = PX.parseBook(currentBook);
      box.innerHTML += bookLadder(b, "YES bids");
      box.innerHTML += bookLadder(b, "NO bids");
    }
  }

  function bookLadder(book, label) {
    const levels = (label.startsWith("YES") ? book.yes : book.no).slice(-10).reverse();
    if (!levels.length) return "";
    const maxQty = Math.max(...levels.map((l) => l[1]));
    return `<div style="margin-top:8px;font-size:11px" class="faint"><b>${label}</b></div>` +
      levels.map((l) => `<div style="display:flex;align-items:center;gap:8px;font-size:11px">
        <span style="width:52px" class="mono">$${l[0]}</span>
        <span style="width:70px;text-align:right" class="mono">${Number(l[1]).toLocaleString()}</span>
        <span style="height:8px;background:var(--blue);opacity:.5;width:${Math.max(2, (l[1] / maxQty) * 120)}px;border-radius:2px"></span>
      </div>`).join("");
  }

  function simulate() {
    const side = $("#sim-side").value;
    const size = parseInt($("#sim-size").value, 10);
    const limit = parseFloat($("#sim-limit").value);
    const out = $("#sim-result");
    if (!currentBook) {
      out.innerHTML = '<span class="warn">Load a market first (a captured order book is required — no book, no fill, ever).</span>';
      return;
    }
    const report = PX.simulateBuy(currentBook, side, size, Number.isFinite(limit) ? limit : null);
    logSim(side, size, limit, report);
    if (report.blocked) {
      out.innerHTML = `<div class="card warn"><b>BLOCKED — ${esc(report.blocked)}</b>
        ${report.ask !== undefined ? `<div>executable ask ${esc(report.ask)} vs your limit ${esc(report.limit)}</div>` : ""}
        <div class="faint" style="font-size:11px">A blocked intent is the honest answer: the displayed book does not support the order, so nothing is invented.</div></div>`;
      return;
    }
    out.innerHTML = `<div class="card">
      <b class="pos">SIMULATED FILL ${side.toUpperCase()}</b>
      <table class="compact"><tbody>
        <tr><td>Requested / filled</td><td>${report.requested} / <b>${report.filled}</b>${report.unfilled ? ` <span class="warn">(${report.unfilled} unfilled — depth or limit bound)</span>` : ""}</td></tr>
        <tr><td>VWAP</td><td><b>$${report.vwap.toFixed(4)}</b></td>
        <tr><td>Touch</td><td>$${report.touch}</td></tr>
        <tr><td>Slippage vs touch</td><td>$${report.slippage}</td></tr>
        <tr><td>Notional</td><td>$${report.notional}</td></tr>
        <tr><td>Kalshi taker fee (exact)</td><td>$${report.fee.toFixed(4)}</td></tr>
        <tr><td>Total cost</td><td><b>$${report.total_cost}</b></td></tr>
      </tbody></table>
      <div class="faint" style="font-size:11px;margin-top:6px">Level fills: ${report.levels.map((l) => `$${l.price} × ${l.contracts}`).join(" · ")}</div>
      <div class="faint" style="font-size:11px">Pricing basis: ${mode === "live" ? "LIVE official order book (this browser)" : "committed snapshot book"}. Simulation only — no order was sent anywhere.</div>
    </div>`;
  }

  function logSim(side, size, limit, report) {
    let log = [];
    try { log = JSON.parse(localStorage.getItem("px-sim-log") || "[]"); } catch (e) { log = []; }
    log.push({ at: new Date().toISOString(), mode, side, size, limit,
      blocked: report.blocked || null, filled: report.filled || 0, vwap: report.vwap || null,
      fee: report.fee || null, slippage: report.slippage || null });
    if (log.length > 500) log = log.slice(-500);
    localStorage.setItem("px-sim-log", JSON.stringify(log));
  }

  function init() {
    const section = $("#tab-livedesk");
    const start = () => { refreshList(); };
    // kick off when the tab is first shown (and immediately if already visible)
    if (section.classList.contains("active")) start();
    const obs = new MutationObserver(() => {
      if (section.classList.contains("active") && mode === "pending") { obs.disconnect(); start(); }
    });
    obs.observe(section, { attributes: true, attributeFilter: ["class"] });
    $("#refresh-all").addEventListener("click", refreshList);
    $("#load-market").addEventListener("click", () => loadMarket($("#market-picker").value));
    $("#market-picker").addEventListener("keydown", (e) => {
      if (e.key === "Enter") loadMarket($("#market-picker").value);
    });
    $("#sim-run").addEventListener("click", simulate);
  }

  globalThis.PXLive = { init, refreshList, setMode };
})();
