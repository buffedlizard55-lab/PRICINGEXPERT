/* Renders one strategy page from data/site/strategies/<id>.json */
(function () {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s === null || s === undefined ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  const fmtUsd = (x, d = 2) => x === null || x === undefined ? "—"
    : (x < 0 ? "-$" : "$") + Math.abs(x).toLocaleString("en-US",
        { minimumFractionDigits: d, maximumFractionDigits: d });
  const fmtPct = (x) => x === null || x === undefined ? "—" : (x >= 0 ? "+" : "") + x.toFixed(2) + "%";

  function curveSvg(curve, startCash) {
    if (!curve || !curve.length) return '<span class="faint">no curve yet</span>';
    const w = 760, h = 160, pad = 8;
    const pts = curve.map((p) => p.equity).concat([startCash]);
    const min = Math.min(...pts), max = Math.max(...pts);
    const range = max - min || 1;
    const n = curve.length;
    const x = (i) => pad + (n > 1 ? (i * (w - 2 * pad)) / (n - 1) : 0);
    const y = (v) => h - pad - ((v - min) / range) * (h - 2 * pad);
    const coords = curve.map((p, i) => `${x(i).toFixed(1)},${y(p.equity).toFixed(1)}`).join(" ");
    const baseY = y(Math.max(min, startCash));
    return `<svg width="100%" viewBox="0 0 ${w} ${h}" style="max-width:${w}px">
      <line x1="${pad}" y1="${baseY}" x2="${w - pad}" y2="${baseY}" stroke="var(--line)" stroke-dasharray="4 4"/>
      <text x="${pad}" y="${baseY - 4}" fill="var(--ink-faint)" font-size="10">start ${fmtUsd(startCash, 0)}</text>
      <text x="${w - pad}" y="${12}" fill="var(--ink-faint)" font-size="10" text-anchor="end">${fmtUsd(curve[n - 1].equity)}</text>
      <polyline points="${coords}" fill="none" stroke="${curve[n - 1].equity >= startCash ? "var(--green)" : "var(--red)"}" stroke-width="2"/>
    </svg>`;
  }

  async function main() {
    const id = new URLSearchParams(location.search).get("id");
    const page = $("#page");
    if (!id) { page.innerHTML = '<div class="card warn">Missing ?id= parameter.</div>'; return; }
    let s;
    try {
      const r = await fetch("data/site/strategies/" + encodeURIComponent(id) + ".json", { cache: "no-store" });
      if (!r.ok) throw new Error(r.status);
      s = await r.json();
    } catch (e) {
      page.innerHTML = `<div class="card warn">Strategy payload not found for ${esc(id)} (${esc(e.message)}).
        Run <code>python3 scripts/build_site.py</code> after committing evidence.</div>`;
      return;
    }
    document.title = s.username + " — PRICINGEXPERT";
    $("#crumb").textContent = s.username + " · " + s.name;
    const st = s.stats || {};
    page.innerHTML = `
      <h2><span class="avatar" style="width:44px;height:44px;font-size:15px">${esc(s.username.slice(0, 2).toUpperCase())}</span>
        ${esc(s.username)} <span class="faint">· ${esc(s.name)}</span>
        <span class="tag ${s.tag}">${esc(s.tag)}</span> <span class="tag">${esc(s.status)}</span></h2>
      <div class="grid c4" style="margin:14px 0">
        <div class="card"><div class="stat"><span class="label">Equity (mark)</span>${fmtUsd(st.equity)}</div>
          <div class="${(st.return_pct || 0) >= 0 ? "pos" : "neg"}" style="font-size:13px">${fmtPct(st.return_pct)}</div></div>
        <div class="card"><div class="stat"><span class="label">Realized PnL</span>${fmtUsd(st.realized_pnl)}</div>
          <div class="faint" style="font-size:12px">${st.wins}W / ${st.losses}L · ${st.settlements} settled</div></div>
        <div class="card"><div class="stat"><span class="label">Costs</span>${fmtUsd((st.fees || 0) + (st.slippage || 0))}</div>
          <div class="faint" style="font-size:12px">fees ${fmtUsd(st.fees)} · slippage ${fmtUsd(st.slippage)}</div></div>
        <div class="card"><div class="stat"><span class="label">Activity</span>${st.fills} fills</div>
          <div class="faint" style="font-size:12px">${st.open_positions} open · ${st.exits} exits</div></div>
      </div>
      <div class="card">
        <h3>Rule (exact)</h3>
        <div style="font-size:14px">${esc(s.rule)}</div>
        <h3 style="margin-top:14px">Why it should (or should not) work</h3>
        <div class="dim" style="font-size:13.5px">${esc(s.why)}</div>
        <h3 style="margin-top:14px">How it fails</h3>
        <div class="dim" style="font-size:13.5px">${esc(s.failure_mode)}</div>
        <h3 style="margin-top:14px">Evidence status</h3>
        ${(s.evidence || []).map((e) => `<div style="margin:6px 0;font-size:13px">
          <span class="tag ${e.status === "verified-api-doc" ? "verified" : e.status === "hypothesis-only" ? "hypothesis" : "modelled"}">${esc(e.status)}</span>
          ${e.url ? `<a href="${esc(e.url)}" target="_blank" rel="noopener">source ↗</a> ` : ""}${esc(e.claim)}</div>`).join("")}
        <h3 style="margin-top:14px">Parameters</h3>
        <div class="mono" style="font-size:12px">${esc(JSON.stringify(s.params))}</div>
      </div>
      <h2 style="margin-top:26px">Equity curve <span class="badge">per desk cycle (start + realized-so-far + open mark)</span></h2>
      <div class="card">${curveSvg(s.curve, st.starting_cash || 10000)}</div>
      <h2 style="margin-top:26px">Analysis — what the ledger says</h2>
      <div class="card"><div style="font-size:14px">${esc(s.analysis)}</div></div>
      <h2 style="margin-top:26px">Open positions</h2>
      <div class="card"><table class="compact"><thead><tr><th>Market</th><th>Side</th><th>Contracts</th>
        <th>Entry VWAP</th><th>Entry (UTC)</th><th>Mark</th><th>Value</th><th>Mark basis</th></tr></thead>
        <tbody>${(s.positions || []).map((p) => `<tr>
          <td class="mono" style="font-size:11px">${esc(p.ticker)}</td>
          <td><b>${esc(p.side)}</b></td><td>${Number(p.contracts).toLocaleString()}</td>
          <td>${p.entry_vwap ? "$" + p.entry_vwap : "—"}</td><td class="faint">${esc(p.entry_at)}</td>
          <td>${p.mark !== null && p.mark !== undefined ? "$" + p.mark : "—"}</td>
          <td>${p.value !== null && p.value !== undefined ? fmtUsd(p.value) : "—"}</td>
          <td class="faint">${esc(p.mark_kind)}</td></tr>`).join("")
          || '<tr><td colspan="8" class="dim">flat</td></tr>'}</tbody></table></div>
      <h2 style="margin-top:26px">Backtest (verified candle data only)</h2>
      <div class="card"><table class="compact"><thead><tr><th>Market</th><th>Bars</th><th>Trades</th>
        <th>Realized PnL</th><th>Return</th><th>Fees</th><th>Validation (last 40%)</th><th>Verdict</th></tr></thead>
        <tbody>${(s.backtest || []).map((b) => `<tr>
          <td class="mono" style="font-size:11px">${esc(b.market)}</td><td>${b.candles}</td><td>${b.n}</td>
          <td class="${b.realized_pnl >= 0 ? "pos" : "neg"}">${fmtUsd(b.realized_pnl)}</td>
          <td class="${b.return_pct >= 0 ? "pos" : "neg"}">${fmtPct(b.return_pct)}</td>
          <td class="faint">${fmtUsd(b.fees)}</td>
          <td class="faint">n=${b.validation_n} · ${fmtUsd(b.validation_pnl)}</td>
          <td><span class="tag ${b.verdict === "supported" ? "verified" : b.verdict === "refuted" ? "blocked" : "modelled"}">${esc(b.verdict)}</span></td>
        </tr>`).join("") || '<tr><td colspan="8" class="dim">not backtest-eligible on committed data (or no candles captured yet)</td></tr>'}</tbody></table>
        <div class="faint" style="font-size:11px;margin-top:8px">Candle-replay fills are
        <b>candle-proxied</b> (signal on close, fill at next open, documented buffer) — they are NOT
        book-verified fills. Forward fills on the board are.</div></div>
      <h2 style="margin-top:26px">Every trade (committed ledger)</h2>
      <div class="card"><table class="compact"><thead><tr><th>Type</th><th>Cycle</th><th>Time (UTC)</th>
        <th>Market</th><th>Side</th><th>Contracts</th><th>VWAP</th><th>Touch</th><th>Fee</th>
        <th>Slippage</th><th>PnL</th><th>Trigger / evidence</th></tr></thead>
        <tbody>${(s.trades || []).slice().reverse().map((t) => `<tr>
          <td><span class="tag ${t.type === "fill" ? "verified" : "modelled"}">${esc(t.type)}</span></td>
          <td class="faint">${esc(t.cycle)}</td><td class="faint">${esc(t.at)}</td>
          <td class="mono" style="font-size:11px">${esc(t.ticker)}</td><td>${esc(t.side || "—")}</td>
          <td>${t.contracts !== undefined ? Number(t.contracts).toLocaleString() : "—"}</td>
          <td>${t.vwap !== undefined ? "$" + t.vwap : "—"}</td>
          <td class="faint">${t.touch !== undefined ? "$" + t.touch : "—"}</td>
          <td class="faint">${t.fee !== undefined ? fmtUsd(t.fee, 4) : "—"}</td>
          <td class="faint">${t.slippage !== undefined ? fmtUsd(t.slippage) : "—"}</td>
          <td class="${(t.pnl || 0) >= 0 ? "pos" : "neg"}">${t.pnl !== undefined && t.pnl !== null ? fmtUsd(t.pnl) : "—"}</td>
          <td class="faint" style="font-size:10px">${esc(JSON.stringify(t.trigger || {}))}<br>${esc(t.evidence || "")}</td>
        </tr>`).join("") || '<tr><td colspan="12" class="dim">no committed trades yet</td></tr>'}</tbody></table></div>
      <h2 style="margin-top:26px">Recent intents (upcoming / blocked)</h2>
      <div class="card"><table class="compact"><thead><tr><th>Cycle</th><th>Time</th><th>Market</th>
        <th>Side</th><th>Limit</th><th>Trigger</th><th>State</th></tr></thead>
        <tbody>${(s.intents || []).slice().reverse().map((t) => `<tr>
          <td class="faint">${esc(t.cycle)}</td><td class="faint">${esc(t.at)}</td>
          <td class="mono" style="font-size:11px">${esc(t.ticker)}</td><td>${esc(t.side || (t.trigger && t.trigger.yes_ask !== undefined ? "BOTH" : "—"))}</td>
          <td>${t.limit !== undefined ? "$" + t.limit : "—"}</td>
          <td class="faint" style="font-size:10px">${esc(JSON.stringify(t.trigger || {}))}</td>
          <td><span class="tag ${t.type === "intent-blocked" ? "blocked" : "hypothesis"}">${esc(t.type || "proposed")}</span></td>
        </tr>`).join("") || '<tr><td colspan="7" class="dim">none</td></tr>'}</tbody></table></div>`;
  }
  main();
})();
