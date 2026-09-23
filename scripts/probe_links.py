#!/usr/bin/env python3
"""Probe every external URL referenced by the site assets (remote check).
Runs in CI (has network egress). Local sandbox runs may not reach the web, so a
network error is recorded as "unreachable-from-runner" — never as "verified".
Writes data/site/link-probe.json.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

URL_RE = re.compile(r'https?://[^\s"\'<>)\]}]+')
SKIP_PREFIXES = ("https://github.com/buffedlizard55-lab/PRICINGEXPERT",
                 "https://buffedlizard55-lab.github.io/PRICINGEXPERT")


def main() -> int:
    data = Path(sys.argv[1] if len(sys.argv) > 1 else "data")
    root = data.parent
    urls: set[str] = set()
    for pattern in ("*.html", "*.css", "src/*.js", "data/site/*.json", "data/*.json",
                    "data/season-2026/*.json"):
        for p in root.glob(pattern):
            try:
                text = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for u in URL_RE.findall(text):
                u = u.rstrip(".,;")
                if u.startswith(SKIP_PREFIXES):
                    continue
                urls.add(u)
    results = []
    for url in sorted(urls):
        entry = {"url": url, "status": None, "ok": False}
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "PRICINGEXPERT-link-probe/1.0"},
                                         method="HEAD")
            with urllib.request.urlopen(req, timeout=20) as r:
                entry["status"] = r.status
            if entry["status"] in (405, 501):  # some hosts refuse HEAD; retry GET
                with urllib.request.urlopen(
                        urllib.request.Request(url, headers={"User-Agent": "PRICINGEXPERT-link-probe/1.0"}),
                        timeout=20) as r2:
                    entry["status"] = r2.status
            entry["ok"] = entry["status"] < 400
        except urllib.error.HTTPError as e:
            entry["status"] = e.code
            entry["ok"] = False
        except Exception as e:  # noqa: BLE001
            entry["status"] = f"unreachable-from-runner: {type(e).__name__}"
            entry["ok"] = False
        results.append(entry)
    out = data / "site" / "link-probe.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"urls_checked": len(results),
                               "ok": sum(1 for r in results if r["ok"]),
                               "results": results}, indent=1) + "\n", encoding="utf-8")
    bad = [r for r in results if not r["ok"]]
    print(json.dumps({"checked": len(results), "ok": len(results) - len(bad),
                      "bad": [r["url"] for r in bad][:20]}))
    # link failures are a finding, not a blocker for the data gate
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
