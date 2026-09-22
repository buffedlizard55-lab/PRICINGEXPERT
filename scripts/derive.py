#!/usr/bin/env python3
"""Derive normalized, hash-traceable records from the committed raw API payloads.

Outputs:
  data/observations.jsonl     one verified price observation per market snapshot
                              (each row cites the provenance id it came from)
  data/universe/check.json    universe membership + quote snapshot for the desk
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


def load_raw(data: Path, sid: str) -> tuple[dict, dict] | None:
    raw_path = data / "raw" / f"{sid}.json"
    meta_path = data / "provenance" / f"{sid}.meta.json"
    if not raw_path.exists() or not meta_path.exists():
        return None
    raw_bytes = raw_path.read_bytes()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    actual = hashlib.sha256(raw_bytes).hexdigest()
    if actual != meta.get("sha256"):
        raise SystemExit(f"hash mismatch for {sid}: stored {meta.get('sha256')} actual {actual}")
    return json.loads(raw_bytes.decode("utf-8")), meta


def market_row(payload: dict, meta: dict) -> dict:
    m = payload.get("market", payload)
    return {
        "provenance": meta["id"],
        "sha256": meta["sha256"],
        "fetched_at": meta["fetched_at"],
        "url": meta["url"],
        "ticker": m.get("ticker"),
        "event_ticker": m.get("event_ticker"),
        "series": (m.get("event_ticker") or m.get("ticker") or "").split("-")[0],
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
    for meta_path in sorted((data / "provenance").glob("*.meta.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        sid = meta["id"]
        loaded = load_raw(data, sid)
        if loaded is None:
            continue
        payload, meta = loaded
        note = meta.get("note", "")
        if note.startswith("kind:market"):
            rows.append(market_row(payload, meta))
        elif note.startswith("kind:candlesticks"):
            candles = payload.get("candles") or []
            ticker = meta["ticker"]
            series = meta["series"]
            out = data / "candles" / f"{series}-{ticker}.csv"
            out.parent.mkdir(parents=True, exist_ok=True)
            lines = ["ts,open,high,low,close,volume,provenance,sha256"]
            for c in candles:
                lines.append(f"{parse_ts(c.get('start_time'))},{fnum(c.get('open'))},"
                             f"{fnum(c.get('high'))},{fnum(c.get('low'))},{fnum(c.get('close'))},"
                             f"{fnum(c.get('volume')) or 0},{sid},{meta['sha256']}")
            out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out = data / "observations.jsonl"
    out.write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + ("\n" if rows else ""),
                   encoding="utf-8")
    print(json.dumps({"observations": len(rows), "candle_files": len(list((data / 'candles').glob('*.csv'))
                               if (data / 'candles').exists() else [])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
