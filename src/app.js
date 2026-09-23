/* PRICINGEXPERT main app: loads committed, hash-bound data and renders the site.
 * All numbers on the page come from data/site/*.json — built by scripts/build_site.py
 * from committed evidence. This file renders; it does not compute returns.
 */
(function () {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));
  const fmtUsd = (x, digits = 2) => x === null || x === undefined
    ? "—"
    : (x < 0 ? "-$" : "$") + Math.abs(x).toLocaleString("en-US",
        { minimumFractionDigits: digits, maximumFractionDigits: digits });
  const fmtPct = (x) => x === null || x === undefined ? "—" : (x >= 0 ? "+" : "") + x.toFixed(2) + "%";
  const esc = (s) => String(s === null || s === undefined ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  const initials = (name) => name.slice(0, 2).toUpperCase();
  const sha8 = (s) => (s || "").slice(0, 8);

  const DATA = {
    leaderboard: null, strategies: [], research: null, verification: null, upcoming: null,
  };

  // ------------------------------------------------------------------ tabs
  function initTabs() {
    const params = new URLSearchParams(location.search);
    let tab = params.get("tab") || (location.hash || "#tab-board").replace("#tab-", "");
    if (!["board", "strategies", "livedesk", "ledger", "research", "verify"].includes(tab)) tab = "board";
    $$("#tabs button").forEach((btn) => {
      btn.addEventListener("click", () => showTab(btn.dataset.tab));
    });
    showTab(tab);
  }
  function showTab(tab) {
    $$(".section").forEach((s) => s.classList.toggle("active", s.id === "tab-" + tab));
    $$("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    location.hash = "#tab-" + tab;
  }

  // ------------------------------------------------------------------ board
  function renderBoard() {
    const lb = DATA.leaderboard;
    if (!lb) { $("#season-stats").innerHTML = '<div class="card">Loading…</div>'; return; }
    const c = lb.competition || {};
    const rows = lb.rows || [];
    const top = rows[0] || {};
    const totFills = rows.reduce((s, r) => s + (r.fills || 0), 0);
    const totOpen = rows.reduce((s, r) => s + (r.open_positions || 0), 0);
    const totSettled = rows.reduce((s, r) => s + (r.settlements || 0), 0);
    const dts = (ts) => (ts ? new Date(ts * 1000).toISOString().slice(0, 10) : "");
    $("#season-stats").innerHTML = [
      stat("Season", esc(c.season || "2026"), `${dts(c.start_ts)} → ${dts(c.end_ts)} UTC · $${Number(c.starting_cash || 0).toLocaleString()} start`),
      stat("Desk cycles", String(lb.cycles_completed || 0),
        lb.last_cycle ? `last ${esc(lb.last_cycle)} · ${esc(lb.last_cycle_at || "")}` : "pending first cycle"),
      stat("Verified fills", String(totFills), `${totOpen} open positions · ${totSettled} settlements`),
      stat("Leader", esc(top.username || "—"), top.equity !== undefined ? fmtUsd(top.equity) : ""),
    ].join("");
    const tbody = $("#board-table tbody");
    tbody.innerHTML = rows.map((r, i) => {
      const wl = (r.wins || 0) + "W " + (r.losses || 0) + "L";
      return `<tr>
        <td>${i + 1}</td>
        <td><span class="avatar">${initials(r.username)}</span>
          <b><a href="strategy.html?id=${encodeURIComponent(r.strategy_id)}">${esc(r.username)}</a></b><br>
          <span class="faint" style="font-size:11px">${esc(r.strategy_id)} · ${esc(tagOf(r.strategy_id))}</span></td>
        <td><b>${fmtUsd(r.equity)}</b></td>
        <td class="${r.return_pct >= 0 ? "pos" : "neg"}">${fmtPct(r.return_pct)}</td>
        <td class="${r.realized_pnl >= 0 ? "pos" : "neg"}">${fmtUsd(r.realized_pnl)}</td>
        <td class="faint">${fmtUsd(r.fees)} · ${fmtUsd(r.slippage)}</td>
        <td>${r.fills} / ${r.open_positions} / ${r.settlements}</td>
        <td>${wl}</td>
        <td>${sparkline(r.curve || [])}</td>
        <td class="ledger-link">
          <a href="data/season-2026/forward/state.json" target="_blank" rel="noopener">state ↗</a><br>
          <a href="data/season-2026/forward/trades.jsonl" target="_blank" rel="noopener">ledger ↗</a></td>
      </tr>`;
    }).join("") || '<tr><td colspan="10" class="dim">No desk cycles committed yet — the forward desk has not run.</td></tr>';
  }
  function tagOf(id) {
    const s = DATA.strategies.find((x) => x.id === id);
    return s ? s.name : "";
  }
  function stat(label, value, sub) {
    return `<div class="card"><div class="stat"><span class="label">${label}</span>${value}</div>
      <div class="faint" style="font-size:11px;margin-top:4px">${sub || ""}</div></div>`;
  }
  function sparkline(curve) {
    if (!curve.length) return '<span class="faint">—</span>';
    const w = 120, h = 28, pts = curve.map((p) => p.equity);
    const min = Math.min(...pts), max = Math.max(...pts);
    const range = max - min || 1;
    const step = pts.length > 1 ? w / (pts.length - 1) : 0;
    const coords = pts.map((v, i) =>
      `${(i * step).toFixed(1)},${(h - 3 - ((v - min) / range) * (h - 6)).toFixed(1)}`).join(" ");
    const up = pts[pts.length - 1] >= pts[0];
    return `<svg class="spark" width="${w}" height="${h}">
      <polyline points="${coords}" fill="none"
        stroke="${up ? "var(--green)" : "var(--red)"}" stroke-width="1.5"/></svg>`;
  }

  // ------------------------------------------------------------------ strategies
  function renderStrategies() {
    const lb = DATA.leaderboard || { rows: [] };
    const byId = {};
    lb.rows.forEach((r) => { byId[r.strategy_id] = r; });
    $("#strategy-cards").innerHTML = DATA.strategies.map((s) => {
      const r = byId[s.id] || {};
      const evTags = (s.evidence || []).map((e) =>
        `<span class="tag ${e.status === "verified-api-doc" ? "verified" : (e.status === "hypothesis-only" ? "hypothesis" : "modelled")}">
          ${esc(e.status)}</span>`).join("");
      return `<div class="card strategy-card" style="cursor:pointer" data-strat="${esc(s.id)}">
        <div class="row-flex">
          <span class="avatar">${initials(s.username)}</span>
          <div><b>${esc(s.username)}</b> <span class="faint">· ${esc(s.name)}</span>
          <div class="faint" style="font-size:11px">${esc(s.tag)} · ${s.status}</div></div>
          <div style="margin-left:auto;text-align:right">
            <b class="${(r.return_pct || 0) >= 0 ? "pos" : "neg"}">${fmtPct(r.return_pct || 0)}</b><br>
            <span class="faint" style="font-size:11px">${fmtUsd(r.equity)} · ${r.fills || 0} fills</span>
          </div>
        </div>
        <div class="rule"><b>Rule.</b> ${esc(s.rule)}</div>
        <div class="dim" style="font-size:12.5px"><b>Why.</b> ${esc(s.why)}
          <b> Failure mode.</b> ${esc(s.failure_mode)}</div>
        <div class="pill-row">${evTags}</div>
        <a href="strategy.html?id=${encodeURIComponent(s.id)}">Full page: every trade, curve, backtest, analysis →</a>
      </div>`;
    }).join("");
    $$("#strategy-cards [data-strat]").forEach((el) => {
      el.addEventListener("click", (e) => {
        if (e.target.tagName === "A") return;
        location.href = "strategy.html?id=" + encodeURIComponent(el.dataset.strat);
      });
    });
  }

  // ------------------------------------------------------------------ ledger
  function renderLedger() {
    const lb = DATA.leaderboard;
    const upcoming = DATA.upcoming || { rows: [] };
    loadJson("data/site/ledger-rows.json").then((lr) => {
      const all = (lr && lr.rows) || [];
      $("#ledger-pills").innerHTML = [
        pill("fills", all.filter((r) => r.type === "fill").length),
        pill("exits", all.filter((r) => r.type === "exit").length),
        pill("settlements", all.filter((r) => r.type === "settlement").length),
        pill("blocked intents", (upcoming.rows || []).filter((r) => r.kind === "blocked").length),
        pill("last cycle", (lb && lb.last_cycle) || "—"),
      ].join("");
      const tbody = $("#ledger-table tbody");
      tbody.innerHTML = all.slice(-400).reverse().map((t) => `<tr>
        <td><span class="tag ${t.type === "fill" ? "verified" : t.type === "settlement" ? "verified" : "modelled"}">${esc(t.type)}</span></td>
        <td class="faint">${esc(t.cycle)}</td>
        <td class="faint">${esc(t.at)}</td>
        <td><b>${esc(t.strategy)}</b></td>
        <td class="mono" style="font-size:11px">${esc(t.ticker)}${t.title ? ` <span class="faint">· ${esc(String(t.title).slice(0, 40))}…</span>` : ""}</td>
        <td>${esc(t.side || "—")}</td>
        <td>${t.contracts !== undefined ? Number(t.contracts).toLocaleString() : "—"}</td>
        <td>${t.vwap !== undefined ? "$" + t.vwap : "—"}</td>
        <td class="faint">${t.touch !== undefined ? "$" + t.touch : "—"}</td>
        <td class="faint">${t.fee !== undefined ? fmtUsd(t.fee, 4) : "—"}</td>
        <td class="faint">${t.slippage !== undefined ? fmtUsd(t.slippage) : "—"}</td>
        <td class="${(t.pnl || 0) >= 0 ? "pos" : "neg"}">${t.pnl !== undefined && t.pnl !== null ? fmtUsd(t.pnl) : "—"}</td>
        <td class="ledger-link mono" style="font-size:10px">${esc(t.evidence || "—")}</td>
      </tr>`).join("") || '<tr><td colspan="13" class="dim">No trades committed yet.</td></tr>';
    }).catch(() => { $("#ledger-pills").innerHTML = pill("ledger", "missing"); });

    const ut = $("#upcoming-table tbody");
    const urows = (upcoming.rows || []).slice(-100).reverse();
    ut.innerHTML = urows.map((r) => `<tr>
      <td class="faint">${esc(r.cycle)}</td>
      <td class="faint">${esc(r.at)}</td>
      <td><b>${esc(r.strategy)}</b></td>
      <td class="mono" style="font-size:11px">${esc(r.ticker)}</td>
      <td>${esc(r.side || (r.trigger && r.trigger.yes_ask !== undefined ? "BOTH" : "—"))}</td>
      <td>${r.limit !== undefined ? "$" + r.limit : (r.trigger && r.trigger.pair_cost !== undefined ? "pair ≤ $" + r.trigger.pair_cost : "—")}</td>
      <td class="faint" style="font-size:11px">${esc(JSON.stringify(r.trigger || {}))}</td>
      <td><span class="tag ${r.kind === "blocked" ? "blocked" : "hypothesis"}">${r.kind}</span></td>
    </tr>`).join("") || '<tr><td colspan="8" class="dim">No upcoming intents logged yet.</td></tr>';
  }
  function pill(label, value) {
    return `<span class="pill"><b>${esc(String(value))}</b> ${esc(label)}</span>`;
  }

  // ------------------------------------------------------------------ research
  let rfilter = "all";
  function renderResearch(filter) {
    if (filter) rfilter = filter;
    const R = DATA.research || { observations: [], public_info: [], hypotheses: [], tests: [], relationships: [] };
    const q = $("#research-search") ? ($("#research-search").value || "").toLowerCase() : "";
    const match = (obj) => !q || JSON.stringify(obj).toLowerCase().includes(q);
    const verifiedOnly = rfilter === "verified";
    const keep = (isVerified) => !verifiedOnly || isVerified;
    const obs = R.observations.filter(match); // every observation row is hash-bound = verified
    $("#obs-count").textContent = obs.length + " rows (all hash-bound to raw evidence)";
    $("#obs-table tbody").innerHTML = obs.slice(-300).reverse().map((o) => `<tr>
      <td class="faint">${esc(o.fetched_at)}</td>
      <td class="mono" style="font-size:11px">${esc(o.ticker)}<div class="faint" style="font-size:10px">${esc(o.title || "")}</div></td>
      <td>${esc(o.series)}</td>
      <td>${esc(o.status)}${o.result ? ` · <b>${esc(o.result)}</b>` : ""}</td>
      <td>${quoteCell(o.yes_bid, o.yes_ask)}</td>
      <td>${quoteCell(o.no_bid, o.no_ask)}</td>
      <td>${o.last !== null && o.last !== undefined ? "$" + o.last : "—"}</td>
      <td>${o.volume}</td>
      <td>${o.volume_24h}</td>
      <td class="faint">${o.close_ts ? new Date(o.close_ts * 1000).toISOString().slice(0, 16) : "—"}</td>
      <td>${o.result ? `<b class="${o.result === "yes" ? "pos" : "neg"}">${esc(o.result)}</b>` : "—"}</td>
      <td class="ledger-link mono" style="font-size:10px"><a href="${esc(o.provenance_path || ("data/provenance/" + o.provenance + ".meta.json"))}" target="_blank" rel="noopener">${sha8(o.sha256)} ↗</a></td>
    </tr>`).join("") || '<tr><td colspan="12" class="dim">No observations committed yet.</td></tr>';

    $("#pubinfo-table tbody").innerHTML = (R.public_info || []).filter(match)
      .filter((p) => keep(p.status === "verified")).map((p) => `<tr>
      <td class="faint">${esc(p.published || p.ts || "—")}</td>
      <td><a href="${esc(p.url || "#")}" target="_blank" rel="noopener">${esc(p.source)}</a></td>
      <td class="mono" style="font-size:11px">${esc(p.market || p.series || "—")}</td>
      <td style="font-size:12px">${esc(p.summary)}</td>
      <td class="faint">${esc(p.fetched_at || "—")}</td>
      <td><span class="tag ${p.status === "verified" ? "verified" : "hypothesis"}">${esc(p.status)}</span></td>
    </tr>`).join("") || '<tr><td colspan="6" class="dim">No public information recorded yet.</td></tr>';

    const hyps = (R.hypotheses || []).filter(match)
      .filter((h) => keep(h.status && h.status !== "untested"));
    $("#hyp-count").textContent = hyps.length;
    $("#hyp-list").innerHTML = hyps.map((h) => `<div class="card">
      <b>${esc(h.id)}</b> <span class="tag ${h.status === "untested" ? "hypothesis" : h.status === "refuted" ? "blocked" : "verified"}">${esc(h.status)}</span>
      <div style="margin-top:6px;font-size:13px">${esc(h.statement)}</div>
      <div class="faint" style="font-size:11.5px;margin-top:4px">Markets: ${esc((h.markets || []).join(", "))}<br>${esc(h.basis || "")}</div>
      ${(h.evidence || []).length ? `<div class="faint" style="font-size:11px;margin-top:4px">Evidence: ${h.evidence.map(esc).join(", ")}</div>` : ""}
    </div>`).join("") || '<div class="card dim">No hypotheses recorded yet.</div>';

    $("#test-table tbody").innerHTML = (R.tests || []).filter(match)
      .filter((t) => keep(t.verdict && t.verdict !== "inconclusive")).map((t) => `<tr>
      <td class="mono" style="font-size:11px">${esc(t.id)}</td>
      <td>${esc(t.hypothesis)}</td>
      <td class="mono" style="font-size:11px">${esc(t.market)}</td>
      <td>${esc(t.method)}</td>
      <td class="faint" style="font-size:11px">${esc(t.data_range || "")}<br><span class="mono" style="font-size:10px">${esc((t.data_files || []).slice(0, 3).join(", "))}</span></td>
      <td style="font-size:12px">${esc(t.result)}</td>
      <td><span class="tag ${t.verdict === "supported" ? "verified" : t.verdict === "refuted" ? "blocked" : "modelled"}">${esc(t.verdict)}</span></td>
    </tr>`).join("") || '<tr><td colspan="7" class="dim">No tests recorded yet.</td></tr>';

    const rels = (R.relationships || []).filter(match)
      .filter((r) => keep(r.verdict && r.verdict !== "hypothesis"));
    $("#rel-count").textContent = rels.length;
    $("#rel-list").innerHTML = rels.map((r) => `<div class="card">
      <b>${esc(r.id)}</b> <span class="tag ${r.verdict === "supported" ? "verified" : r.verdict === "refuted" || r.verdict === "coincidental" ? "blocked" : "hypothesis"}">${esc(r.verdict)}</span>
      <div style="margin-top:6px;font-size:13px">${esc(r.market_a)} ⟷ ${esc(r.market_b)}</div>
      <div class="dim" style="font-size:12.5px;margin-top:4px"><b>Claimed similarity.</b> ${esc(r.claimed_similarity)}</div>
      <div class="dim" style="font-size:12.5px;margin-top:4px"><b>Transfer test.</b> ${esc(r.transfer_test)}</div>
      <div class="faint" style="font-size:11.5px;margin-top:4px"><b>Verdict note.</b> ${esc(r.verdict_note)}</div>
    </div>`).join("") || '<div class="card dim">No cross-market relationships recorded yet.</div>';
  }
  function quoteCell(bid, ask) {
    if (bid === null || bid === undefined || ask === null || ask === undefined) return "—";
    return `$${bid} / $${ask}`;
  }

  // ------------------------------------------------------------------ verification
  function renderVerification() {
    const V = DATA.verification || {};
    const manifest = V.manifest || { files: {}, generated_at: "—" };
    const files = Object.entries(manifest.files || {});
    $("#manifest-count").textContent = files.length + " evidence files";
    $("#verify-summary").innerHTML = `<table class="compact"><tbody>
      <tr><td>Gate</td><td><code>scripts/verify.py</code> — 13 check groups (V1–V13): hashes, manifest,
        candle provenance, universe, cash recompute, exact fees, book prices, settlements, slippage,
        rule coverage, season config, links, ledger hygiene</td></tr>
      <tr><td>Manifest built</td><td>${esc(manifest.generated_at || "—")}
        <a class="ledger-link" href="data/site/link-audit.json" target="_blank" rel="noopener">link audit ↗</a></td></tr>
      <tr><td>Remote link checks</td><td>run in CI on every publish (<code>.github/workflows/pages.yml</code>)</td></tr>
      <tr><td>Simulation boundary</td><td>no API key exists in the repository; nothing here submits orders</td></tr>
    </tbody></table>`;
    $("#manifest-pre").textContent = files.map(([name, meta]) =>
      `${name}\n    url:  ${meta.url}\n    at:   ${meta.fetched_at}  sha256: ${meta.sha256}`).join("\n")
      || "no evidence files committed yet";
    const src = (V.source_registry || []);
    $("#src-table tbody").innerHTML = src.map((s) => `<tr>
      <td class="mono" style="font-size:11px">${esc(s.id)}</td>
      <td>${esc(s.kind)}</td>
      <td>${esc(s.source)}</td>
      <td class="ledger-link"><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.url.slice(0, 60))}…</a></td>
      <td>${s.verified_at ? `<span class="tag verified">${esc(s.verified_at)}</span>` : '<span class="tag hypothesis">pending</span>'}</td>
      <td class="faint" style="font-size:11px">${esc(s.notes || "")}</td>
    </tr>`).join("") || '<tr><td colspan="6" class="dim">source registry empty</td></tr>';
    const irr = V.irregularities || [];
    $("#irr-list").innerHTML = irr.map((i) => `<div class="card" style="border-color:rgba(210,153,34,.4)">
      <b class="warn">${esc(i.id)}</b> <span class="faint">· ${esc(i.ts)}</span>
      <div style="margin-top:6px;font-size:13px">${esc(i.description)}</div>
      <div class="faint" style="font-size:11.5px;margin-top:4px">status: ${esc(i.status)} · ${esc(i.detail || "")}</div>
    </div>`).join("") || '<div class="card dim">No irregularities logged — or none flagged yet this session.</div>';
    const master = V.master_site_projects || [];
    $("#master-list").innerHTML = master.map((m) => `<div class="card">
      <b><a href="${esc(m.url)}" target="_blank" rel="noopener">${esc(m.name)}</a></b>
      <div class="dim" style="font-size:12px;margin-top:4px">${esc(m.relevance)}</div>
    </div>`).join("") || '<div class="card dim">none</div>';
  }

  // ------------------------------------------------------------------ loader
  function loadJson(url) {
    return fetch(url, { cache: "no-store" }).then((r) => {
      if (!r.ok) throw new Error(url + " -> " + r.status);
      return r.json();
    });
  }
  function loadAll() {
    return Promise.all([
      loadJson("data/site/leaderboard.json"),
      loadJson("data/strategies.json"),
      loadJson("data/site/research.json").catch(() => null),
      loadJson("data/site/verification.json").catch(() => null),
      loadJson("data/site/upcoming.json").catch(() => null),
    ]).then(([lb, strategies, research, verification, upcoming]) => {
      DATA.leaderboard = lb; DATA.strategies = strategies; DATA.research = research;
      DATA.verification = verification; DATA.upcoming = upcoming;
      renderBoard(); renderStrategies(); renderLedger(); renderResearch(); renderVerification();
      if (typeof PXLive !== "undefined") PXLive.init();
      $("#research-search").addEventListener("input", () => renderResearch());
      $("#research-filter-verified").addEventListener("click", () => renderResearch("verified"));
      $("#research-filter-all").addEventListener("click", () => renderResearch("all"));
    }).catch((e) => {
      $("#season-stats").innerHTML =
        `<div class="card warn">Site data not built yet: ${esc(String(e))}.
        Run <code>python3 scripts/build_site.py</code> after committing evidence.</div>`;
    });
  }

  document.addEventListener("DOMContentLoaded", () => { initTabs(); loadAll(); });
})();
