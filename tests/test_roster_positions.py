#!/usr/bin/env python3
"""
Roster-position overrides -- the parsing half, with no Sheet behind it.

    python tests/test_roster_positions.py

common.roster_position_map() is what decides which position reaches the board
for a rostered player, so it is worth pinning down away from the network. The
grids below are trimmed copies of the three real shapes: a Yahoo NBA paste
(vertical), a Yahoo NFL paste (two columns), and a Fantrax export.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common import roster_position_map  # noqa: E402
from sources_nba import NBA_POSITIONS   # noqa: E402
from sources_nfl import REAL_POSITIONS  # noqa: E402

PASTE_CFG = {"player_col": "Player", "team_col": "Status"}

# Vertical: slot, clean name, decorated name, "TEAM - POS" anchor.
NBA_GRID = [
    ["Kyle (7)"],
    ["Pos"],
    ["G"],
    ["Nolan Traore"],
    ["Nolan TraoreNew Player Note"],
    ["BKN - G"],
    ["10:00 pm @ POR"],
    ["F"],
    ["Pascal Siakam"],
    ["Pascal SiakamGTDNew Player Note"],
    ["IND - F,C"],
    ["Util"],
    ["Victor Wembanyama"],
    ["Victor WembanyamaPlayer Note"],
    ["SAS - C"],
]

# Two columns: slot in col0, clean name in col1, decorated duplicate below it.
NFL_GRID = [
    ["Kyle (7)", ""],
    ["Pos", "Player"],
    ["QB", "Lamar Jackson"],
    ["", "Lamar JacksonVideo ForecastNo new player NotesBal - QB"],
    ["W/R/T", "Travis Kelce"],
    ["", "Travis KelceNo new player NotesKC - TE"],
    ["BN", "Bijan Robinson"],
    ["", "Bijan RobinsonVideo ForecastAtl - RB"],
]

FANTRAX_GRID = [
    ["ID", "Player", "Team", "Position", "Status"],
    ["*05ucd*", "Shohei Ohtani", "LAD", "UT,SP", "Jonathan (4)"],
    ["*0abcd*", "Bobby Witt Jr.", "KC", "SS,INF", "Tommy (1)"],
]


def check(label, got, want):
    assert got == want, f"{label}\n  got  {got!r}\n  want {want!r}"
    print(f"  ok  {label}")


def main() -> int:
    print("NBA vertical paste")
    got = roster_position_map(NBA_GRID, PASTE_CFG, set(), NBA_POSITIONS)
    # Multi-position eligibility survives whole; the slot labels above each
    # player ("Util", "F") are never mistaken for the position.
    check("positions by name", got, {
        "Nolan Traore": "G",
        "Pascal Siakam": "F,C",
        "Victor Wembanyama": "C",
    })

    print("NFL two-column paste")
    got = roster_position_map(NFL_GRID, PASTE_CFG, set(), REAL_POSITIONS)
    # "W/R/T" and "BN" are roster SLOTS. The real position comes off the
    # decorated line's "KC - TE" tail, which is why they must not appear here.
    check("positions by name", got, {
        "Lamar Jackson": "QB",
        "Travis Kelce": "TE",
        "Bijan Robinson": "RB",
    })

    print("Fantrax export shape is detected, not configured")
    got = roster_position_map(FANTRAX_GRID, PASTE_CFG, set(), {"UT", "SP", "SS"})
    # "INF" is outside the allowed set and drops out, leaving the real one.
    check("positions by name", got, {
        "Shohei Ohtani": "UT,SP",
        "Bobby Witt Jr.": "SS",
    })

    print("a drifted paste cannot write junk onto the board")
    junk = [["Kyle (7)"], ["Pos"], ["G"], ["A Player"],
            ["A PlayerPlayer Note"], ["BKN - G"]]
    check("allowed set is enforced",
          roster_position_map(junk, PASTE_CFG, set(), {"F"}), {})

    print("no roster tab -> no overrides")
    check("empty grid", roster_position_map([["Pos"]], PASTE_CFG, set(),
                                            NBA_POSITIONS), {})

    print("\nall roster-position checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
