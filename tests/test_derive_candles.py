"""derive.py candle parsing — verified 2026 API shape (price.*_dollars +
end_period_ts + volume_fp) and the legacy flat shape. Run:
python3 tests/test_derive_candles.py"""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import derive  # noqa: E402

# Verified live payload shape (docs.kalshi.com Get Market Candlesticks, 2026-07-24):
NEW_SHAPE = {"ticker": "T-AUG", "candlesticks": [
    {"end_period_ts": 1784937600,
     "yes_bid": {"open_dollars": "0.4000"}, "yes_ask": {"open_dollars": "0.4200"},
     "price": {"open_dollars": "0.4100", "high_dollars": "0.4300",
               "low_dollars": "0.3900", "close_dollars": "0.4200",
               "previous_dollars": "0.4050", "mean_dollars": "0.4100"},
     "volume_fp": "12.00", "open_interest_fp": "30.00"},
    {"end_period_ts": 1784941200,
     "price": {"open_dollars": "0.4200", "high_dollars": "0.4200",
               "low_dollars": "0.4150", "close_dollars": "0.4150"},
     "volume_fp": "3.00"},
]}
LEGACY_SHAPE = {"ticker": "T-OLD", "candles": [
    {"start_time": "2025-06-15T15:06:40Z", "open": 0.5, "high": 0.52,
     "low": 0.48, "close": 0.51, "volume": 7},
]}


def write_evidence(data: Path, sid: str, payload: dict, extra_meta: dict):
    raw = data / "raw" / f"{sid}.json"
    raw.parent.mkdir(parents=True, exist_ok=True)
    (data / "provenance").mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload).encode()
    raw.write_bytes(body)
    meta = {"id": sid, "url": "https://fake", "fetched_at": "2026-09-23T03:00:00Z",
            "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body),
            "http_status": 200, "tool": "test", "note": "kind:candlesticks — test",
            **extra_meta}
    (data / "provenance" / f"{sid}.meta.json").write_text(json.dumps(meta, indent=1))


class TestDeriveCandles(unittest.TestCase):
    def _run(self, cases):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            for sid, payload, extra in cases:
                write_evidence(data, sid, payload, extra)
            old_argv = sys.argv
            sys.argv = ["derive.py", str(data)]
            try:
                rc = derive.main()
            finally:
                sys.argv = old_argv
            self.assertEqual(rc, 0)
            out = {}
            for p in sorted((data / "candles").glob("*.csv")):
                out[p.name] = p.read_text(encoding="utf-8").splitlines()
            return out

    def test_new_shape_parsed(self):
        out = self._run([("c1", NEW_SHAPE, {"ticker": "T-AUG", "series": "S"})])
        lines = out["S-T-AUG.csv"]
        self.assertEqual(lines[0], "ts,open,high,low,close,volume,provenance,sha256")
        self.assertEqual(lines[1].split(",")[0], "1784937600")
        self.assertEqual(lines[1].split(",")[1:6], ["0.41", "0.43", "0.39", "0.42", "12.0"])
        self.assertEqual(len(lines), 3)  # header + 2 bars

    def test_legacy_shape_still_parsed(self):
        out = self._run([("c2", LEGACY_SHAPE, {"ticker": "T-OLD", "series": "S"})])
        lines = out["S-T-OLD.csv"]
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[1].split(",")[0], "1750000000")  # 2025-06-15T15:06:40Z
        self.assertEqual(lines[1].split(",")[1:6], ["0.5", "0.52", "0.48", "0.51", "7.0"])

    def test_incomplete_rows_dropped(self):
        payload = {"ticker": "T-X", "candlesticks": [
            {"end_period_ts": 1000, "price": {"open_dollars": "0.5"}},  # no h/l/c -> drop
            {"end_period_ts": 2000,
             "price": {"open_dollars": "0.5", "high_dollars": "0.5",
                       "low_dollars": "0.5", "close_dollars": "0.5"}},  # kept
        ]}
        out = self._run([("c3", payload, {"ticker": "T-X", "series": "S"})])
        self.assertEqual(len(out["S-T-X.csv"]), 2)


if __name__ == "__main__":
    unittest.main()
