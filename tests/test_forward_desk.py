"""Forward desk cycle tests on synthetic (but mechanically valid) evidence.
Run: python3 tests/test_forward_desk.py
"""
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from forward_desk import run_cycle  # noqa: E402
from paper_engine import STARTING_CASH  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def write_evidence(cycle_dir: Path, tid: str, payload: dict, note: str, extra_meta: dict | None = None):
    raw = json.dumps(payload).encode()
    ev = cycle_dir / "evidence"
    (ev / "raw").mkdir(parents=True, exist_ok=True)
    (ev / "provenance").mkdir(parents=True, exist_ok=True)
    (ev / "raw" / f"{tid}.json").write_bytes(raw)
    meta = {"id": tid, "url": "synthetic://test/" + tid, "fetched_at": "2026-09-22T00:00:00Z",
            "sha256": sha(raw), "bytes": len(raw), "http_status": 200,
            "tool": "test", "note": note, "ticker": payload.get("market", payload).get("ticker", ""),
            "series": "TEST"}
    if extra_meta:
        meta.update(extra_meta)
    (ev / "provenance" / f"{tid}.meta.json").write_text(json.dumps(meta), encoding="utf-8")


def market_payload(ticker, status="active", result="", yb=0.88, ya=0.90, nb=0.10, na=0.12,
                   last=0.89, prev=0.85, vol=20000, vol24=8000, exp_ts=10_000_000_000):
    return {"market": {
        "ticker": ticker, "event_ticker": "TEST-EVT-" + ticker, "title": "test market " + ticker,
        "yes_sub_title": "up", "no_sub_title": "down", "status": status, "result": result,
        "rules_primary": "settles YES if...", "yes_bid_dollars": f"{yb:.4f}",
        "yes_ask_dollars": f"{ya:.4f}", "no_bid_dollars": f"{nb:.4f}", "no_ask_dollars": f"{na:.4f}",
        "yes_bid_size_fp": "5000", "no_bid_size_fp": "5000",
        "last_price_dollars": f"{last:.4f}", "previous_price_dollars": f"{prev:.4f}",
        "volume_fp": f"{vol:.2f}", "volume_24h_fp": f"{vol24:.2f}", "open_interest_fp": "1200",
        "liquidity_dollars": "9000.0000", "open_time": "2026-09-01T00:00:00Z",
        "close_time": "2026-12-31T22:00:00Z",
        "expected_expiration_time": "2026-12-31T21:00:00Z",
        "updated_time": "2026-09-22T00:00:00Z", "exchange_index": 0,
    }}


def book_payload(ticker, yes_levels, no_levels):
    return {"market_ticker": ticker, "orderbook_fp": {
        "yes_dollars": [[p, q] for p, q in yes_levels],
        "no_dollars": [[p, q] for p, q in no_levels],
    }}


NOW = 1_790_000_000  # inside the season, far from exp_ts (2026-12-31)


