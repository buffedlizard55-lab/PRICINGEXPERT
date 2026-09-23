#!/usr/bin/env python3
"""Read-only client for Kalshi's public (unauthenticated) Trade API.

Rules (enforced by scripts/verify.py and by design):
  * Only GET requests to https://api.elections.kalshi.com/trade-api/v2 — the host
    verified live in this session. (Kalshi documents external-api.kalshi.com as an
    alias; it is NOT verified or used by this project.)
  * No credentials are ever read or sent — this client can only READ.
  * Every response body is kept verbatim by the caller when it becomes evidence;
    this module returns parsed JSON plus the raw bytes and a call log entry with
    the exact URL, timestamp and SHA-256 of the body.
  * Rate limiting: fixed pause between calls, exponential backoff on 429/5xx.

Endpoints used (each re-verified live in scripts/probe_endpoints.py before use):
  GET /exchange/status
  GET /historical/cutoff
  GET /series
  GET /series/{series_ticker}
  GET /markets  (cursor pagination)
  GET /markets/{ticker}
  GET /markets/{ticker}/orderbook
  GET /markets/trades
  GET /series/{series}/markets/{ticker}/candlesticks
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
USER_AGENT = ("PRICINGEXPERT/1.0 (+https://github.com/buffedlizard55-lab/PRICINGEXPERT; "
              "paper-trading research, read-only public API)")


class KalshiError(RuntimeError):
    pass


class KalshiClient:
    def __init__(self, base_url: str = BASE_URL, pause: float = 0.15, timeout: float = 30.0,
                 max_retries: int = 4, opener=None):
        self.base_url = base_url.rstrip("/")
        self.pause = pause
        self.timeout = timeout
        self.max_retries = max_retries
        self.calls: list[dict] = []
        self._opener = opener or urllib.request.build_opener()
        self._last_call = 0.0

    def _sleep_for_rate_limit(self):
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.pause:
            time.sleep(self.pause - elapsed)

    def get(self, path: str, params: dict | None = None) -> tuple[dict, bytes, str]:
        """GET a JSON endpoint. Returns (parsed_json, raw_bytes, full_url)."""
        query = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
        url = f"{self.base_url}/{path.lstrip('/')}"
        if query:
            url += "?" + urllib.parse.urlencode(query, doseq=True)
        attempt = 0
        while True:
            self._sleep_for_rate_limit()
            request = urllib.request.Request(
                url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
            started = time.time()
            try:
                with self._opener.open(request, timeout=self.timeout) as response:
                    raw = response.read()
                    status = response.status
            except urllib.error.HTTPError as error:
                raw = error.read() if error.fp else b""
                status = error.code
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
                raw = str(error).encode()
                status = 0
            finally:
                self._last_call = time.monotonic()
            self.calls.append({"url": url, "status": status, "bytes": len(raw),
                               "at": _iso(started), "sha256": hashlib.sha256(raw).hexdigest()})
            if status == 200:
                try:
                    return json.loads(raw.decode("utf-8")), raw, url
                except json.JSONDecodeError as error:
                    raise KalshiError(f"non-JSON body from {url}: {error}") from error
            if status in (404, 400):
                raise KalshiError(f"HTTP {status} from {url}: {raw[:200]!r}")
            attempt += 1
            if attempt > self.max_retries:
                raise KalshiError(f"HTTP {status} from {url} after {attempt} attempts: {raw[:200]!r}")
            time.sleep(min(20.0, 0.5 * (2 ** attempt)))

    # ------------------------------------------------------------ endpoint helpers
    def exchange_status(self) -> dict:
        return self.get("exchange/status")[0]

    def historical_cutoff(self) -> dict:
        return self.get("historical/cutoff")[0]

    def series_list(self, category: str | None = None, limit: int = 500) -> list[dict]:
        payload = self.get("series", {"category": category, "limit": limit})[0]
        return payload.get("series") or []

    def series(self, series_ticker: str) -> dict:
        payload, _, _ = self.get(f"series/{series_ticker}")
        return payload.get("series", payload)

    def markets(self, series_ticker: str | None = None, status: str | None = None,
                limit: int = 200, max_pages: int = 5, event_ticker: str | None = None,
                min_close_ts: int | None = None, max_close_ts: int | None = None) -> list[dict]:
        out: list[dict] = []
        cursor = ""
        for _ in range(max_pages):
            params = {"series_ticker": series_ticker, "status": status, "limit": limit,
                      "cursor": cursor or None, "event_ticker": event_ticker,
                      "min_close_ts": min_close_ts, "max_close_ts": max_close_ts}
            payload = self.get("markets", params)[0]
            rows = payload.get("markets") or []
            out.extend(rows)
            cursor = str(payload.get("cursor") or "")
            if not cursor or not rows:
                break
        return out

    def market(self, ticker: str) -> tuple[dict, bytes, str]:
        payload, raw, url = self.get(f"markets/{urllib.parse.quote(ticker, safe='')}")
        return payload.get("market", payload), raw, url

    def orderbook(self, ticker: str, depth: int = 50) -> tuple[dict, bytes, str]:
        payload, raw, url = self.get(f"markets/{urllib.parse.quote(ticker, safe='')}/orderbook",
                                     {"depth": depth})
        return payload, raw, url

    def trades(self, ticker: str | None = None, limit: int = 100, min_ts: int | None = None,
               max_ts: int | None = None) -> tuple[dict, bytes, str]:
        payload, raw, url = self.get("markets/trades",
                                     {"ticker": ticker, "limit": limit,
                                      "min_ts": min_ts, "max_ts": max_ts})
        return payload, raw, url

    def candlesticks(self, series_ticker: str, ticker: str, start_ts: int, end_ts: int,
                     period_interval: int = 3600) -> tuple[dict, bytes, str]:
        payload, raw, url = self.get(
            f"series/{series_ticker}/markets/{urllib.parse.quote(ticker, safe='')}/candlesticks",
            {"start_ts": start_ts, "end_ts": end_ts, "period_interval": period_interval})
        return payload, raw, url


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


if __name__ == "__main__":  # manual smoke check (network required)
    client = KalshiClient()
    print(json.dumps(client.exchange_status(), indent=2))
