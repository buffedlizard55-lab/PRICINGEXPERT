#!/usr/bin/env python3
"""Evidence collector: fetch the planned official API endpoints and store every
response verbatim with a provenance sidecar (URL, fetched_at UTC, SHA-256, bytes).

Run environment: anything with network egress (the GitHub Actions runner, or this
session's manual route). The committed raw/ and provenance/ directories are the
ONLY price evidence the desk and the site may use.

Usage:
  python3 scripts/collect.py --plan data/collect_plan.json --out data
  python3 scripts/collect.py --only markets-active book-KX... --plan ... --out data

Every endpoint id in the plan becomes:
  data/raw/<id>.json             verbatim response body
  data/provenance/<id>.meta.json {url, fetched_at, sha256, bytes, http_status, tool}
  data/calls.json                append-style call log (one JSON array)

Pagination (verified against the live host, 2026-09-23): /markets ignores an
`offset` query param and paginates by `cursor`. A step may therefore carry
  "params": {"limit": 200, "paginate": {"max_pages": 15}}
and each page is stored verbatim as <id>-pNN.json with its own provenance
sidecar (per-page URL + SHA-256). No derived/concatenated files are written to
raw/ — raw/ holds only verbatim API responses; consumers merge pages themselves.

series_pick (rolling-contract desks): the universe is SERIES, not fixed tickers
(hourly/daily contracts rotate). A step
  {"id": "pick-KXGOLDD", "kind": "series_pick",
   "params": {"series_ticker": "KXGOLDD", "limit": 50,
              "top_n": 1, "close_margin_sec": 5400, "depth": 50}}
fetches the series market list, applies scripts/market_pick.select_tickers
(the single documented selection policy), and captures the market object +
orderbook of each selected contract:
  <id>.json                    verbatim series market list
  <id>-market-<T>.json         verbatim market object for selected contract T
  <id>-book-<T>.json           verbatim orderbook for T (fill evidence)
The selection (tickers) and the timestamp the policy used are recorded in the
<id>.meta.json sidecar (meta["selected"], meta["pick_now_ts"]); the desk and
verify.py re-derive the same list from the same payload.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kalshi_client import KalshiClient, KalshiError  # noqa: E402
from market_pick import select_tickers  # noqa: E402

TOOL = "scripts/collect.py (python urllib, read-only)"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def markets_query(params: dict) -> dict:
    q = {
        "status": params.get("status"),
        "series_ticker": params.get("series_ticker"),
        "min_close_ts": params.get("min_close_ts"),
        "max_close_ts": params.get("max_close_ts"),
        "limit": params.get("limit", 200)}
    return q


def fetch_step(client: KalshiClient, step: dict) -> list[tuple[str, bytes, str, str | None]]:
    """Fetch one step; return a list of (file_id, raw, url, note) where note is
    a per-capture provenance-note override (None = use the step note).
    Pagination yields <id>-pNN; series_pick yields the series list plus
    -market-<T> / -book-<T> captures for each selected contract."""
    kind = step["kind"]
    params = step.get("params") or {}
    sid = step["id"]
    if kind == "exchange_status":
        payload, raw, url = client.get("exchange/status")
        return [(sid, raw, url, None)]
    if kind == "historical_cutoff":
        payload, raw, url = client.get("historical/cutoff")
        return [(sid, raw, url, None)]
    if kind == "series_list":
        payload, raw, url = client.get("series", {"limit": params.get("limit")})
        return [(sid, raw, url, None)]
    if kind == "series":
        payload, raw, url = client.get(f"series/{params['ticker']}")
        return [(sid, raw, url, None)]
    if kind == "markets":
        out: list[tuple[str, bytes, str, str | None]] = []
        cursor = params.get("cursor")
        max_pages = int((params.get("paginate") or {}).get("max_pages", 1))
        for pi in range(1, max_pages + 1):
            q = markets_query(params)
            if cursor:
                q["cursor"] = cursor
            payload, raw, url = client.get("markets", q)
            file_id = sid if max_pages == 1 else f"{sid}-p{pi:02d}"
            out.append((file_id, raw, url, None))
            cursor = payload.get("cursor")
            if not cursor:
                break
        return out
    if kind == "market":
        payload, raw, url = client.market(params["ticker"])
        return [(sid, raw, url, None)]
    if kind == "orderbook":
        payload, raw, url = client.orderbook(params["ticker"], depth=params.get("depth", 50))
        return [(sid, raw, url, None)]
    if kind == "trades":
        payload, raw, url = client.trades(params.get("ticker"), limit=params.get("limit", 500),
                                          min_ts=params.get("min_ts"), max_ts=params.get("max_ts"))
        return [(sid, raw, url, None)]
    if kind == "candlesticks":
        payload, raw, url = client.candlesticks(params["series"], params["ticker"],
                                                params["start_ts"], params["end_ts"],
                                                params.get("period_interval", 3600))
        return [(sid, raw, url, None)]
    if kind == "series_pick":
        series = params["series_ticker"]
        payload, raw, url = client.get("markets", markets_query({"series_ticker": series,
                                                                "limit": params.get("limit", 50)}))
        top_n = int(params.get("top_n", 1))
        margin = int(params.get("close_margin_sec", 5400))
        out: list[tuple[str, bytes, str, str | None]] = [
            (sid, raw, url, f"kind:series_pick — series {series} market list; selection policy "
                            "in scripts/market_pick.py (recorded in this file's meta)")]
        now_ts = int(time.time())
        picks = select_tickers(payload, now_ts, close_margin_sec=margin, top_n=top_n)
        # recorded in the step's meta by main() via step["_pick_meta"]
        step["_pick_meta"] = {"selected": picks, "pick_now_ts": now_ts, "series": series,
                              "top_n": top_n, "close_margin_sec": margin}
        for t in picks:
            mp, mraw, murl = client.market(t)
            out.append((f"{sid}-market-{t}", mraw, murl,
                        f"kind:market — market object for {t} (series_pick {series})"))
            bp, brow, burl = client.orderbook(t, depth=int(params.get("depth", 50)))
            out.append((f"{sid}-book-{t}", brow, burl,
                        f"kind:orderbook — captured book for {t} (series_pick {series}, fill evidence)"))
        return out
    raise SystemExit(f"unknown endpoint kind {kind!r}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--out", required=True, help="data directory")
    ap.add_argument("--only", nargs="*", default=None, help="collect only these endpoint ids")
    args = ap.parse_args()

    out = Path(args.out)
    plan = load_json(Path(args.plan))
    client = KalshiClient()
    calls: list[dict] = []
    ok = failed = skipped = 0
    for step in plan.get("endpoints", []):
        sid = step["id"]
        params = step.get("params") or {}
        if args.only and sid not in args.only:
            skipped += 1
            continue
        try:
            captures = fetch_step(client, step)
        except Exception as error:  # noqa: BLE001 - one bad step must not kill the run
            calls.append({"id": sid, "status": "error", "error": str(error)[:300],
                          "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
            failed += 1
            continue
        for file_id, raw, url, note_override in captures:
            meta = {
                "id": file_id,
                "url": url,
                "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw),
                "http_status": 200,
                "tool": TOOL,
                "note": note_override or step.get("note", ""),
            }
            if step.get("kind") == "series_pick" and file_id == sid:
                meta.update(step.get("_pick_meta") or {})
            if step["kind"] == "candlesticks":
                meta["ticker"] = params["ticker"]
                meta["series"] = params["series"]
            if step["kind"] == "trades":
                meta["ticker"] = params.get("ticker")
            raw_path = out / "raw" / f"{file_id}.json"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(raw)
            dump(out / "provenance" / f"{file_id}.meta.json", meta)
            calls.append({"id": file_id, "status": 200, "bytes": len(raw),
                          "sha256": meta["sha256"],
                          "at": meta["fetched_at"], "url": url})
            ok += 1
    (out / "calls.json").write_text(json.dumps(calls, indent=1) + "\n", encoding="utf-8")
    report = {"ok": ok, "failed": failed, "skipped": skipped,
              "calls_logged": len(calls),
              "failures": [c for c in calls if c.get("status") == "error"],
              "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    (out / "collect-report.json").write_text(json.dumps(report, indent=1) + "\n",
                                             encoding="utf-8")
    print(json.dumps(report))
    # Always exit 0: diagnostics live in collect-report.json, which the workflow
    # commits back to the branch so failures are readable without log access.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
