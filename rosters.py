#!/usr/bin/env python3
"""
Normalize a manual roster extract to the same player names the boards use.

    python rosters.py --sport mlb
    python rosters.py --sport all --cache

Reads the newest CSV or XLSX in rosters/<sport>/ (files starting with "_" are
ignored) and writes output/rosters_<sport>.csv:

    player,fantasy_team

`player` is the naming authority's spelling, so the file joins cleanly against
combined_rankings_<sport>.csv. Rosters stay their own output rather than being
folded into the board, so a rostered player the ranking source doesn't cover
still appears here.

Matching is id-first where the export carries ids. Fantrax's asterisk-wrapped
`*05ucd*` values are the same global player ids its API returns, and they are
exact where names are not -- sixteen players on the MLB roster share a name
with someone else in the pool.

No pandas -- see common.py.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
from collections import Counter
from pathlib import Path

from common import (HERE, OUTPUT_DIR, Fetcher, build_resolver, load_aliases,
                    load_fullnames, normalize_name, prepare_authority,
                    read_csv, sheet_grid, snapshot, write_csv)
from roster_readers import (_clean, parse_mapped_grid, parse_yahoo_grid,
                            read_mapped_csv, read_yahoo_paste)

ROSTERS_DIR = HERE / "rosters"
CONFIG_DIR = HERE / "config"
SPORTS = ("nba", "nfl", "mlb")

# Kyle's leagues: MLB carries ~67 per team, NBA and NFL cap at 35 (30 roster
# spots plus IR places that fluctuate). A parsed team well outside that means
# the block boundaries went wrong, not that the data is odd.
MAX_PER_TEAM = {"nba": 35, "nfl": 35, "mlb": 70}

# Text that should never survive into a player name. If it does, the parse
# succeeded structurally but produced junk -- the dangerous middle ground
# between "worked" and "obviously failed".
# NB: no empty string in here. "" is a substring of everything, so one slipping
# in flags every name as dirty and the check becomes noise.
JUNK_MARKERS = ("Player Note", "Video Forecast", "No new player",
                "Notes", " - ")


def validate(rows: list[dict], sizes: Counter, sport: str) -> list[str]:
    """Cheap sanity checks. The goal is to make a bad parse loud.

    A file that fails outright is easy; the risk is one that half-works -- a
    team block split in the wrong place, or decoration left welded onto names
    -- and quietly produces a plausible-looking file.
    """
    problems = []
    cap = MAX_PER_TEAM.get(sport, 999)
    over = {t: n for t, n in sizes.items() if n > cap}
    if over:
        problems.append(f"team(s) over the {cap}-player cap: {over} -- "
                        f"block boundaries likely wrong")
    if len(sizes) < 2:
        problems.append(f"only {len(sizes)} team(s) found -- expected the whole league")
    tiny = {t: n for t, n in sizes.items() if n < 5}
    if tiny:
        problems.append(f"suspiciously small team(s): {tiny}")

    dirty = [r["name"] for r in rows
             if any(m in str(r["name"]) for m in JUNK_MARKERS)]
    if dirty:
        problems.append(f"{len(dirty)} name(s) still carry page decoration, "
                        f"e.g. {dirty[0]!r} -- the reader did not undecorate them")
    blank = sum(1 for r in rows if not str(r["name"]).strip())
    if blank:
        problems.append(f"{blank} blank player name(s)")
    pairs = Counter((r["name"], r["fantasy_team"]) for r in rows)
    dupes = sum(n for n in pairs.values() if n > 1)
    if dupes:
        problems.append(f"{dupes} duplicated player/team row(s)")
    return problems


def override_rows(rcfg: dict, sport: str, fetch) -> list[dict]:
    """Manual ownership edits, from the Sheet's Overrides tab or a local file.

    The Sheet is the live path. The local `rosters/<sport>/_overrides.csv` is a
    fallback -- it cannot be the primary any more, because `rosters/` is
    gitignored and so GitHub Actions never sees it.

    Sheet tab columns: sport, player, fantasy_team. One tab covers all three
    sports; rows are filtered by the `sport` column.
    """
    sheet_id, tab = rcfg.get("sheet_id"), rcfg.get("overrides_tab")
    if sheet_id and tab:
        grid = sheet_grid(sheet_id, tab, fetch, float(rcfg.get("cache_hours", 0)),
                          expect={"player", "fantasy_team"})
        if len(grid) < 2:
            return []
        header = [_clean(h).lower() for h in grid[0]]
        rows = [dict(zip(header, [_clean(c) for c in r])) for r in grid[1:]]
        return [r for r in rows
                if (r.get("player") or "").strip()
                and (not r.get("sport") or r["sport"].lower() == sport.lower())]

    path = ROSTERS_DIR / sport / "_overrides.csv"
    if not path.exists():
        return []
    return [r for r in read_csv(path, comment="#") if (r.get("player") or "").strip()]


def apply_overrides(out: list[dict], ov: list[dict], resolve_name):
    """Layer manual ownership edits on top of the parsed roster.

    Yahoo leagues go dormant out of season, so from roughly January the pasted
    NFL rosters stop reflecting reality even though teams keep trading. Editing
    output/ is no good -- the next run overwrites it -- so edits live in
    rosters/<sport>/_overrides.csv and are re-applied every time.

    The leading underscore is deliberate: newest_roster() already skips files
    starting with one, so an overrides file can never be mistaken for an input.

        player,fantasy_team      move or add
        player,                  blank team = drop
    """
    if not ov:
        return out, []

    notes = []
    current = {r["player"]: r["fantasy_team"] for r in out}
    for r in ov:
        raw = str(r["player"]).strip()
        matched = resolve_name(raw)
        canonical = matched or raw
        team = str(r.get("fantasy_team") or "").strip()
        was = current.get(canonical)

        if matched is None and team:
            # Adding a name the authority doesn't know is legitimate for a deep
            # prospect, but it is also exactly what a typo looks like.
            notes.append(f"WARNING {raw!r} did not match any known player -- "
                         f"adding as typed; check the spelling")
        if team.upper() in ("", "FA", "DROP", "NONE"):
            if was is None:
                notes.append(f"drop {canonical!r}: not on the roster, ignored")
            else:
                del current[canonical]
                notes.append(f"dropped {canonical!r} from {was}")
        elif was is None:
            current[canonical] = team
            notes.append(f"added {canonical!r} to {team}")
        elif was != team:
            current[canonical] = team
            notes.append(f"moved {canonical!r} {was} -> {team}")
        else:
            notes.append(f"no-op {canonical!r} already on {team}")
        if canonical != raw and not notes[-1].startswith("WARNING"):
            notes[-1] += f"  (matched {raw!r})"

    merged = [{"player": p, "fantasy_team": t} for p, t in current.items()]
    merged.sort(key=lambda r: (r["fantasy_team"], r["player"]))
    return merged, notes


def newest_roster(sport: str) -> Path | None:
    folder = ROSTERS_DIR / sport
    if not folder.exists():
        return None
    files = [p for p in folder.iterdir()
             if p.suffix.lower() in (".csv", ".xlsx", ".xlsm")
             and not p.name.startswith("_") and not p.name.startswith("~$")]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def team_mapping(rcfg: dict, sport: str, fetch) -> dict:
    """Source team name -> Kyle's stable code, from the Sheet's mapping tab.

    Without this, someone renaming their fantasy team reads as every player on
    it being traded at once. The codes also make one owner the same label in
    all three sports.
    """
    sheet_id, tab = rcfg.get("sheet_id"), rcfg.get("mapping_tab")
    if not (sheet_id and tab):
        return {}
    grid = sheet_grid(sheet_id, tab, fetch, float(rcfg.get("cache_hours", 0)),
                      expect={sport})
    if len(grid) < 2:
        return {}
    header = [_clean(h).upper() for h in grid[0]]
    if sport.upper() not in header:
        print(f"[warn] mapping tab has no {sport.upper()} column -- "
              f"found {header}")
        return {}
    code_i, src_i = 0, header.index(sport.upper())

    out, seen = {}, {}
    for row in grid[1:]:
        if len(row) <= src_i:
            continue
        src, code = _clean(row[src_i]), _clean(row[code_i])
        if not src or not code:
            continue
        # A duplicate silently merges two teams into one, which looks like
        # plausible data. Refuse rather than guess.
        if src in out:
            print(f"[warn] mapping: {src!r} listed twice -- ignoring the second")
            continue
        seen.setdefault(code, []).append(src)
        out[src] = code
    dupe_codes = {c: v for c, v in seen.items() if len(v) > 1}
    if dupe_codes:
        print(f"[warn] mapping: code(s) used for more than one team: {dupe_codes}")
    return out


def run_sport(sport: str, args) -> int:
    cfg = json.loads((CONFIG_DIR / f"{sport}.json").read_text(encoding="utf-8-sig"))
    rcfg = cfg.get("rosters", {})
    sheet_id, tab = rcfg.get("sheet_id"), rcfg.get("tab", sport.upper())

    path = None if sheet_id else newest_roster(sport)
    if not sheet_id and path is None:
        print(f"[skip] {sport}: no sheet configured and no file in rosters/{sport}/")
        return 0

    print(f"\n=== {sport.upper()} ===")
    if sheet_id:
        print(f"[roster] Google Sheet tab {tab!r}")
    else:
        age_h = (time.time() - path.stat().st_mtime) / 3600
        print(f"[roster] {path.name} ({age_h:.0f}h old, local fallback)")

    # The authority loads BEFORE parsing so the Yahoo reader can undecorate
    # names by matching against real players rather than stripping suffixes.
    fetch = Fetcher(args.cache, cfg.get("request_delay_seconds", 2))
    mod = importlib.import_module(f"sources_{sport}")
    aliases = load_aliases(HERE / f"aliases-{sport}.csv")
    auth = prepare_authority(mod.name_authority(cfg, fetch), aliases)
    resolve = build_resolver(auth)
    known_names = {a["name"] for a in auth.values()}

    id_col = rcfg.get("id_col")
    player_col = rcfg.get("player_col", "Player")
    team_col = rcfg.get("team_col", "Status")

    try:
        if sheet_id:
            grid = sheet_grid(sheet_id, tab, fetch,
                              float(rcfg.get("cache_hours", 0)))
            # Detect the shape rather than configuring it: a Fantrax export has
            # the declared columns in its header, a Yahoo paste does not.
            header = [c.strip() for c in (grid[0] if grid else [])]
            if player_col in header and team_col in header:
                raw = parse_mapped_grid(grid, id_col, player_col, team_col)
            else:
                raw = parse_yahoo_grid(grid, known_names)
        elif path.suffix.lower() == ".csv":
            raw = read_mapped_csv(path, id_col, player_col, team_col)
        else:
            raw = read_yahoo_paste(path, known_names)
    except KeyError as exc:
        print(f"!! {exc}\n   set the columns in config/{sport}.json under \"rosters\"")
        return 1

    if not raw:
        print("!! roster parsed to zero players")
        return 1

    mapping = team_mapping(rcfg, sport, fetch)
    if mapping:
        unmapped = sorted({r["fantasy_team"] for r in raw
                           if _clean(r["fantasy_team"]) not in mapping})
        for r in raw:
            r["fantasy_team"] = mapping.get(_clean(r["fantasy_team"]),
                                            r["fantasy_team"])
        print(f"[map]    {len(mapping)} team codes applied"
              + (f" -- UNMAPPED: {unmapped}" if unmapped else ""))

    sizes = Counter(r["fantasy_team"] for r in raw)
    print(f"[parse]  {len(raw)} players across {len(sizes)} teams "
          f"({min(sizes.values())}-{max(sizes.values())} each)")
    for p in validate(raw, sizes, sport):
        print(f"[warn]   {p}")

    by_id = {}
    if any("source_id" in r for r in raw):
        by_id = {str(a["scorer_id"]): a["name"] for a in auth.values()
                 if a.get("scorer_id")}

    rows, unresolved, id_hits = [], [], 0
    for r in raw:
        canonical = None
        if by_id and r.get("source_id"):
            canonical = by_id.get(str(r["source_id"]))
            if canonical:
                id_hits += 1
        if canonical is None:
            key = normalize_name(r["name"])
            key = aliases.get(key, key)
            uid = resolve(key, r.get("team") or None, r.get("pos") or None)
            canonical = auth[uid]["name"] if uid is not None else None
        if canonical is None:
            # KEPT, not dropped -- Kyle's call, and right: the roster and the
            # naming authority come from the same site, so an unmatched name is
            # rare and its spelling is already that site's own convention.
            # Dropping it would quietly shrink a real roster. Still reported.
            unresolved.append({"player": r["name"], "fantasy_team": r["fantasy_team"]})
            canonical = r["name"]
        rows.append({"player": canonical, "fantasy_team": r["fantasy_team"]})

    # Apply the same expansions rankings.py worked out, so a player isn't
    # "Giannis Antetokounmpo" on one tab and "G. Antetokounmpo" on the other.
    fullnames = load_fullnames(sport)
    if fullnames:
        by_display = {auth[k]["name"]: v for k, v in fullnames.items() if k in auth}
        hit = sum(1 for r in rows if r["player"] in by_display)
        if hit:
            for r in rows:
                r["player"] = by_display.get(r["player"], r["player"])
            print(f"[names]  expanded {hit} abbreviated name(s)")

    seen, deduped = set(), []
    for r in rows:
        pair = (r["player"], r["fantasy_team"])
        if pair not in seen:
            seen.add(pair)
            deduped.append(r)
    out = sorted(deduped, key=lambda r: (r["fantasy_team"], r["player"]))

    def _resolve_name(raw_name: str):
        key = normalize_name(raw_name)
        key = aliases.get(key, key)
        uid = resolve(key)
        return auth[uid]["name"] if uid is not None else None

    out, ov_notes = apply_overrides(out, override_rows(rcfg, sport, fetch),
                                    _resolve_name)
    if ov_notes:
        print(f"[manual]  {len(ov_notes)} override(s)")
        for n in ov_notes:
            print(f"          {n}")

    dest = OUTPUT_DIR / f"rosters_{sport}.csv"
    write_csv(dest, out, ["player", "fantasy_team"])
    print(f"[match]  {id_hits} by id, {len(rows) - id_hits} by name, "
          f"{len(unresolved)} unresolved")
    print(f"[write]  {len(out)} players -> output/{dest.name}")

    snap = snapshot(out, sport, "rosters", ["player", "fantasy_team"])
    if snap:
        print(f"[hist]   archived -> output/history/rosters/{sport}/{snap.name}")

    # Written, not just printed -- otherwise the only record of a miss is
    # scrollback, and these are exactly the rows worth reviewing later.
    write_csv(OUTPUT_DIR / f"rosters_unresolved_{sport}.csv", unresolved,
              ["player", "fantasy_team"])
    if unresolved:
        print(f"\nUnresolved ({len(unresolved)}) -- not in the naming authority:")
        for u in unresolved[:25]:
            print(f"   {u['player']}  [{u['fantasy_team']}]")
        if len(unresolved) > 25:
            print(f"   ... and {len(unresolved) - 25} more")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default="mlb", help="nba | nfl | mlb | all")
    ap.add_argument("--cache", action="store_true", help="reuse downloaded pages")
    args = ap.parse_args()
    sports = SPORTS if args.sport == "all" else tuple(
        s.strip() for s in args.sport.split(","))
    rc = 0
    for sport in sports:
        rc |= run_sport(sport, args)
    return rc


if __name__ == "__main__":
    sys.exit(main())
