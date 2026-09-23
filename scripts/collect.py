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
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kalshi_client import KalshiClient, KalshiError  # noqa: E402

TOOL = "scripts/collect.py (python urllib, read-only)"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def fetch_step(client: KalshiClient, step: dict) -> tuple[bytes, str, str] | None:
    kind = step["kind"]
    params = step.get("params") or {}
    if kind == "exchange_status":
        payload, raw, url = client.get("exchange/status")
        return raw, url, payload
    if kind == "historical_cutoff":
        payload, raw, url = client.get("historical/cutoff")
        return raw, url, payload
    if kind == "series_list":
        payload, raw, url = client.get("series", {"limit": params.get("limit")})
        return raw, url, payload
    if kind == "series":
        payload, raw, url = client.get(f"series/{params['ticker']}")
        return raw, url, payload
    if kind == "markets":
        payload, raw, url = client.get("markets", {
            "status": params.get("status"),
            "series_ticker": params.get("series_ticker"),
            "min_close_ts": params.get("min_close_ts"),
            "max_close_ts": params.get("max_close_ts"),
            "limit": params.get("limit", 200)})
        return raw, url, payload
    if kind == "market":
        payload, raw, url = client.market(params["ticker"])
        return raw, url, payload
    if kind == "orderbook":
        payload, raw, url = client.orderbook(params["ticker"], depth=params.get("depth", 50))
        return raw, url, payload
    if kind == "trades":
        payload, raw, url = client.trades(params.get("ticker"), limit=params.get("limit", 500),
                                          min_ts=params.get("min_ts"), max_ts=params.get("max_ts"))
        return raw, url, payload
    if kind == "candlesticks":
        payload, raw, url = client.candlesticks(params["series"], params["ticker"],
                                                params["start_ts"], params["end_ts"],
                                                params.get("period_interval", 3600))
        return raw, url, payload
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
        if args.only and sid not in args.only:
            skipped += 1
            continue
        try:
            raw, url, _payload = fetch_step(client, step)
        except Exception as error:  # noqa: BLE001 - one bad step must not kill the run
            calls.append({"id": sid, "status": "error", "error": str(error)[:300],
                          "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
            failed += 1
            continue
        raw_path = out / "raw" / f"{sid}.json"
        meta = {
            "id": sid,
            "url": url,
            "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "http_status": 200,
            "tool": TOOL,
            "note": step.get("note", ""),
        }
        if step["kind"] == "candlesticks":
            meta["ticker"] = params["ticker"]
            meta["series"] = params["series"]
        if step["kind"] == "trades":
            meta["ticker"] = params.get("ticker")
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(raw)
        dump(out / "provenance" / f"{sid}.meta.json", meta)
        calls.append({"id": sid, "status": 200, "bytes": len(raw), "sha256": meta["sha256"],
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
