"""market_pick selection policy tests. Run: python3 tests/test_market_pick.py

NOW = 1_750_000_000 = 2025-06-15T15:06:40Z (fixed reference "now")."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from market_pick import select_tickers  # noqa: E402

NOW = 1_750_000_000  # 2025-06-15T15:06:40Z
FAR = "2025-06-15T19:00:00Z"   # ~4h ahead: past any sane margin
NEAR = "2025-06-15T16:00:00Z"  # ~53min ahead: inside the 90-min default margin
IMM = "2025-06-15T15:30:00Z"   # ~23min ahead: definitely inside


def m(ticker, status="active", close=FAR, oi=100, v24=50):
    return {"ticker": ticker, "status": status, "close_time": close,
            "open_interest_fp": oi, "volume_24h_fp": v24}


def payload(*mkts):
    return {"markets": list(mkts)}


class TestSelectTickers(unittest.TestCase):
    def test_ranks_by_oi_desc(self):
        a = m("A-T1", oi=100, v24=1)
        b = m("B-T2", oi=500, v24=1)
        c = m("C-T3", oi=100, v24=999)
        # oi first: B; then A vs C: equal oi, C has higher v24
        self.assertEqual(select_tickers(payload(a, b, c), NOW, top_n=3),
                         ["B-T2", "C-T3", "A-T1"])

    def test_oi_zero_never_selected(self):
        a = m("A", oi=0, v24=10_000)
        b = m("B", oi=1)
        self.assertEqual(select_tickers(payload(a, b), NOW, top_n=5), ["B"])

    def test_non_active_never_selected(self):
        a = m("A", status="initialized", oi=500)
        b = m("B", status="settlable", oi=500)
        c = m("C", status="active", oi=10)
        self.assertEqual(select_tickers(payload(a, b, c), NOW, top_n=3), ["C"])

    def test_close_margin_excludes_imminent_settlement(self):
        near = m("NEAR", oi=9999, close=NEAR)   # settles in ~53min < 90-min margin
        far = m("FAR", oi=5, close=FAR)
        self.assertEqual(select_tickers(payload(near, far), NOW,
                                        close_margin_sec=5400, top_n=3), ["FAR"])
        # but with a 10-minute margin both qualify and oi ranks NEAR first
        self.assertEqual(select_tickers(payload(near, far), NOW,
                                        close_margin_sec=600, top_n=3), ["NEAR", "FAR"])

    def test_no_close_time_uses_expected_expiration(self):
        near = {"ticker": "NE", "status": "active", "open_interest_fp": 9999,
                "volume_24h_fp": 0, "close_time": None,
                "expected_expiration_time": IMM}
        far = {"ticker": "FA", "status": "active", "open_interest_fp": 1,
               "volume_24h_fp": 0, "close_time": None,
               "expected_expiration_time": FAR}
        self.assertEqual(select_tickers(payload(near, far), NOW,
                                        close_margin_sec=5400, top_n=3), ["FA"])

    def test_past_close_never_selected(self):
        gone = m("GONE", oi=9999, close="2025-06-15T10:00:00Z")
        ok = m("OK", oi=1)
        self.assertEqual(select_tickers(payload(gone, ok), NOW, top_n=3), ["OK"])

    def test_no_candidates_returns_empty(self):
        self.assertEqual(select_tickers(payload(), NOW, top_n=3), [])
        self.assertEqual(select_tickers(payload(m("X", oi=0)), NOW, top_n=3), [])

    def test_top_n_zero_is_discovery_only(self):
        self.assertEqual(select_tickers(payload(m("X", oi=50)), NOW, top_n=0), [])

    def test_top_n_truncation_and_tiebreak(self):
        a = m("A", oi=10)
        b = m("B", oi=10)
        c = m("C", oi=10)
        self.assertEqual(select_tickers(payload(c, a, b), NOW, top_n=2), ["A", "B"])


if __name__ == "__main__":
    unittest.main()
