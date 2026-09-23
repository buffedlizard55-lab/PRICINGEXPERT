#!/usr/bin/env python3
"""Generate data/manifest.json from the committed raw/ + provenance/ directories.
The manifest is the site's table of contents for evidence: file, URL, fetched_at,
SHA-256. Regenerate after every evidence commit; verify.py checks consistency.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    data = Path(sys.argv[1] if len(sys.argv) > 1 else "data")
    prov_dir = data / "provenance"
    files = {}
    if prov_dir.exists():
        for meta_path in sorted(prov_dir.glob("*.meta.json")):
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            files[meta["id"]] = {
                "url": meta.get("url"),
                "fetched_at": meta.get("fetched_at"),
                "sha256": meta.get("sha256"),
                "bytes": meta.get("bytes"),
                "note": meta.get("note", ""),
            }
    manifest = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generator": "scripts/make_manifest.py",
        "files": files,
    }
    (data / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n",
                                        encoding="utf-8")
    print(json.dumps({"files": len(files)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
