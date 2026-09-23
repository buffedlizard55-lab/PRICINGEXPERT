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
    """Scripted stand-in for KalshiClient: get() pops the next queued page."""

    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def get(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        if not self.pages:
            raise AssertionError(f"unexpected extra call {path} {params}")
        payload, raw = self.pages.pop(0)
        return payload, raw, f"https://fake/{path}"

    def market(self, ticker):
        raise AssertionError("not scripted")


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


if __name__ == "__main__":
    unittest.main()
