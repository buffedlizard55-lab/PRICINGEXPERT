#!/usr/bin/env bash
# PRICINGEXPERT full end-to-end proof on deterministic synthetic evidence.
# Isolates everything in a temp workdir (never touches the repo's data/).
#
#   python3 tests/make_e2e_fixture.py  -> staged workdir
#   2 in-season desk cycles + 1 post-season cycle (season-freeze check)
#   derive -> backtest -> manifest -> build_site -> verify  (gate must pass)
#
# Expected: verify reports 0 errors; curve end == board equity for every strategy;
# cycle-003 (post-season) produces no new intents/fills but still marks open positions.
set -euo pipefail
cd "$(dirname "$0")/.."

WORK=$(python3 tests/make_e2e_fixture.py | python3 -c "import json,sys; print(json.load(sys.stdin)['workdir'])")
NOW1=$(date -u -d '2026-09-21 14:13:20' +%s)
NOW2=$(date -u -d '2026-09-21 14:33:20' +%s)
NOW3=$(date -u -d '2027-01-02 00:10:00' +%s)

echo "== desk cycles (2 in-season + 1 post-season)"
python3 "$WORK/scripts/forward_desk.py" "$WORK/data/season-2026/forward/cycle-001" "$WORK/data/season-2026" "$WORK/data/strategies.json" "$NOW1"
python3 "$WORK/scripts/forward_desk.py" "$WORK/data/season-2026/forward/cycle-002" "$WORK/data/season-2026" "$WORK/data/strategies.json" "$NOW2"
python3 "$WORK/scripts/forward_desk.py" "$WORK/data/season-2026/forward/cycle-003" "$WORK/data/season-2026" "$WORK/data/strategies.json" "$NOW3"

echo "== pipeline"
(cd "$WORK" && python3 scripts/derive.py data && python3 scripts/backtest.py data >/dev/null \
  && python3 scripts/make_manifest.py data && python3 scripts/build_site.py data)

echo "== gate"
python3 "$WORK/scripts/verify.py" "$WORK/data" | python3 -c "
import json, sys
r = json.load(sys.stdin)
print('verify:', r['passed'], 'passed /', len(r['errors_full']), 'errors')
for e in r['errors_full'][:10]:
    print('  -', e[:160])
sys.exit(1 if r['errors_full'] else 0)
"

echo "== invariants"
python3 - "$WORK" <<'EOF'
import json, sys
from pathlib import Path
work = Path(sys.argv[1])
lb = json.load(open(work / "data/site/leaderboard.json"))
assert all((r["curve"][-1]["equity"] == r["equity"]) if r["curve"] else True
           for r in lb["rows"]), "curve end != board equity"
state = json.load(open(work / "data/season-2026/forward/state.json"))
assert state.get("season_closed_at"), "post-season cycle did not record season_closed_at"
intents = [json.loads(l) for l in open(work / "data/season-2026/forward/intents.jsonl") if l.strip()]
assert not any(i["cycle"] == "cycle-003" for i in intents), "post-season cycle proposed trades"
marks = [json.loads(l) for l in open(work / "data/season-2026/forward/marks.jsonl") if l.strip()]
assert any(m["cycle"] == "cycle-003" for m in marks), "post-season cycle stopped marking"
print("invariants: curve==board, season freeze (no post-season intents), marks continue — OK")
EOF

echo "== workdir kept for inspection: $WORK"
