"""
NFL sources: FantasyPros dynasty superflex + KeepTradeCut, Yahoo names,
FantasyPros half-PPR ECR for the current-rank column.

Both dynasty sources are public and scraped directly, so there is nothing to
export by hand. This replaced the paywalled DLF CSV imports on 2026-08-22; the
old reader is in git history if it is ever wanted back.

The two disagree meaningfully, which is rather the point of blending them:
FantasyPros (expert consensus) puts Josh Allen at superflex #1, while KTC
(crowd-sourced trade values) has him 4th behind Gibbs, Chase and Robinson.
"""
from __future__ import annotations

import json

from common import (Fetcher, fantasypros, pick_seasonal_url, to_num,
                    yahoo_player_list)

# KTC ships the whole board inline as `playersArray`, carrying BOTH formats --
# superflexValues and oneQBValues -- so one fetch covers either. The rank we
# want is the scalar `rank` inside the format block, NOT the one nested in its
# tep/tepp/teppp tier-premium variants.
KTC_VALUE_KEY = "superflexValues"

# Anything that isn't one of these is a draft pick rather than a player. The
# Yahoo name list is offence-only, so kickers and defences are out of scope.
REAL_POSITIONS = {"QB", "RB", "WR", "TE"}


def _keeptradecut(cfg: dict, fetch: Fetcher) -> list[dict]:
    html = fetch(cfg["url"], max_age_hours=float(cfg.get("cache_hours", 0)))
    i = html.find("playersArray")
    if i == -1:
        print("[warn] keeptradecut: no playersArray in the page")
        return []
    try:
        players, _ = json.JSONDecoder().raw_decode(html, html.find("[", i))
    except ValueError:
        print("[warn] keeptradecut: playersArray did not parse")
        return []

    key = cfg.get("value_key", KTC_VALUE_KEY)
    rows = []
    for p in players:
        if p.get("position") not in REAL_POSITIONS:
            continue
        rank = to_num((p.get(key) or {}).get("rank"))
        if rank is None:
            continue
        rows.append({"name": (p.get("playerName") or "").strip(),
                     "team": p.get("team"),
                     "pos": p.get("position"),
                     "rank": int(rank)})
    return rows


def _fantasypros_dynasty(cfg: dict, fetch: Fetcher) -> list[dict]:
    """Same embedded ecrData as the current-rank pages, just a different URL."""
    return [{"name": r["name"], "team": r.get("team"), "rank": r["current_rank"]}
            for r in fantasypros(cfg["url"], fetch)]


# ------------------------------------------------------------ interface ----


def rank_sources(cfg: dict, fetch: Fetcher) -> dict:
    src = cfg["sources"]
    return {
        "fp_dynasty": _fantasypros_dynasty(src["fp_dynasty"], fetch),
        "ktc": _keeptradecut(src["ktc"], fetch),
    }


def name_authority(cfg: dict, fetch: Fetcher) -> list[dict]:
    return yahoo_player_list(cfg["name_authority"], fetch)


def current_rank(cfg: dict, fetch: Fetcher) -> list[dict]:
    return fantasypros(pick_seasonal_url(cfg["current"]), fetch)


def inspect(cfg: dict, fetch: Fetcher) -> None:
    for key, fn in (("fp_dynasty", _fantasypros_dynasty), ("ktc", _keeptradecut)):
        rows = fn(cfg["sources"][key], fetch)
        print(f"{key}: {len(rows)} players")
        for r in rows[:5]:
            print("   ", r)
        print()
    print(f"yahoo: {len(yahoo_player_list(cfg['name_authority'], fetch))} players")
