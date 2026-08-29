"""
NBA sources: Hashtag Basketball dynasty + keeper, Yahoo names, FantasyPros ECR.
"""
from __future__ import annotations

import re

from common import (Fetcher, apply_roster_positions, fantasypros, grid_to_rows,
                    html_tables, is_player, page_text, pick_seasonal_url, to_num,
                    yahoo_player_list)

POS = r"(?:PG|SG|SF|PF|C)"

# The dynasty page is a card layout. Anchor on the Dynasty#<n> block at the end
# of each card and read backwards to the header.
#
# The cards are raggeder than they look. Three shapes have to survive:
#   veteran -> "1 Victor Wembanyama PF C SA 22.6yo <stats> Dynasty #1 ..."
#   rookie  -> "245 (NEW) Henri Veesaar ATL 22.4yo <no stats> Dynasty #245 ..."
#   oddball -> "290 46 Moussa Cisse C DAL yo <stats> Dynasty #290 ..."
#
# So position, age and the trailing keeper fields are ALL optional. Requiring
# position and keeper cost 57 rookies (incl. Cameron Boozer at dynasty #10);
# requiring age cost Moussa Cisse, whose age is simply blank. Only the name,
# team, the "yo" marker and Dynasty#<n> are load-bearing.
CARD_RE = re.compile(
    r"(?P<rank>\d{1,3})\s+"
    r"(?:\(NEW\)\s+)?"
    r"(?P<name>[A-Za-zÀ-ɏ][A-Za-zÀ-ɏ.'’\- ]{2,38}?)\s+"
    rf"(?:(?P<pos>{POS}(?:(?:\s*[,/]\s*|\s+){POS})*)\s+)?"
    r"(?P<team>[A-Z]{2,3})\s+"
    r"(?:(?P<age>\d{1,2}\.\d)\s*)?yo"
    r".{0,1500}?"
    r"Dynasty\s*#\s*(?P<dyn>\d+)",
    re.S,
)


def _parse_dynasty(html: str) -> list[dict]:
    text = page_text(html)
    seen, rows = set(), []
    for m in CARD_RE.finditer(text):
        rank = int(m.group("dyn"))
        if rank in seen:
            continue
        seen.add(rank)
        rows.append({"name": m.group("name").strip(),
                     "team": m.group("team"),
                     "pos": (m.group("pos") or "").replace(" ", ","),
                     "rank": rank})
    rows.sort(key=lambda r: r["rank"])

    # The page states its own count -- if we matched fewer, say so rather than
    # silently shipping a short board.
    claimed = len(re.findall(r"Dynasty\s*#\s*\d+", text))
    if claimed and len(rows) < claimed:
        print(f"[warn] dynasty: matched {len(rows)} of {claimed} cards "
              f"({claimed - len(rows)} unparsed -- run --inspect)")
    return rows


def _parse_keeper(html: str) -> list[dict]:
    """Keeper page is a normal ASP.NET GridView: # / PLAYER / TEAM / POS / AGE / VALUE."""
    best = None
    for grid in html_tables(html):
        head = {h.strip().upper() for h in grid[0]}
        if {"PLAYER", "VALUE"} <= head and (best is None or len(grid) > len(best)):
            best = grid
    if not best:
        return []

    rows = []
    for r in grid_to_rows(best):
        r = {str(k).strip().upper(): v for k, v in r.items()}
        name = str(r.get("PLAYER", "")).strip()
        if not name or name.upper() == "PLAYER":   # repeated header rows
            continue
        if not is_player(name):
            continue
        rank = to_num(r.get("#"))
        if rank is None:
            continue
        rows.append({"name": name, "team": r.get("TEAM"),
                     "pos": r.get("POS"), "rank": int(rank)})
    return rows


# ------------------------------------------------------------ interface ----


def rank_sources(cfg: dict, fetch: Fetcher) -> dict:
    src = cfg["sources"]
    return {
        "dynasty": _parse_dynasty(fetch(src["dynasty"]["url"])),
        "keeper": _parse_keeper(fetch(src["keeper"]["url"])),
    }


# Yahoo speaks both granularities here: the player list is coarse (G/F/C and
# the two pairs), while a roster paste can carry PG/SG/SF/PF. Both are accepted
# -- the roster's answer is the league's own, whichever vocabulary it uses.
NBA_POSITIONS = {"PG", "SG", "SF", "PF", "G", "F", "C"}


def name_authority(cfg: dict, fetch: Fetcher) -> list[dict]:
    """Yahoo's player list, with positions from the roster tab where it has them.

    Multi-position eligibility is kept whole -- "G,F" is the useful part of an
    NBA row, and collapsing it the way NFL does would throw away half of it.
    """
    rows = yahoo_player_list(cfg["name_authority"], fetch)
    return apply_roster_positions(rows, cfg, fetch, NBA_POSITIONS)


def current_rank(cfg: dict, fetch: Fetcher) -> list[dict]:
    return fantasypros(pick_seasonal_url(cfg["current"]), fetch)


def inspect(cfg: dict, fetch: Fetcher) -> None:
    """Dump page structure when a parser stops matching."""
    html = fetch(cfg["sources"]["dynasty"]["url"])
    text = page_text(html)
    print(f"dynasty page text: {len(text):,} chars")
    print(f"card-regex matches : {len(list(CARD_RE.finditer(text)))}")
    print(f"'Dynasty #' seen   : {len(re.findall(r'Dynasty\s*#\s*\d+', text))}")
    i = text.find("Dynasty #")
    print("\n--- 700 chars around the first 'Dynasty #' ---")
    print(text[max(0, i - 500): i + 200] if i != -1 else text[:700])
    keeper = _parse_keeper(fetch(cfg["sources"]["keeper"]["url"]))
    print(f"\nkeeper rows: {len(keeper)}")
    print(keeper[:3])