class TestForwardDesk(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.season = self.tmp / "season-2026"
        (self.season / "forward").mkdir(parents=True)
        # universe
        (self.tmp / "universe.json").write_text(json.dumps(
            {"markets": ["TEST-A-1", "TEST-B-2"]}), encoding="utf-8")
        # strategies: a subset of the real file
        strategies = json.loads((REPO / "data" / "strategies.json").read_text(encoding="utf-8"))
        self.strat_path = self.tmp / "strategies.json"
        self.strat_path.write_text(json.dumps(strategies), encoding="utf-8")
        # initial state for the real strategy ids
        state = {"season": "2026", "starting_cash": STARTING_CASH, "cycles_completed": 0,
                 "strategies": {s["id"]: {"cash": STARTING_CASH, "positions": {}, "realized_pnl": 0.0,
                                          "fees_paid": 0.0, "slippage_paid": 0.0, "wins": 0,
                                          "losses": 0, "fills": 0, "exits": 0, "settlements": 0}
                                 for s in strategies}}
        (self.season / "forward" / "state.json").write_text(json.dumps(state), encoding="utf-8")
        for name in ("trades.jsonl", "intents.jsonl", "marks.jsonl"):
            (self.season / "forward" / name).write_text("", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_cycle(self, name="cycle-001") -> Path:
        cycle = self.season / "forward" / name
        # Market A: heavy favourite 88-90c with big volume -> sure-penny / last-mile family
        write_evidence(cycle, "market-TEST-A-1",
                       market_payload("TEST-A-1", yb=0.88, ya=0.90, nb=0.10, na=0.12,
                                      last=0.89, prev=0.85, vol=50000, vol24=20000),
                       "kind:market synthetic")
        write_evidence(cycle, "book-TEST-A-1",
                       book_payload("TEST-A-1",
                                    yes_levels=[(0.86, 500), (0.87, 900), (0.88, 2000)],
                                    no_levels=[(0.09, 500), (0.10, 1200), (0.11, 3000)]),
                       "kind:orderbook")
        # Market B: mid market 50c with momentum (prev 47 -> last 50.5) -> tape-rider
        write_evidence(cycle, "market-TEST-B-2",
                       market_payload("TEST-B-2", yb=0.50, ya=0.52, nb=0.48, na=0.50,
                                      last=0.505, prev=0.47, vol=9000, vol24=4000),
                       "kind:market synthetic")
        write_evidence(cycle, "book-TEST-B-2",
                       book_payload("TEST-B-2",
                                    yes_levels=[(0.48, 300), (0.49, 400), (0.50, 700)],
                                    no_levels=[(0.48, 250), (0.49, 350), (0.50, 600)]),
                       "kind:orderbook")
        return cycle

    def test_cycle_produces_ledger_and_consistent_cash(self):
        cycle = self.make_cycle()
        summary = run_cycle(cycle, self.season, {"markets": ["TEST-A-1", "TEST-B-2"]}, NOW)
        self.assertGreater(summary["actions"], 0)
        state = json.loads((self.season / "forward" / "state.json").read_text(encoding="utf-8"))
        trades = [json.loads(l) for l in
                  (self.season / "forward" / "trades.jsonl").read_text(encoding="utf-8").splitlines()
                  if l.strip()]
        self.assertTrue(any(t["type"] == "fill" for t in trades), "expected at least one fill")
        # cash recompute
        for sname, ss in state["strategies"].items():
            cash = STARTING_CASH
            for t in trades:
                if t["strategy"] != next(s["username"] for s in json.loads(
                        (REPO / "data" / "strategies.json").read_text(encoding="utf-8"))
                        if s["id"] == sname):
                    continue
                if t["type"] == "fill":
                    cash = round(cash - t["notional"] - t["fee"], 6)
                elif t["type"] == "exit":
                    cash = round(cash + t["proceeds"], 6)
                elif t["type"] == "settlement":
                    cash = round(cash + t["payout"], 6)
            self.assertLessEqual(cash, ss["cash"] + 0.005, f"cash drift for {sname}")
            self.assertGreaterEqual(ss["cash"], -0.005, f"negative cash for {sname}")
        # every fill price exists in its book
        for t in trades:
            if t["type"] != "fill":
                continue
            book = json.loads((cycle / "evidence" / "raw" / f"book-{t['ticker']}.json")
                              .read_text(encoding="utf-8"))
            levels = set()
            for side in ("yes", "no"):
                for p, _q in book["orderbook_fp"][side + "_dollars"]:
                    levels.add(round(p, 4))
                    levels.add(round(1 - p, 4))
            for fl in t["fills"]:
                self.assertIn(fl["price"], levels, f"fill {fl} not in book for {t['ticker']}")

    def test_settlement_uses_official_result_only(self):
        cycle = self.make_cycle()
        # a second cycle where market A closed as YES
        write_evidence(cycle, "market-TEST-A-1-v2",
                       market_payload("TEST-A-1", status="closed", result="yes",
                                      last=1.0, prev=0.89, vol=60000, vol24=0),
                       "kind:market synthetic")
        # NOTE: two evidence files with the same market ticker — run_cycle keys by
        # ticker, last write wins; instead craft the closed market as the only one:
        cycle2 = self.season / "forward" / "cycle-002"
        (cycle2 / "evidence" / "raw").mkdir(parents=True, exist_ok=True)
        (cycle2 / "evidence" / "provenance").mkdir(parents=True, exist_ok=True)
        write_evidence(cycle2, "market-TEST-A-1",
                       market_payload("TEST-A-1", status="closed", result="yes"),
                       "kind:market synthetic")
        write_evidence(cycle2, "book-TEST-A-1",
                       book_payload("TEST-A-1",
                                    yes_levels=[(0.99, 100)], no_levels=[(0.00, 100)]),
                       "kind:orderbook")
        write_evidence(cycle2, "market-TEST-B-2",
                       market_payload("TEST-B-2"), "kind:market synthetic")
        write_evidence(cycle2, "book-TEST-B-2",
                       book_payload("TEST-B-2",
                                    yes_levels=[(0.48, 300)], no_levels=[(0.48, 300)]),
                       "kind:orderbook")
        run_cycle(cycle, self.season, {"markets": ["TEST-A-1", "TEST-B-2"]}, NOW)
        run_cycle(cycle2, self.season, {"markets": ["TEST-A-1", "TEST-B-2"]}, NOW + 3600)
        trades = [json.loads(l) for l in
                  (self.season / "forward" / "trades.jsonl").read_text(encoding="utf-8").splitlines()
                  if l.strip()]
        settlements = [t for t in trades if t["type"] == "settlement"]
        for s in settlements:
            if s["ticker"] == "TEST-A-1":
                self.assertEqual(s["result"], "yes")
                self.assertEqual(s["payout"], round(s["contracts"], 2))

    def test_no_book_no_fill(self):
        cycle = self.season / "forward" / "cycle-nobook"
        (cycle / "evidence" / "raw").mkdir(parents=True, exist_ok=True)
        (cycle / "evidence" / "provenance").mkdir(parents=True, exist_ok=True)
        write_evidence(cycle, "market-TEST-A-1", market_payload("TEST-A-1"), "kind:market synthetic")
        run_cycle(cycle, self.season, {"markets": ["TEST-A-1"]}, NOW)
        trades = [json.loads(l) for l in
                  (self.season / "forward" / "trades.jsonl").read_text(encoding="utf-8").splitlines()
                  if l.strip()]
        self.assertEqual([t for t in trades if t["type"] == "fill"], [],
                         "no fill may exist without a captured book")


if __name__ == "__main__":
    unittest.main(verbosity=2)
