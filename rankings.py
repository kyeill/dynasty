#!/usr/bin/env python3
"""
dynasty
=======
Blend dynasty ranking sources into one board per sport, keyed to the exact
player names used by a chosen naming authority, with position, a value score
and a "current" consensus rank appended.

    python rankings.py --sport nba
    python rankings.py --sport all --cache
    python rankings.py --sport nba --weights dynasty=0.5,keeper=0.5
    python rankings.py --sport nba --missing drop
    python rankings.py --sport nba --cache --inspect

Every sport follows the same pipeline; only the parsers differ, and those live
in sources_<sport>.py:

    rank_sources()  -> the lists that get blended
    name_authority()-> AUTHORITY for player name + position
    current_rank()  -> an extra column, never part of the ordering
    extra_ranks()   -> optional further columns, also never part of it

A player is only written to the board if the naming authority lists him,
because the whole point is that every name matches it exactly.

Settings live in config/<sport>.json. No pandas -- see common.py.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
from pathlib import Path

from common import (CACHE_DIR, HERE, OUTPUT_DIR, Fetcher, blend,
                    build_resolver, expand_abbreviated, index_by_key,
                    load_aliases, prepare_authority, read_csv, save_fullnames,
                    snapshot, to_num, value_scale, write_csv)

CONFIG_DIR = HERE / "config"
SPORTS = ("nba", "nfl", "mlb")


def load_config(sport: str) -> dict:
    path = CONFIG_DIR / f"{sport}.json"
    if not path.exists():
        print(f"!! no config for '{sport}' -- expected {path}")
        sys.exit(2)
    # utf-8-sig, not utf-8: Notepad (and PowerShell's Set-Content) write a BOM,
    # which plain json.loads rejects outright.
    return json.loads(path.read_text(encoding="utf-8-sig"))


def guard_current(rows: list[dict], sport: str, floor: int) -> list[dict]:
    """Keep the last healthy 'current rank' pull so a bad day can't erase a good one.

    A source can degrade badly while still returning HTTP 200 -- FantasyPros
    served exactly ONE NBA player on 2026-08-22 with no error. Accepted
    silently that blanks current_rank for hundreds of players, and since this
    runs unattended nobody would notice for weeks.

    Two thresholds, because a hard floor alone is blunt: below `floor` the pull
    is treated as broken and the snapshot is used; above it but collapsed
    against the last good one, it warns and proceeds. The second check is
    self-calibrating, so there is no number to retune as a pool grows.
    """
    snap = CACHE_DIR / f"current-last-good-{sport}.csv"
    prev = read_csv(snap) if snap.exists() else None

    if len(rows) >= floor and rows:
        if prev and len(rows) < len(prev) * 0.5:
            print(f"[warn] current rank dropped sharply: {len(rows)} players vs "
                  f"{len(prev)} last time -- using it anyway, but worth a look")
        write_csv(snap, rows, ["name", "team", "current_rank"])
        return rows

    print(f"[warn] current rank returned {len(rows)} players, expected >= {floor}")
    if prev:
        age_d = (time.time() - snap.stat().st_mtime) / 86400
        print(f"[warn] falling back to last good snapshot: {len(prev)} players, "
              f"{age_d:.1f} days old")
        return prev
    print("[warn] no snapshot on disk -- current_rank will be blank this run")
    return rows


def run_sport(sport: str, args) -> int:
    cfg = load_config(sport)
    floors = cfg.get("sanity_floor", {})
    fetch = Fetcher(args.cache, cfg.get("request_delay_seconds", 2))
    mod = importlib.import_module(f"sources_{sport}")

    if args.weights:
        for pair in args.weights.split(","):
            k, _, v = pair.partition("=")
            if k.strip() in cfg["sources"]:
                cfg["sources"][k.strip()]["weight"] = float(v)
    policy = args.missing or cfg.get("missing_policy", "penalty")

    if args.inspect:
        if hasattr(mod, "inspect"):
            mod.inspect(cfg, fetch)
        else:
            print(f"[inspect] sources_{sport} has no inspect()")
        return 0

    print(f"\n=== {sport.upper()} ===")
    parsed = mod.rank_sources(cfg, fetch)
    authority = mod.name_authority(cfg, fetch)
    current = guard_current(mod.current_rank(cfg, fetch), sport,
                            int(floors.get("current", 10)))

    # The ranking sources and the naming authority are load-bearing -- no board
    # without them. The current rank is only an extra column, so a bad pull
    # there warns but must not throw away otherwise-good rows.
    for name, rows in list(parsed.items()) + [("authority", authority)]:
        print(f"[parse] {name}: {len(rows)} players")
        if not rows:
            print(f"\n!! {name} parsed to zero rows. "
                  f"Run:  python rankings.py --sport {sport} --cache --inspect")
            return 1
        floor = floors.get(name)
        if floor and len(rows) < floor:
            print(f"[warn] {name}: {len(rows)} rows is below the expected floor "
                  f"of {floor} -- the page may have changed")
    print(f"[parse] current: {len(current)} players")

    aliases = load_aliases(HERE / f"aliases-{sport}.csv")
    auth = prepare_authority(authority, aliases)
    resolve = build_resolver(auth)
    dropped = len(authority) - len(auth)
    if dropped:
        print(f"[parse] authority: {dropped} rows collapsed as indistinguishable")

    frames = {n: index_by_key(rows, aliases, resolve) for n, rows in parsed.items()}
    weights = {n: cfg["sources"][n]["weight"] for n in frames}
    print(f"[blend] weights {weights}  policy={policy}")

    board = blend(frames, weights, policy)
    if not board:
        print("\n!! nothing to rank after blending")
        return 1

    cur = index_by_key(current, aliases, resolve) if current else {}

    # Optional reference columns -- resolved onto the same players, but never
    # part of the blended ordering. A sport without them is unaffected.
    extras = {}
    if hasattr(mod, "extra_ranks"):
        for key, rows in mod.extra_ranks(cfg, fetch).items():
            extras[key] = index_by_key(rows, aliases, resolve) if rows else {}
            hits = sum(1 for r in board if r["key"] in extras[key])
            print(f"[parse] {key}: {len(rows)} players, {hits} matched onto the board")

    for r in board:
        k = r["key"]
        a = auth.get(k)
        r["player"] = a["name"] if a else None
        r["pos"] = a.get("pos") if a else None
        r["team"] = a.get("team") if a else None
        c = cur.get(k)
        r["current_rank"] = int(c["current_rank"]) if c else None
        for key, table in extras.items():
            hit = table.get(k)
            r[key] = int(to_num(hit["rank"])) if hit else None

    # Yahoo abbreviates long first names; the ranking sources don't. Recover
    # the full form from them, and hand the result to rosters.py, which has no
    # ranking sources of its own to ask.
    name_sources = list(frames.values()) + ([cur] if cur else [])
    expanded = {}
    for r in board:
        full = expand_abbreviated(r["player"], r["key"], name_sources)
        if isinstance(r["player"], str) and full != r["player"]:
            expanded[r["key"]] = full
            r["player"] = full
    if expanded:
        save_fullnames(expanded, sport)
        print(f"[names] expanded {len(expanded)} abbreviated: "
              + ", ".join(sorted(expanded.values())[:4])
              + (" ..." if len(expanded) > 4 else ""))

    ranked_unmatched = [r for r in board if not r["player"]]
    board = [r for r in board if r["player"]]
    ranked_keys = {r["key"] for r in board}

    for i, r in enumerate(board, 1):
        r["combined_rank"] = i

    # 100 for the best player, 0 at replacement level. See common.value_scale.
    have_value = False
    vcfg = cfg.get("value") or {}
    if vcfg.get("replacement_rank"):
        scale = value_scale(vcfg["replacement_rank"], vcfg.get("top_20_ratio", 2.1))
        for r in board:
            r["value"] = scale(r["combined_rank"])
            r["current_value"] = (scale(r["current_rank"])
                                  if r["current_rank"] is not None else None)
        have_value = True
        above = sum(1 for r in board if r["value"] > 0)
        print(f"[value] #1=100, zero at rank {vcfg['replacement_rank']} "
              f"-- {above} players above replacement")

    cols = (["combined_rank", "player", "pos", "team"]
            + (["value"] if have_value else [])
            + ["blended_score"]
            + [f"{n}_rank" for n in frames]
            + ["current_rank"]
            + (["current_value"] if have_value else [])
            + list(extras) + ["sources_matched"])

    out_path = OUTPUT_DIR / f"combined_rankings_{sport}.csv"
    write_csv(out_path, board, cols)
    print(f"\n[write] {len(board)} players -> output/{out_path.name}")

    snap = snapshot(board, sport, "boards", cols)
    if snap:
        print(f"[hist]  archived -> output/history/boards/{sport}/{snap.name}")

    # Two very different problems, so label them. Only the first is actionable:
    # a ranked player missing from the authority is usually a spelling
    # difference that belongs in aliases-<sport>.csv. Carrying the source ranks
    # through matters -- whether a miss sat at #12 or #900 is the only thing
    # that says whether it is worth an alias.
    rank_cols = [f"{n}_rank" for n in frames]
    notes = []
    for r in ranked_unmatched:
        note = {"reason": "ranked, no name match (add to aliases)", "player": r["key"]}
        vals = [r.get(c) for c in rank_cols if r.get(c) is not None]
        note.update({c: r.get(c) for c in rank_cols})
        note["best_rank"] = min(vals) if vals else None
        notes.append(note)
    notes += [{"reason": "on authority, unranked by all sources", "player": a["name"]}
              for k, a in auth.items() if k not in ranked_keys]
    write_csv(OUTPUT_DIR / f"unmatched_{sport}.csv", notes,
              ["reason", "player"] + rank_cols + ["best_rank"])
    n_alias = sum(1 for n in notes if n["reason"].startswith("ranked"))
    print(f"[write] {len(notes)} unmatched -> output/unmatched_{sport}.csv "
          f"({n_alias} need aliases)")

    print(f"\nTop 10 ({sport}):")
    widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in board[:10]))
              for c in cols}
    print("  ".join(c.rjust(widths[c]) for c in cols))
    for r in board[:10]:
        print("  ".join(str(r.get(c, "") if r.get(c) is not None else "")
                        .rjust(widths[c]) for c in cols))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sport", default="nba", help="nba | nfl | mlb | all")
    ap.add_argument("--cache", action="store_true", help="reuse downloaded pages")
    ap.add_argument("--inspect", action="store_true", help="dump page structure and exit")
    ap.add_argument("--weights", help="e.g. dynasty=0.67,keeper=0.33")
    ap.add_argument("--missing", choices=["drop", "renormalize", "penalty"])
    ap.add_argument("--if-stale", type=float, metavar="HOURS",
                    help="skip a sport whose output is newer than HOURS old")
    args = ap.parse_args()

    sports = SPORTS if args.sport == "all" else tuple(
        s.strip() for s in args.sport.split(","))

    rc = 0
    for sport in sports:
        if not (CONFIG_DIR / f"{sport}.json").exists():
            if args.sport == "all":
                continue  # a sport that isn't set up yet is not an error
            print(f"!! no config for '{sport}'")
            return 2
        # Lets the scheduled task fire on several triggers (daily AND at logon,
        # for a machine that is not reliably on at a fixed hour) without
        # refetching every time one of them happens to land.
        if args.if_stale:
            out = OUTPUT_DIR / f"combined_rankings_{sport}.csv"
            if out.exists():
                age_h = (time.time() - out.stat().st_mtime) / 3600
                if age_h < args.if_stale:
                    print(f"[skip] {sport}: output is {age_h:.1f}h old "
                          f"(< {args.if_stale}h)")
                    continue
        rc |= run_sport(sport, args)
    return rc


if __name__ == "__main__":
    sys.exit(main())
