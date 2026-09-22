"""Python engine tests — same fixture fills as tests/engine.test.mjs (parity).
Run: python3 tests/test_paper_engine.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from paper_engine import (best_bid, book_quotes, depth, execute, taker_fee,  # noqa: E402
                          affordable_contracts, parse_book, settlement_payout,
                          size_and_fill, STARTING_CASH)

BOOK_PAYLOAD = {
    "orderbook_fp": {
        "yes_dollars": [[0.4900, 100], [0.4800, 200], [0.4700, 300]],
        "no_dollars": [[0.4900, 80], [0.4800, 150]],
    }
}
BOOK = parse_book(BOOK_PAYLOAD)


class TestQuotes(unittest.TestCase):
    def test_reciprocal_rule(self):
        q = book_quotes(BOOK)
        self.assertEqual(q["yes_bid"], 0.49)
        self.assertEqual(q["no_bid"], 0.49)
        self.assertEqual(q["yes_ask"], 0.51)
        self.assertEqual(q["no_ask"], 0.51)

    def test_depth(self):
        self.assertEqual(depth(BOOK, "yes"), 600)
        self.assertEqual(depth(BOOK, "no"), 230)

    def test_best_bid_empty(self):
        self.assertIsNone(best_bid({"yes": [], "no": []}, "yes"))


class TestExecute(unittest.TestCase):
    def test_buy_yes_crosses_no_bids_limit_bounded(self):
        r = execute(BOOK, "yes", "buy", 200, 0.52)
        self.assertEqual(r["filled"], 200)
        self.assertEqual(len(r["fills"]), 2)
        self.assertEqual(r["fills"][0]["price"], 0.51)
        self.assertEqual(r["fills"][0]["contracts"], 80)
        self.assertEqual(r["fills"][1]["price"], 0.52)
        self.assertEqual(r["fills"][1]["contracts"], 120)
        notional = 80 * 0.51 + 120 * 0.52
        self.assertAlmostEqual(r["notional"], notional, places=9)
        self.assertAlmostEqual(r["vwap"], notional / 200, places=6)
        self.assertEqual(r["touch"], 0.51)

    def test_limit_blocks_worse_levels(self):
        r = execute(BOOK, "yes", "buy", 500, 0.505)
        self.assertEqual(r["filled"], 0)
        self.assertEqual(r["unfilled"], 500)

    def test_sell_yes_crosses_own_bids(self):
        r = execute(BOOK, "yes", "sell", 250)
        self.assertEqual(r["filled"], 250)
        self.assertEqual(r["fills"][0]["price"], 0.49)
        self.assertEqual(r["fills"][1]["price"], 0.48)
        self.assertEqual(r["touch"], 0.49)

    def test_buy_no_takes_yes_bids(self):
        r = execute(BOOK, "no", "buy", 100, 0.99)
        # YES bid 0.49 -> NO exec 0.51 (100 available)
        self.assertEqual(r["filled"], 100)
        self.assertEqual(r["fills"][0]["price"], 0.51)

    def test_invalid_inputs(self):
        r = execute(BOOK, "maybe", "buy", 10)
        self.assertEqual(r["filled"], 0)
        r = execute(BOOK, "yes", "hold", 10)
        self.assertEqual(r["filled"], 0)
        r = execute(BOOK, "yes", "buy", 0)
        self.assertEqual(r["filled"], 0)


class TestFees(unittest.TestCase):
    def test_exact_formula(self):
        # 0.07 * C * P * (1-P)
        self.assertEqual(taker_fee(0.5, 1000), 17.5)     # 0.07*1000*0.25
        self.assertEqual(taker_fee(0.5, 1), 0.0175)      # 0.07*1*0.25
        self.assertEqual(taker_fee(0.3, 3), 0.0441)      # 0.07*3*0.21
        self.assertEqual(taker_fee(1.0, 10), 0.0)
        self.assertEqual(taker_fee(0.0, 10), 0.0)

    def test_rounds_up_to_grid(self):
        # 0.07 * 7 * 0.12 * 0.88 = 0.051744 -> ceil to $0.0001 grid = 0.0518
        self.assertEqual(taker_fee(0.12, 7), 0.0518)

    def test_multiplier(self):
        self.assertEqual(taker_fee(0.5, 100, 2.0), 3.5)  # 0.07*100*0.25*2


class TestSizing(unittest.TestCase):
    def test_affordable(self):
        self.assertEqual(affordable_contracts(100, 0.5), 193)

    def test_size_and_fill_budget_bound(self):
        # budget = 10000*0.25 = 2500; touch 0.51; fee included
        r = size_and_fill(BOOK, "yes", STARTING_CASH, 0.25, limit=0.99)
        self.assertIsNotNone(r)
        self.assertGreater(r["filled"], 0)
        self.assertLessEqual(r["notional"] + r["fee"], 2500 + 1e-9)
        self.assertEqual(r["side"], "yes")

    def test_size_and_fill_blocked_no_book(self):
        self.assertIsNone(size_and_fill({"yes": [], "no": []}, "yes", 1000, 0.25))

    def test_size_and_fill_blocked_above_limit(self):
        self.assertIsNone(size_and_fill(BOOK, "yes", 1000, 0.25, limit=0.505))


class TestSettlement(unittest.TestCase):
    def test_payout(self):
        self.assertEqual(settlement_payout("yes", 100, "yes"), 100)
        self.assertEqual(settlement_payout("no", 100, "yes"), 0)
        with self.assertRaises(ValueError):
            settlement_payout("yes", 100, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
