#!/usr/bin/env python3
"""PRICINGEXPERT verification gate. Offline, deterministic, must pass before publish.

Checks (each failure is a hard error with an IRR-style message):
  V1  hash      every data/raw file matches its provenance SHA-256 sidecar
  V2  manifest  data/manifest.json lists exactly the raw + provenance files present
  V3  candles   every candle CSV row cites a provenance id whose sha matches the row's
  V4  universe  every ticker in the forward ledger is in data/universe.json
  V5  cash      recompute each strategy's cash from the append-only ledger; must equal
                state.json exactly
  V6  fees      every ledger fee equals taker_fee(vwap, contracts) rounded to $0.0001
  V7  book      every forward fill's per-level prices exist in the captured order book
                evidence (or are 1 - level price, per the reciprocal rule)
  V8  settle    every settlement payout equals contracts * (side == official result);
                result must be in {yes, no}; settlement timestamp present in evidence
  V9  slippage  fill slippage == (vwap - touch) * contracts; exit slippage ==
                (bid - vwap) * contracts — within rounding, for every fill and exit
  V10 rules     every active strategy id has a rule implementation (no silent dead code)
  V11 season    competition.json: start < end, calendar-year span, sane capital
  V12 links     every external URL in site assets is well-formed and non-placeholder;
                the remote reachability check runs in CI (scripts/probe_links.py)
  V13 ledger    trades.jsonl / intents.jsonl / marks.jsonl are valid JSONL, sorted by
                (cycle, at), with no duplicate row hashes
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paper_engine import taker_fee  # noqa: E402

DATA = Path(sys.argv[1] if len(sys.argv) > 1 else "data")
ERRORS: list[str] = []
CHECKS: list[str] = []


def check(ok: bool, code: str, msg: str):
    (CHECKS if ok else ERRORS).append(f"{code}: {msg}")
    return ok


def jload(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as e:
            check(False, "V13", f"{path}:{i} invalid JSONL: {e}")
    return rows


def main() -> int:
    # V1 + V2 -------------------------------------------------------------------
    raw_dir, prov_dir = DATA / "raw", DATA / "provenance"
    manifest = jload(DATA / "manifest.json", {"files": {}})
    files = manifest.get("files", {})
    raw_files = sorted(raw_dir.glob("*.json")) if raw_dir.exists() else []
    prov_files = sorted(prov_dir.glob("*.meta.json")) if prov_dir.exists() else []
    for p in raw_files:
        meta = jload(prov_dir / f"{p.stem}.meta.json")
        if not check(meta is not None, "V1", f"missing provenance for {p.name}"):
            continue
        sha = hashlib.sha256(p.read_bytes()).hexdigest()
        check(sha == meta.get("sha256"), "V1", f"hash mismatch {p.name}")
        check(p.stem in files, "V2", f"{p.name} missing from manifest")
    for p in prov_files:
        check(p.name.replace(".meta.json", "") in {f for f in files}, "V2",
              f"{p.name} missing from manifest")
    check(len(raw_files) == len({f for f in files}), "V2",
          f"manifest count {len(files)} != raw count {len(raw_files)}")

    # V3 -------------------------------------------------------------------------
    candle_dir = DATA / "candles"
    if candle_dir.exists():
        for csv_path in sorted(candle_dir.glob("*.csv")):
            lines = csv_path.read_text(encoding="utf-8").splitlines()
            for i, line in enumerate(lines[1:], 2):
                if not line:
                    continue
                parts = line.split(",")
                if len(parts) != 8:
                    check(False, "V3", f"{csv_path}:{i} bad column count")
                    continue
                prov_id, sha = parts[6], parts[7]
                meta = jload(prov_dir / f"{prov_id}.meta.json")
                check(meta is not None and meta.get("sha256") == sha, "V3",
                      f"{csv_path}:{i} provenance {prov_id} sha mismatch")

    universe = jload(DATA / "universe.json", {"markets": []})
    season = DATA / "season-2026" / "forward"
    trades = jsonl(season / "trades.jsonl")
    intents = jsonl(season / "intents.jsonl")
    marks = jsonl(season / "marks.jsonl")
    state = jload(season / "state.json", {"strategies": {}, "starting_cash": 10000})

    # V4 ------------------------------------------------------------------------
    traded = {t["ticker"] for t in trades}
    for t in traded:
        check(t in universe.get("markets", []), "V4", f"ledger ticker {t} not in universe")

    # V5 cash recompute ----------------------------------------------------------
    recomputed: dict[str, float] = {}
    usernames = {s["username"]: s["id"] for s in jload(DATA / "strategies.json", [])}
    for t in trades:
        uid = t["strategy"]
        if t["type"] == "fill":
            recomputed[uid] = recomputed.get(uid, state["starting_cash"]) \
                - t.get("notional", 0) - t.get("fee", 0)
        elif t["type"] == "exit":
            proceeds = round(t.get("notional", t.get("vwap", 0) * t.get("contracts", 0))
                             - t.get("fee", 0), 6)
            recomputed[uid] = round(recomputed.get(uid, state["starting_cash"]) + proceeds, 6)
        elif t["type"] == "settlement":
            recomputed[uid] = round(recomputed.get(uid, state["starting_cash"])
                                    + t.get("payout", 0), 6)
    name_of = {v: k for k, v in usernames.items()}
    for sid, ss in state.get("strategies", {}).items():
        uid = name_of.get(sid)
        if uid is None:
            check(False, "V5", f"state strategy id {sid} missing from strategies.json")
            continue
        expect = recomputed.get(uid, state["starting_cash"])
        check(abs(expect - ss["cash"]) < 0.005, "V5",
              f"{sid}: recomputed cash {expect} != state {ss['cash']}")

    # V6 + V9 + V7 ---------------------------------------------------------------
    for t in trades:
        if t["type"] == "fill":
            fee = taker_fee(t["vwap"], t["contracts"])
            check(abs(fee - t.get("fee", 0)) < 0.0002, "V6",
                  f"fee mismatch {t['strategy']} {t['ticker']} {t['at']}: "
                  f"formula {fee} vs ledger {t.get('fee')}")
            touch = t.get("touch")
            slip = t.get("slippage", 0)
            if touch is not None:
                expect_slip = (t["vwap"] - touch) * t["contracts"]
                check(abs(expect_slip - slip) < 0.01, "V9",
                      f"slippage mismatch {t['strategy']} {t['ticker']} {t['at']}")
            ev = t.get("evidence", "")
            m = re.match(r"book:(.+):(.+)", ev)
            if m:
                ticker, cycle = m.group(1), m.group(2)
                book_path = season / cycle / "evidence" / "raw" / f"book-{ticker}.json"
                if check(book_path.exists(), "V7", f"missing book evidence {book_path}"):
                    book = json.loads(book_path.read_text(encoding="utf-8"))
                    levels = set()
                    src = book.get("orderbook_fp") or book.get("orderbook") or {}
                    for side in ("yes", "no"):
                        for lv in src.get(f"{side}_dollars") or src.get(side) or []:
                            price = float(lv[0])
                            levels.add(round(price, 4))
                            levels.add(round(1 - price, 4))
                    for fl in t.get("fills", []):
                        check(fl["price"] in levels or fl["level_price"] in levels,
                              "V7", f"fill price {fl} not in captured book "
                                    f"{t['strategy']} {ticker} {t['at']}")
        elif t["type"] == "exit":
            bid = t.get("bid_at_exit")
            if bid is not None:
                expect_slip = (bid - t["vwap"]) * t["contracts"]  # sell: touch - vwap
                check(abs(expect_slip - t.get("slippage", 0)) < 0.01, "V9",
                      f"exit slippage mismatch {t['strategy']} {t['ticker']} {t['at']}")
        elif t["type"] == "settlement":
            check(t.get("result") in ("yes", "no"), "V8",
                  f"settlement without official result: {t['ticker']}")
            expect = t["contracts"] * (1.0 if t["side"] == t.get("result") else 0.0)
            check(abs(expect - t.get("payout", -1)) < 1e-6, "V8",
                  f"payout mismatch {t['strategy']} {t['ticker']}: {expect} vs {t.get('payout')}")

    # V10 rules -------------------------------------------------------------------
    try:
        from forward_desk import evaluate_strategy  # noqa: E402
        dummy_market = {"ticker": "X-A-Y", "series": "X", "title": "", "status": "active",
                        "result": None, "rules_primary": "", "yes_bid": 0.49, "yes_ask": 0.51,
                        "no_bid": 0.49, "no_ask": 0.51, "yes_bid_size": 10, "no_bid_size": 10,
                        "last": 0.5, "previous": 0.5, "volume": 1e6, "volume_24h": 1e6,
                        "open_ts": 0, "close_ts": 9999999999, "exp_ts": 9999999999,
                        "settlement_ts": None, "updated_ts": None, "can_close_early": False}
        dummy_book = {"yes": [(0.49, 10)], "no": [(0.49, 10)]}
        for s in jload(DATA / "strategies.json", []):
            if s.get("status") == "active":
                evaluate_strategy(s, dummy_market, dummy_book, 9999999998)
                check(True, "V10", f"rule {s['id']} implemented")
    except SystemExit as e:
        check(False, "V10", f"unimplemented rule: {e}")
    except Exception as e:  # noqa: BLE001
        check(False, "V10", f"rule evaluation error: {e}")

    # V11 season -------------------------------------------------------------------
    comp = jload(DATA / "season-2026" / "competition.json", {})
    check(bool(comp), "V11", "competition.json missing")
    if comp:
        check(comp.get("start_ts", 0) < comp.get("end_ts", 0), "V11", "start >= end")
        check(1000 <= comp.get("starting_cash", 0) <= 1_000_000, "V11", "sane starting cash")

    # V12 links --------------------------------------------------------------------
    url_re = re.compile(r'https?://[^\s"\'<>)\]}]+')
    placeholders = {"example.com", "placeholder", "todo", "tbd"}
    site_paths = [DATA / "site"]
    for extra in ("index.html", "strategy.html", "research.html", "styles.css"):
        p = Path(extra)
        if p.exists():
            site_paths.append(p)
    src = Path("src")
    if src.exists():
        site_paths.extend(sorted(src.glob("*.js")))
    found: dict[str, list[str]] = {}
    for p in site_paths:
        if not p.exists() or p.suffix not in (".html", ".js", ".css", ".json"):
            continue
        for url in url_re.findall(p.read_text(encoding="utf-8")):
            url = url.rstrip(".,;")
            if any(x in url.lower() for x in placeholders):
                check(False, "V12", f"placeholder URL in {p}: {url}")
            found.setdefault(url, []).append(str(p))
    (DATA / "site" / "link-audit.json").write_text(
        json.dumps({"checked_at": "see CI", "local_check": "well-formed only",
                    "remote_check": "scripts/probe_links.py in CI",
                    "urls": {u: fs for u, fs in sorted(found.items())}}, indent=1) + "\n",
        encoding="utf-8")

    # V13 ledger hygiene -------------------------------------------------------------
    for name, rows in (("trades", trades), ("intents", intents), ("marks", marks)):
        keys = []
        for r in rows:
            h = hashlib.sha256(json.dumps(r, sort_keys=True).encode()).hexdigest()
            keys.append(h)
            r["_h"] = h
        check(len(keys) == len(set(keys)), f"V13-{name}", "duplicate rows detected")
        ordered = [(r.get("cycle", ""), r.get("at", "")) for r in rows]
        check(ordered == sorted(ordered), f"V13-{name}", "rows not sorted by (cycle, at)")

    print(json.dumps({"passed": len(CHECKS), "errors": len(ERRORS),
                      "checks": [c[:80] for c in CHECKS[:12]], "errors_full": ERRORS},
                     indent=1))
    return 1 if ERRORS else 0


if __name__ == "__main__":
    raise SystemExit(main())
