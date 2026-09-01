"""
Turn silent problems into a red run.

The pipeline has exactly one push notification: GitHub emails Kyle when a
workflow run fails. Everything else -- staleness, row counts, fallbacks -- is
written to a file or a tab and reaches him only if he goes looking. So the
failures worth an email have to be made to FAIL, deliberately, here.

Two kinds, and both are chosen to be things that never happen legitimately:

  1. A source has been running on its last-good copy for too long. Falling back
     keeps a board alive, which is the point, but past a couple of weeks it has
     stopped being a blip and become the answer nobody chose.

  2. A board collapsed structurally -- it lost a fifth of its players, or a
     column that was populated yesterday is empty today. That is what a broken
     parser looks like from the outside: no crash, no error, just a board that
     is quietly wrong. Every expensive bug in this project's history would have
     shown up as one of these two shapes.

Deliberately NOT a general diff. The board legitimately changes every day, so
anything that reports ordinary movement would fire constantly and be ignored
within a week -- and an alarm nobody reads is worse than no alarm, because it
also buries the real one. check_regression.py is the tool for "what moved";
this is only the tool for "something is broken".

Runs after the boards are built and BEFORE they are committed, so `git show
HEAD:<path>` is still yesterday's copy. The commit happens either way: a
degraded board is still worth publishing, it just should not be published
quietly.

    python check_health.py            # every sport
    python check_health.py --sport mlb
"""
from __future__ import annotations

import argparse
import subprocess
import sys

from common import OUTPUT_DIR, read_csv, to_num
from rankings import load_config

SPORTS = ("nba", "nfl", "mlb")

# A board losing this much of itself is a parser failure, not a source moving.
# The worst legitimate day on record is FantasyPros' NFL dynasty list going
# 547 -> 432 over four days, and no single day of that came close.
MIN_ROWS_FRACTION = 0.80

# A column that was populated yesterday and is empty today. The thresholds are
# far apart on purpose: a column has to go from mostly-there to almost-gone,
# which no normal day does.
WAS_POPULATED = 0.50
NOW_EMPTY = 0.20

DEFAULT_STALE_FAIL_DAYS = 14


def previous_board(sport: str) -> list[dict]:
    """Yesterday's committed board, or [] if there isn't one to compare to."""
    path = f"output/combined_rankings_{sport}.csv"
    try:
        # Bytes, not text=True. Player names carry diacritics and Windows would
        # decode git's output as cp1252 and die on the first Jokic.
        out = subprocess.run(["git", "show", f"HEAD:{path}"],
                             capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0 or not out.stdout.strip():
        return []
    import csv
    import io
    text = out.stdout.decode("utf-8-sig", errors="replace")
    return list(csv.DictReader(io.StringIO(text)))


def filled(rows: list[dict], col: str) -> float:
    """Fraction of rows with something in `col`."""
    if not rows:
        return 0.0
    return sum(1 for r in rows if str(r.get(col) or "").strip()) / len(rows)


def check_staleness(sport: str, limit: int) -> list[str]:
    rows = read_csv(OUTPUT_DIR / f"_source_status_{sport}.csv")
    problems = []
    for r in rows:
        if not str(r.get("stale") or "").strip():
            continue
        age = to_num(r.get("age_days"))
        if age is not None and age >= limit:
            problems.append(
                f"{sport}: {r['source']} has been on its last-good copy for "
                f"{age:.0f} days (limit {limit}). It is not coming back on its "
                f"own -- replace the source or disable it deliberately.")
    return problems


def check_structure(sport: str) -> list[str]:
    live = read_csv(OUTPUT_DIR / f"combined_rankings_{sport}.csv")
    if not live:
        return [f"{sport}: board is empty"]
    prev = previous_board(sport)
    if not prev:
        print(f"  {sport}: no previous board to compare against -- "
              f"structure check skipped")
        return []

    problems = []
    floor = int(len(prev) * MIN_ROWS_FRACTION)
    if len(live) < floor:
        problems.append(
            f"{sport}: board fell to {len(live)} players from {len(prev)} "
            f"({100 * len(live) / len(prev):.0f}%). Below {floor} this is a "
            f"parser failing, not a source moving.")

    for col in prev[0]:
        if col not in live[0]:
            problems.append(f"{sport}: column {col!r} disappeared from the board")
            continue
        before, now = filled(prev, col), filled(live, col)
        if before >= WAS_POPULATED and now < NOW_EMPTY:
            problems.append(
                f"{sport}: column {col!r} went from {100 * before:.0f}% "
                f"populated to {100 * now:.0f}%. Something upstream of it "
                f"stopped resolving.")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sport", default="all", choices=("all",) + SPORTS)
    args = ap.parse_args()
    sports = SPORTS if args.sport == "all" else (args.sport,)

    problems = []
    for sport in sports:
        if not (OUTPUT_DIR / f"combined_rankings_{sport}.csv").exists():
            print(f"  {sport}: no board written -- skipped")
            continue
        try:
            limit = int((load_config(sport).get("stale_fail_days")
                         or DEFAULT_STALE_FAIL_DAYS))
        except Exception:
            limit = DEFAULT_STALE_FAIL_DAYS
        problems += check_staleness(sport, limit)
        problems += check_structure(sport)

    if not problems:
        print("[health] all boards look structurally sound and no source is "
              "past its staleness limit")
        return 0

    print("\n!! HEALTH CHECK FAILED\n")
    for p in problems:
        print(f"   - {p}")
    print("\nThe boards were still written and will still be committed -- a "
          "degraded board\nis worth publishing, it just should not be "
          "published quietly. This run is red\nso the failure reaches an "
          "inbox instead of a log nobody reads.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
