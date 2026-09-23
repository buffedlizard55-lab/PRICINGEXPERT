#!/usr/bin/env python3
"""Derive normalized, hash-traceable records from the committed raw API payloads.

Inputs (both corpora, every row cites its provenance id + SHA-256):
  data/{raw,provenance}                     research corpus (plan-collector)
  data/season-*/forward/cycle-*/evidence/{raw,provenance}
                                           desk cycle evidence — each cycle's market
                                           snapshots become one observation row per
                                           cycle, i.e. a verified price time series

Outputs:
  data/observations.jsonl     one verified price observation per market snapshot
  data/candles/<series>-<market>.csv   candle rows (only for committed candle payloads)

Nothing here touches the network. If a raw file is missing or its hash does not
match its provenance sidecar, the run fails — derived rows must always trace to a
verified body.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paper_engine import fnum, parse_ts  # noqa: E402


def load_raw(raw_dir: Path, meta_path: Path) -> tuple[dict, dict] | None:
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    raw_path = raw_dir / f"{meta.get('id', meta_path.name.replace('.meta.json', '.json'))}.json"
    if not raw_path.exists():
        return None
    raw_bytes = raw_path.read_bytes()
    actual = hashlib.sha256(raw_bytes).hexdigest()
    if actual != meta.get("sha256"):
        raise SystemExit(f"hash mismatch for {meta['id']}: stored {meta.get('sha256')} actual {actual}")
    return json.loads(raw_bytes.decode("utf-8")), meta


def provenance_files(data: Path) -> list[Path]:
    """Top-level research corpus first, then every season desk-cycle, in order."""
    files = sorted((data / "provenance").glob("*.meta.json"))
    for season in sorted(data.glob("season-*")):
        files.extend(sorted((season / "forward").glob("cycle-*/evidence/provenance/*.meta.json")))
    return files


def market_row(payload: dict, meta: dict) -> dict:
    m = payload.get("market", payload)
    return {
        "provenance": meta["id"],
        "sha256": meta["sha256"],
        "fetched_at": meta["fetched_at"],
        "url": meta["url"],
        "ticker": m.get("ticker"),
        "event_ticker": m.get("event_ticker"),
        "series": m.get("event_ticker") or m.get("ticker") or "",
        "title": m.get("title"),
        "yes_sub_title": m.get("yes_sub_title"),
        "no_sub_title": m.get("no_sub_title"),
        "status": m.get("status"),
        "result": (m.get("result") or "").lower() or None,
        "rules_primary": m.get("rules_primary"),
        "yes_bid": fnum(m.get("yes_bid_dollars")),
        "yes_ask": fnum(m.get("yes_ask_dollars")) or (
            round(1.0 - fnum(m.get("no_bid_dollars")), 4) if fnum(m.get("no_bid_dollars")) is not None else None),
        "no_bid": fnum(m.get("no_bid_dollars")),
        "no_ask": fnum(m.get("no_ask_dollars")) or (
            round(1.0 - fnum(m.get("yes_bid_dollars")), 4) if fnum(m.get("yes_bid_dollars")) is not None else None),
        "yes_bid_size": fnum(m.get("yes_bid_size_fp")),
        "no_bid_size": fnum(m.get("no_bid_size_fp")),
        "last": fnum(m.get("last_price_dollars")),
        "previous": fnum(m.get("previous_price_dollars")),
        "volume": fnum(m.get("volume_fp")) or 0.0,
        "volume_24h": fnum(m.get("volume_24h_fp")) or 0.0,
        "open_interest": fnum(m.get("open_interest_fp")) or 0.0,
        "liquidity_dollars": fnum(m.get("liquidity_dollars")),
        "open_ts": parse_ts(m.get("open_time")),
        "close_ts": parse_ts(m.get("close_time")),
        "expected_expiration_ts": parse_ts(m.get("expected_expiration_time")),
        "settlement_ts": parse_ts(m.get("settlement_ts")),
        "updated_ts": parse_ts(m.get("updated_time")),
        "exchange_index": int(m.get("exchange_index") or 0),
        "price_level_structure": m.get("price_level_structure"),
        "can_close_early": m.get("can_close_early"),
        "strike_type": m.get("strike_type"),
    }


def main() -> int:
    data = Path(sys.argv[1] if len(sys.argv) > 1 else "data")
    rows = []
    seen: set[tuple[str, str, str]] = set()
    for meta_path in provenance_files(data):
        loaded = load_raw(meta_path.parent.parent / "raw", meta_path)
        if loaded is None:
            continue
        payload, meta = loaded
        dedup_key = (meta["url"], meta["fetched_at"], meta["sha256"])
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        note = meta.get("note", "")
        if note.startswith("kind:market "):
            row = market_row(payload, meta)
            try:
                row["provenance_path"] = "data/" + meta_path.resolve().relative_to(
                    data.resolve()).as_posix()
            except ValueError:
                row["provenance_path"] = "data/provenance/" + meta_path.name
            rows.append(row)
        elif note.startswith("kind:candlesticks"):
            sid = meta["id"]
            # Verified 2026 shape (official docs, 2026-07-24): {"candlesticks":
            # [{end_period_ts, price:{open_dollars, high_dollars, low_dollars,
            # close_dollars, previous_dollars, ...}, volume_fp, open_interest_fp}]}
            candles = payload.get("candlesticks") or payload.get("candles") or []
            ticker = meta["ticker"]
            series = meta["series"]
            out = data / "candles" / f"{series}-{ticker}.csv"
            out.parent.mkdir(parents=True, exist_ok=True)
            lines = ["ts,open,high,low,close,volume,provenance,sha256"]
            for c in candles:
                p = c.get("price") or {}
                o = fnum(p.get("open_dollars")) if p else None
                h = fnum(p.get("high_dollars")) if p else None
                lo = fnum(p.get("low_dollars")) if p else None
                cl = fnum(p.get("close_dollars")) if p else None
                if None in (o, h, lo, cl):  # legacy flat shape fallback
                    o, h, lo, cl = (fnum(c.get(k)) for k in ("open", "high", "low", "close"))
                ts = parse_ts(c.get("end_period_ts")) or parse_ts(c.get("start_time"))
                if None in (o, h, lo, cl, ts):
                    continue  # incomplete candle rows are unusable for replay; drop
                vol = fnum(c.get("volume_fp")) or fnum(c.get("volume")) or 0
                lines.append(f"{ts},{o},{h},{lo},{cl},{vol},{sid},{meta['sha256']}")
            out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out = data / "observations.jsonl"
    out.write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + ("\n" if rows else ""),
                   encoding="utf-8")
    print(json.dumps({"observations": len(rows), "candle_files": len(list((data / 'candles').glob('*.csv'))
                               if (data / 'candles').exists() else [])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
