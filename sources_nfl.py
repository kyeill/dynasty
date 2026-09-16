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
import re

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


def _ktc_players(html: str):
    """The full board out of a KTC page, or None.

    Since 2026-09-08 the board lives in its own block --
    <script type="application/json" id="ktc-players"> -- and the inline code
    just reads it: `var playersArray = JSON.parse(document.getElementById(
    'ktc-players').textContent)`.

    The old reader found the text "playersArray" and decoded the next "[" after
    it. After the move, the next "[" belonged to `var oneQBPlayers = [...]`, a
    three-player start/sit widget, so KTC went from 464 players to 2 while the
    page itself was perfectly healthy. The floor caught it and the board ran on
    its last-good copy, but only a floor stood between that and a two-player
    KTC column. So: read the element by id, and require a real board's worth.
    """
    m = re.search(r"""<script[^>]*id=["']ktc-players["'][^>]*>(.*?)</script>""",
                  html, re.S)
    if m:
        try:
            data = json.loads(m.group(1))
            if isinstance(data, list):
                return data
        except ValueError:
            print("[warn] keeptradecut: ktc-players block did not parse")

    # The pre-2026-09 inline form, kept in case they move it back -- but only
    # when the "[" belongs to playersArray itself, never merely follows it.
    m = re.search(r"playersArray\s*=\s*\[", html)
    if m:
        try:
            data, _ = json.JSONDecoder().raw_decode(html, m.end() - 1)
            if isinstance(data, list):
                return data
        except ValueError:
            pass
    return None


def _keeptradecut(cfg: dict, fetch: Fetcher) -> list[dict]:
    html = fetch(cfg["url"], max_age_hours=float(cfg.get("cache_hours", 0)))
    players = _ktc_players(html)
    if players is None:
        print("[warn] keeptradecut: no player board found in the page "
              "(neither #ktc-players nor an inline playersArray)")
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


def _one_position(pos) -> str:
    """Yahoo lists dual eligibility as "RB,TE". Keep the last -- Kyle's call.

    Deliberately NFL-only, and it must stay that way. One player out of 501 is
    dual-eligible here, so a single position costs nothing. MLB (352 multi-
    position rows) and NBA (193) are the opposite case: that eligibility is the
    useful part, and collapsing "UT,SP" would strip Ohtani of the hitter half.
    """
    parts = [p.strip() for p in str(pos or "").split(",") if p.strip()]
    return parts[-1] if parts else ""


def name_authority(cfg: dict, fetch: Fetcher) -> list[dict]:
    rows = yahoo_player_list(cfg["name_authority"], fetch)
    for r in rows:
        r["pos"] = _one_position(r.get("pos"))

    # The column is supposed to be QB/RB/WR/TE and nothing else -- the list is
    # offence-only. Anything else here means a parse has gone wrong somewhere
    # upstream, which is exactly how the "PUP-P" injury badge got as far as the
    # published board. Report it rather than letting it ride a second time.
    odd = sorted({r["pos"] for r in rows if r["pos"] and r["pos"] not in REAL_POSITIONS})
    if odd:
        print(f"[warn] yahoo: position(s) outside {sorted(REAL_POSITIONS)}: {odd}")
    return rows


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
