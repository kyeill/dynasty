#!/usr/bin/env python3
"""
Regression check — prove a code change didn't move the boards.

    python check_regression.py            # compare against the committed snapshots
    python check_regression.py --update   # re-bless them after an intended change

Run this after touching common.py. One file feeds three sports, so a
"harmless" tweak to the name resolver can silently reshuffle a board you
weren't thinking about.

It re-runs the pipeline with --cache, so it costs no network traffic and
compares the code's behaviour rather than whatever is sitting in output/.

Rankings legitimately drift as the sources update, so a difference is not
automatically a bug. A handful of shifted values is the sites moving; a changed
row count or a vanished player is usually the code.

No pandas -- see common.py.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time

from common import OUTPUT_DIR, read_csv, write_csv

HERE = OUTPUT_DIR.parent
TESTS = HERE / "tests"
SPORTS = ("nba", "nfl", "mlb")

# Compared for equality. current_rank and the extra reference columns are
# deliberately excluded -- they track live weekly sources and change on their
# own schedule, which would make the check cry wolf every few days.
KEY_COLS = ["combined_rank", "player", "pos", "team", "blended_score"]


def run_pipeline() -> bool:
    print("running: rankings.py --sport all --cache\n")
    proc = subprocess.run(
        [sys.executable, str(HERE / "rankings.py"), "--sport", "all", "--cache"],
        cwd=HERE, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        print("!! pipeline failed:\n" + (proc.stdout or "")[-2000:])
        print((proc.stderr or "")[-2000:])
        return False
    return True


def compare(sport: str) -> bool:
    live_path = OUTPUT_DIR / f"combined_rankings_{sport}.csv"
    base_path = TESTS / f"baseline_{sport}.csv"
    if not live_path.exists():
        print(f"  {sport}: NO OUTPUT at {live_path.name}")
        return False
    if not base_path.exists():
        print(f"  {sport}: no baseline yet -- run with --update")
        return False

    live, base = read_csv(live_path), read_csv(base_path)
    if len(live) != len(base):
        print(f"  {sport}: ROW COUNT {len(base)} -> {len(live)}")
        return False

    cols = [c for c in KEY_COLS if base and c in base[0] and c in live[0]]
    diffs = {}
    for i, (b, l) in enumerate(zip(base, live)):
        for c in cols:
            if str(b.get(c, "")) != str(l.get(c, "")):
                diffs.setdefault(c, []).append((i, b.get(c), l.get(c)))

    if not diffs:
        print(f"  {sport}: OK ({len(live)} rows)")
        return True

    print(f"  {sport}: DIFFERS ({len(live)} rows)")
    for c, rows in diffs.items():
        i, was, now = rows[0]
        print(f"    {c}: {len(rows)} rows differ; first at row {i}: "
              f"{was!r} -> {now!r}")
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--update", action="store_true",
                    help="overwrite the snapshots with current output")
    ap.add_argument("--no-run", action="store_true",
                    help="compare what's already in output/ without re-running")
    args = ap.parse_args()

    if not args.no_run and not run_pipeline():
        return 1

    TESTS.mkdir(exist_ok=True)
    if args.update:
        for sport in SPORTS:
            src = OUTPUT_DIR / f"combined_rankings_{sport}.csv"
            if src.exists():
                rows = read_csv(src)
                write_csv(TESTS / f"baseline_{sport}.csv", rows,
                          list(rows[0]) if rows else [])
                print(f"  {sport}: baseline updated")
        return 0

    # The baseline is a "before my change" snapshot, not permanent truth. The
    # sources move daily, so an old baseline produces differences that are
    # drift rather than defects. Say how old it is so a diff can be read
    # correctly.
    ages = [(time.time() - (TESTS / f"baseline_{s}.csv").stat().st_mtime) / 86400
            for s in SPORTS if (TESTS / f"baseline_{s}.csv").exists()]
    if ages:
        oldest = max(ages)
        note = ("" if oldest < 1 else
                "  <-- source drift is likely; re-bless before making a change")
        print(f"baselines are {oldest:.1f} days old{note}")
    print("comparing against tests/baseline_<sport>.csv")
    # Evaluate every sport before deciding. all() over a generator short-
    # circuits, which would hide a second failure behind the first and send you
    # round the fix-and-rerun loop once per sport.
    results = [compare(sport) for sport in SPORTS]
    ok = all(results)
    print("\nPASS" if ok else "\nFAIL -- see above; --update to re-bless if intended")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
