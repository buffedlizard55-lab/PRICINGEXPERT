"""Collector tests — cursor-following pagination (the live host ignores `offset`)
and per-page provenance. Run: python3 tests/test_collect.py"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from collect import fetch_step, main  # noqa: E402


class FakeClient:
    """Scripted stand-in for KalshiClient: get() pops the next queued page;
    market()/orderbook() pop from scripted dicts keyed by ticker."""

    def __init__(self, pages, markets=None, books=None):
        self.pages = list(pages)
        self.markets = dict(markets or {})
        self.books = dict(books or {})
        self.calls = []

    def get(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        if not self.pages:
            raise AssertionError(f"unexpected extra call {path} {params}")
        payload, raw = self.pages.pop(0)
        return payload, raw, f"https://fake/{path}"

    def market(self, ticker):
        self.calls.append(("market/" + ticker, {}))
        if ticker not in self.markets:
            raise AssertionError(f"unscripted market {ticker}")
        payload, raw = self.markets[ticker]
        return payload, raw, f"https://fake/market/{ticker}"

    def orderbook(self, ticker, depth=50):
        self.calls.append(("orderbook/" + ticker, {"depth": depth}))
        if ticker not in self.books:
            raise AssertionError(f"unscripted book {ticker}")
        payload, raw = self.books[ticker]
        return payload, raw, f"https://fake/orderbook/{ticker}"


def mkplan(endpoints):
    return {"plan_version": "t", "endpoints": endpoints}


class TestFetchStepPagination(unittest.TestCase):
    def test_single_page_no_paginate(self):
        c = FakeClient([({"cursor": "C1", "markets": ["a"]}, b'{"cursor":"C1"}')])
        pages = fetch_step(c, {"id": "s", "kind": "markets",
                               "params": {"limit": 200}})
        self.assertEqual(len(pages), 1)
        self.assertEqual(len(c.calls), 1)
        self.assertNotIn("cursor", c.calls[0][1])

    def test_follows_cursor_until_max_pages(self):
        pages = [({"cursor": f"C{i}", "markets": [f"m{i}"]}, b"x") for i in range(1, 4)]
        c = FakeClient(pages)
        got = fetch_step(c, {"id": "s", "kind": "markets",
                             "params": {"limit": 200, "paginate": {"max_pages": 3}}})
        self.assertEqual(len(got), 3)
        # page 2 and 3 carry the previous page's cursor
        self.assertEqual(c.calls[1][1].get("cursor"), "C1")
        self.assertEqual(c.calls[2][1].get("cursor"), "C2")

    def test_stops_when_cursor_empty(self):
        c = FakeClient([({"cursor": "C1", "markets": ["a"]}, b"x"),
                        ({"cursor": "", "markets": ["b"]}, b"y")])
        got = fetch_step(c, {"id": "s", "kind": "markets",
                             "params": {"paginate": {"max_pages": 5}}})
        self.assertEqual(len(got), 2)
        self.assertEqual(len(c.calls), 2)

    def test_max_pages_one_means_single_call(self):
        c = FakeClient([({"cursor": "C1", "markets": ["a"]}, b"x")])
        got = fetch_step(c, {"id": "s", "kind": "markets",
                             "params": {"limit": 200, "paginate": {"max_pages": 1}}})
        self.assertEqual(len(got), 1)
        self.assertEqual(len(c.calls), 1)


class TestMainPaginationFiles(unittest.TestCase):
    def run_main(self, endpoints):
        with tempfile.TemporaryDirectory() as td:
            plan_path = Path(td) / "plan.json"
            plan_path.write_text(json.dumps(mkplan(endpoints)))
            import collect as mod
            sys.argv = ["collect.py", "--plan", str(plan_path), "--out", td]
            rc = mod.main()
            self.assertEqual(rc, 0)
            raws = sorted(p.name for p in (Path(td) / "raw").glob("*.json"))
            metas = sorted(p.name for p in (Path(td) / "provenance").glob("*.meta.json"))
            report = json.loads((Path(td) / "collect-report.json").read_text())
            calls = json.loads((Path(td) / "calls.json").read_text())
            return raws, metas, report, calls

    def test_per_page_raw_and_provenance(self):
        steps = [{"id": "sweep", "kind": "markets",
                  "params": {"limit": 200, "paginate": {"max_pages": 3}}}]
        # patch the client in the collect module
        import collect as mod
        orig = mod.KalshiClient
        try:
            mod.KalshiClient = lambda: FakeClient(
                [({"cursor": f"C{i}", "markets": [f"m{i}"]}, b"{}") for i in range(1, 4)])
            raws, metas, report, calls = self.run_main(steps)
        finally:
            mod.KalshiClient = orig
        self.assertEqual(raws, ["sweep-p01.json", "sweep-p02.json", "sweep-p03.json"])
        self.assertEqual(metas, ["sweep-p01.meta.json", "sweep-p02.meta.json",
                                 "sweep-p03.meta.json"])
        self.assertEqual(report["ok"], 3)
        self.assertEqual(report["calls_logged"], 3)
        self.assertEqual([c["id"] for c in calls], ["sweep-p01", "sweep-p02", "sweep-p03"])

    def test_one_failure_does_not_stop_run(self):
        import collect as mod
        orig = mod.KalshiClient
        try:
            class HalfClient(FakeClient):
                def get(self, path, params=None):
                    if path == "markets" and (params or {}).get("series_ticker") == "BAD":
                        raise RuntimeError("HTTP 400 from markets/BAD")
                    return super().get(path, params)
            mod.KalshiClient = lambda: HalfClient(
                [({"markets": ["ok-market"]}, b"{}"), ({"markets": []}, b"{}")])
            raws, metas, report, calls = self.run_main([
                {"id": "good", "kind": "markets",
                 "params": {"series_ticker": "GOOD"}},
                {"id": "bad", "kind": "markets",
                 "params": {"series_ticker": "BAD"}},
                {"id": "good2", "kind": "markets",
                 "params": {"series_ticker": "GOOD2"}},
            ])
        finally:
            mod.KalshiClient = orig
        self.assertEqual(report["ok"], 2)
        self.assertEqual(report["failed"], 1)
        self.assertEqual(raws, ["good.json", "good2.json"])
        self.assertEqual(calls[1]["status"], "error")


class TestSeriesPick(unittest.TestCase):
    """series_pick: series list + market/book captures for the policy's picks,
    selection recorded in the markets-list meta (V14 re-derives it)."""

    def run_pick(self, markets_payload, markets, books, top_n=2):
        import collect as mod
        orig = mod.KalshiClient
        try:
            client = FakeClient([markets_payload], markets=markets, books=books)
            mod.KalshiClient = lambda: client
            with tempfile.TemporaryDirectory() as td:
                step = {"id": "pick-TEST", "kind": "series_pick",
                        "params": {"series_ticker": "TEST", "limit": 50,
                                   "top_n": top_n, "close_margin_sec": 5400, "depth": 50},
                        "note": "kind:series_pick — test"}
                plan_path = Path(td) / "plan.json"
                plan_path.write_text(json.dumps({"endpoints": [step]}))
                sys.argv = ["collect.py", "--plan", str(plan_path), "--out", td]
                rc = mod.main()
                self.assertEqual(rc, 0)
                raws = sorted(p.name for p in (Path(td) / "raw").glob("*.json"))
                metas = {p.name: json.loads(p.read_text())
                         for p in (Path(td) / "provenance").glob("*.meta.json")}
                report = json.loads((Path(td) / "collect-report.json").read_text())
        finally:
            mod.KalshiClient = orig
        return raws, metas, report, client

    def test_picks_and_captures(self):
        mk = lambda t, oi: {"ticker": t, "status": "active", "close_time": "2099-01-01T00:00:00Z",
                            "open_interest_fp": oi, "volume_24h_fp": 0}
        payload = ({"markets": [mk("T-LOW", 10), mk("T-BIG", 900), mk("T-MID", 400),
                                mk("T-DEAD", 0), mk("T-INIT", 800)]}, b"{}")
        payload[0]["markets"][4]["status"] = "initialized"
        raws, metas, report, client = self.run_pick(
            payload,
            markets={"T-BIG": ({"ticker": "T-BIG"}, b"{}"),
                     "T-MID": ({"ticker": "T-MID"}, b"{}")},
            books={"T-BIG": ({"market_ticker": "T-BIG", "orderbook_fp": {}}, b"{}"),
                   "T-MID": ({"market_ticker": "T-MID", "orderbook_fp": {}}, b"{}")})
        self.assertEqual(raws, ["pick-TEST-book-T-BIG.json", "pick-TEST-book-T-MID.json",
                                "pick-TEST.json", "pick-TEST-market-T-BIG.json",
                                "pick-TEST-market-T-MID.json"])
        self.assertEqual(report["ok"], 5)
        base = metas["pick-TEST.meta.json"]
        self.assertEqual(base["selected"], ["T-BIG", "T-MID"])  # oi desc: 900, 400
        self.assertIn("pick_now_ts", base)
        self.assertEqual(base["top_n"], 2)
        self.assertTrue(base["note"].startswith("kind:series_pick"))
        self.assertTrue(metas["pick-TEST-market-T-BIG.meta.json"]["note"]
                        .startswith("kind:market"))
        self.assertTrue(metas["pick-TEST-book-T-BIG.meta.json"]["note"]
                        .startswith("kind:orderbook"))
        # no book captured for the unselected T-LOW / T-DEAD / T-INIT
        self.assertNotIn("pick-TEST-book-T-LOW.json", raws)

    def test_top_n_zero_captures_list_only(self):
        payload = ({"markets": [{"ticker": "T-1", "status": "active",
                                 "close_time": "2099-01-01T00:00:00Z",
                                 "open_interest_fp": 50, "volume_24h_fp": 0}]}, b"{}")
        raws, metas, report, client = self.run_pick(payload, {}, {}, top_n=0)
        self.assertEqual(raws, ["pick-TEST.json"])
        self.assertEqual(metas["pick-TEST.meta.json"]["selected"], [])


if __name__ == "__main__":
    unittest.main()
