#!/usr/bin/env python3
"""
Every player that failed to resolve, across all sports and both pipelines.

    python unresolved_report.py

Reads what the last runs wrote -- it does not re-fetch anything. Run
rankings.py and rosters.py first if you want it current.

Two different failures are reported, and only one is usually worth acting on:

  RANKINGS  a ranked player who could not be matched to the naming authority.
            Actionable when the rank is high -- that is a real board omission.
            Deep ranks are usually players the authority's league does not
            carry.

  ROSTERS   a player on your actual roster who is not in the authority at all.
            Never fixable with an alias.

No pandas -- see common.py.
"""
from __future__ import annotations

import sys

from common import OUTPUT_DIR, read_csv, to_num

SPORTS = ("nba", "nfl", "mlb")
# A miss inside this many ranks is worth a look; beyond it, it's the long tail.
NOTABLE = 250


def main() -> int:
    any_found = False

    for sport in SPORTS:
        print(f"\n{'=' * 62}\n{sport.upper()}\n{'=' * 62}")

        board_path = OUTPUT_DIR / f"unmatched_{sport}.csv"
        if board_path.exists():
            rows = read_csv(board_path)
            ranked = [r for r in rows
                      if str(r.get("reason", "")).startswith("ranked")]
            rank_cols = [c for c in (rows[0] if rows else {})
                         if c.endswith("_rank") and c != "best_rank"]
            for r in ranked:
                r["_best"] = to_num(r.get("best_rank"))
            ranked.sort(key=lambda r: (r["_best"] is None, r["_best"] or 0))
            notable = [r for r in ranked
                       if r["_best"] is not None and r["_best"] <= NOTABLE]
            tail = [r for r in ranked if r not in notable]

            print(f"\nRANKINGS -- {len(ranked)} ranked players unmatched")
            if notable:
                any_found = True
                print(f"  inside the top {NOTABLE} ({len(notable)}) -- worth a look:")
                # Show each source separately. A single "best rank" is
                # misleading when the lists have very different depths -- NBA
                # blends a 400-deep dynasty list with a 751-deep keeper one, so
                # a keeper #170 is mid-table filler, not a near-miss star.
                print("     " + "  ".join(f"{c.replace('_rank', ''):>9}"
                                          for c in rank_cols) + "   player")
                for r in notable[:25]:
                    cells = []
                    for c in rank_cols:
                        v = to_num(r.get(c))
                        cells.append(f"{int(v):>9}" if v is not None else f"{'-':>9}")
                    print("     " + "  ".join(cells) + f"   {r['player']}")
                if len(notable) > 25:
                    print(f"     ... and {len(notable) - 25} more")
            else:
                print(f"  none inside the top {NOTABLE}")
            if tail:
                print(f"  deeper than {NOTABLE}: {len(tail)} "
                      f"(long tail, generally not worth aliasing)")
        else:
            print("\nRANKINGS -- no unmatched file; run rankings.py")

        ros_path = OUTPUT_DIR / f"rosters_unresolved_{sport}.csv"
        if ros_path.exists():
            rows = read_csv(ros_path)
            print(f"\nROSTERS -- {len(rows)} rostered players not in the authority")
            for row in rows:
                any_found = True
                print(f"     {row['player']}  [{row['fantasy_team']}]")
        else:
            print("\nROSTERS -- no unresolved file; run rosters.py")

    print(f"\n{'=' * 62}")
    if not any_found:
        print("Nothing notable unresolved.")
    else:
        print("Aliases go in aliases-<sport>.csv as:  source spelling,Authority Spelling")
        print("Roster misses cannot be aliased -- the authority does not carry them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
