"""
MLB sources: HarryKnowsBall dynasty rankings, Fantrax names, FantasyPros Roto,
plus two weekly PitcherList boards as reference columns.

Two things make baseball different:

  * Names do not identify players. Fantrax's pool has 152 duplicated names in
    9,926 -- three Jose Ramirezes alone. Both sources here carry a team, which
    is what lets common.prepare_authority keep them apart.

  * Fantrax's page is an Angular app behind Cloudflare, so it cannot be
    scraped. Its JSON API answers a plain POST perfectly well.
"""
from __future__ import annotations

import json
import re
from datetime import date

from bs4 import BeautifulSoup

from common import (Fetcher, fantasypros, grid_to_rows, html_tables, is_player,
                    pick_seasonal_url, sheet_grid, to_num)

FANTRAX_API = "https://www.fantrax.com/fxpa/req?leagueId={league_id}"

# PitcherList posts a fresh ranking every week at a brand-new URL. Rather than
# guess the slug, read the category's RSS feed -- WordPress lists posts newest
# first, so item 1 is always this week's.
PL_HEADERS = {"Rank", "Pitcher", "Team", "Badges", "Change"}

# The first pitcher in each tier has a tier label welded onto the surname with
# no separator: "Mason MillerT1", "Dylan LeeT1".
_TIER_SUFFIX = re.compile(r"T\d+$")


def _fantrax(cfg: dict, fetch: Fetcher) -> list[dict]:
    """Walk the Fantrax player pool through its JSON API.

    `maxResultsPerPage` accepts 500, so ~9,900 players cost 20 requests rather
    than the 497 the UI's default page size would imply. Cached for a week.
    """
    url = FANTRAX_API.format(league_id=cfg["league_id"])
    ttl = float(cfg.get("cache_hours", 168))
    per_page = int(cfg.get("page_size", 500))
    rows, seen = [], set()
    for page in range(1, int(cfg.get("max_pages", 30)) + 1):
        payload = {"msgs": [{"method": "getPlayerStats", "data": {
            "statusOrTeamFilter": "ALL",
            "pageNumber": str(page),
            "maxResultsPerPage": str(per_page),
        }}]}
        data = fetch.post_json(url, payload,
                               slug=f"fantrax-{cfg['league_id'][:8]}-{page:02d}",
                               max_age_hours=ttl)
        try:
            body = data["responses"][0]["data"]
        except (KeyError, IndexError):
            break
        table = body.get("statsTable", [])
        if not table:
            break
        # Asking for a page past the end does NOT return empty -- Fantrax
        # re-serves the last page, so an empty-page loop silently collects
        # thousands of duplicates. Stop where the API says the data stops.
        total_pages = int(body.get("paginatedResultSet", {})
                              .get("totalNumPages", 0) or 0)
        for entry in table:
            s = entry.get("scorer")
            if not s or s.get("team"):      # skip team defence/pitching entries
                continue
            sid = s.get("scorerId")
            if sid in seen:
                continue
            seen.add(sid)
            rows.append({
                "name": (s.get("name") or "").strip(),
                "team": s.get("teamShortName"),
                "pos": s.get("posShortNames"),
                # Fantrax's own stable id. It matches Kyle's own league export,
                # since the ids are global across Fantrax leagues -- but no
                # NON-Fantrax source carries it, so it is not a universal key.
                "scorer_id": sid,
            })
        if total_pages and page >= total_pages:
            break
    print(f"[fetch] fantrax: {len(rows)} players")
    return rows


# HKB spells its minor-league levels differently from the way they are said
# out loud, which is how Kyle asked for them. Same five rungs, so this is a
# relabel, not a filter. "MLB" is deliberately absent: a player in the majors
# is not a prospect no matter what HKB's own `prospect` flag says.
_HKB_LEVELS = {"LOW_A": "A", "HIGH_A": "A+", "AA": "AA", "AAA": "AAA",
               "ROOKIE_BALL": "ROK", "MLB": "MLB"}


def _harryknowsball(cfg: dict, fetch: Fetcher) -> list[dict]:
    """Rankings come from the Next.js payload, not the rendered page.

    The visible table animates its numbers with per-digit counters, so its text
    is unreadable. `__NEXT_DATA__` carries all ~1,750 ranked players cleanly.
    """
    html = fetch(cfg["url"])
    tag = BeautifulSoup(html, "lxml").find("script", id="__NEXT_DATA__")
    if tag is None:
        print("[warn] harryknowsball: no __NEXT_DATA__ block found")
        return []
    players = json.loads(tag.string).get("props", {}) \
                                    .get("pageProps", {}).get("players", [])
    rows = []
    for p in players:
        rank = to_num(p.get("rank"))
        if rank is None:
            continue
        # HKB ranks tradeable picks ("2027 early 1st") among the players
        if not is_player(p.get("name")):
            continue
        rows.append({
            "name": (p.get("name") or "").strip(),
            "team": p.get("team"),
            # Carried purely to break ties the team can't: both Jared Joneses
            # are on PIT (SP vs 1B), and Edwin Diaz's team here (LAD, post
            # trade) matches neither of the authority's two entries.
            "pos": ",".join(p.get("positions") or []),
            "rank": int(rank),
            # Feeds prospect_rank. Passed through unmapped when HKB invents a
            # new rung, so it shows up in the board rather than silently
            # dropping the player out of the prospect list.
            "level": _HKB_LEVELS.get(str(p.get("level")), p.get("level") or ""),
        })
    return rows


# Post date per extra-rank source, filled during the fetch and read by
# rankings.py for the status file. A module-level dict because the source
# interface returns rows, and widening it for one sport's metadata would
# change all three.
_POST_DATES: dict = {}


def _recent_posts(feed_url: str, fetch: Fetcher, limit: int = 5):
    """Recent (url, date) pairs from a WordPress category feed, newest first.

    Parse <item> blocks rather than every <link> -- the channel's own <link>
    comes first in an RSS document and is the category page, not a post.

    Several, not one. The category carries whatever the site files under it,
    and the weekly ranking is not guaranteed to be the newest thing in it: on
    2026-09-01 PitcherList published a one-off playoff schedule guide, which
    took the top slot and has no rank column at all. Taking item 1 blindly
    meant sp_rank silently emptied while the feed was working perfectly.
    """
    xml = fetch(feed_url, max_age_hours=6)
    out = []
    for item in re.findall(r"<item>(.*?)</item>", xml, re.S)[:limit]:
        link = re.search(r"<link>\s*(https?://[^<\s]+)", item)
        pub = re.search(r"<pubDate>([^<]+)</pubDate>", item)
        if link:
            out.append((link.group(1), pub.group(1)[:16] if pub else None))
    return out


def _pitcherlist(feed_url: str, fetch: Fetcher, label: str) -> list[dict]:
    """This week's PitcherList ranking grid.

    Both post types end with the grid we want, but the reliever post is a trap:
    it contains THREE tables with identical headers -- closers (50), holds
    (100) and SOLDs (100) -- so matching on headers alone silently grabs the
    wrong list. Always take the LAST match, which is the SOLDs board.
    """
    posts = _recent_posts(feed_url, fetch)
    if not posts:
        print(f"[warn] {label}: no posts in feed {feed_url}")
        return []

    skipped = []
    for post, posted in posts:
        slug = post.rstrip("/").split("/")[-1][:60]
        html = fetch(post, max_age_hours=24)
        hits = [g for g in html_tables(html)
                if PL_HEADERS <= {h.strip() for h in g[0]}]
        if not hits:
            skipped.append(f"{posted} ({slug})")
            continue

        rows = []
        for r in grid_to_rows(hits[-1]):
            rank = to_num(r.get("Rank"))
            if rank is None:
                continue
            name = _TIER_SUFFIX.sub("", str(r.get("Pitcher", "")).strip()).strip()
            rows.append({"name": name, "team": str(r.get("Team", "")).strip(),
                         "rank": int(rank)})
        if not rows:
            skipped.append(f"{posted} ({slug})")
            continue

        print(f"[fetch] {label}: {posted} -- {slug}")
        for s in skipped:
            print(f"[note]  {label}: skipped {s} -- no ranking table in it")
        # The date is returned so the run can report how old this week's post
        # is. A feed that keeps serving a three-week-old ranking is the failure
        # that looks most like success.
        _POST_DATES[label] = posted
        return rows

    print(f"[warn] {label}: none of the {len(posts)} most recent posts had a "
          f"table with headers {sorted(PL_HEADERS)} -- checked "
          + ", ".join(skipped))
    _POST_DATES[label] = None
    return []


# ------------------------------------------------------------ interface ----


def rank_sources(cfg: dict, fetch: Fetcher) -> dict:
    return {"hkb": _harryknowsball(cfg["sources"]["hkb"], fetch)}


def extra_status() -> dict:
    """Publication date of the post each extra-rank column came from.

    Read by rankings.py into _source_status_<sport>.csv, so the Sheet says how
    old this week's PitcherList ranking is. These columns had no status line at
    all before 2026-09-04: no floor, no fallback, no row in the status file.
    sp_rank silently went to zero players and stayed there, and nothing said so
    -- the only trace was one [warn] line in a log nobody reads.
    """
    return dict(_POST_DATES)


def extra_ranks(cfg: dict, fetch: Fetcher) -> dict:
    """Reference columns, deliberately NOT part of the blended ordering.

    These are weekly in-season lists covering pitchers only. Blending them
    would treat every hitter as "missing" and penalise the entire batting half
    of the board. They ride along like current_rank instead.

    Each entry declares the months it applies to (May-Oct). Outside those the
    column is left null rather than carrying October's last post forward -- a
    stale ranking that still looks current is worse than a blank.
    """
    month = date.today().month
    out = {}
    for key, feed in (cfg.get("extra_ranks") or {}).items():
        months = feed.get("months")
        if months and month not in months:
            print(f"[skip] {key}: out of season (month {month}) -- column null")
            out[key] = []
            continue
        out[key] = _pitcherlist(feed["feed"], fetch, key)
    return out


# Fantrax's pool marks corner/middle infielders with a catch-all "INF" alongside
# the real position. It is never the answer on its own and only adds noise.
_DROP_POSITIONS = {"INF"}


def _clean_positions(value) -> str:
    kept = [p.strip() for p in str(value or "").split(",")
            if p.strip() and p.strip().upper() not in _DROP_POSITIONS]
    return ",".join(kept)


def name_authority(cfg: dict, fetch: Fetcher) -> list[dict]:
    """Fantrax's pool, with positions from Kyle's own roster sheet where it has them.

    The roster export is the better source for a player he actually owns: it is
    his league's own eligibility, not a generic pool's. It only covers rostered
    players, so the pool still supplies everyone else.

    Joined on Fantrax's scorer id, not on name -- the roster export wraps the
    same global id in asterisks (*05ucd*), so this is exact even for the three
    Jose Ramirezes.
    """
    rows = _fantrax(cfg["name_authority"], fetch)
    for r in rows:
        r["pos"] = _clean_positions(r.get("pos"))

    rcfg = cfg.get("rosters") or {}
    sheet_id, tab = rcfg.get("sheet_id"), rcfg.get("tab")
    if not (sheet_id and tab):
        return rows

    grid = sheet_grid(sheet_id, tab, fetch, float(rcfg.get("cache_hours", 0)))
    if len(grid) < 2:
        return rows
    header = [c.strip() for c in grid[0]]
    id_col, pos_col = rcfg.get("id_col", "ID"), "Position"
    if id_col not in header or pos_col not in header:
        print(f"[warn] roster tab has no {id_col}/{pos_col} column -- "
              f"keeping the pool's positions")
        return rows

    i_id, i_pos = header.index(id_col), header.index(pos_col)
    by_id = {}
    for row in grid[1:]:
        if len(row) <= max(i_id, i_pos):
            continue
        sid = str(row[i_id]).strip().strip("*")
        pos = _clean_positions(row[i_pos])
        if sid and pos:
            by_id[sid] = pos

    applied = 0
    for r in rows:
        override = by_id.get(str(r.get("scorer_id") or ""))
        if override and override != r["pos"]:
            r["pos"] = override
            applied += 1
    print(f"[pos]   roster positions applied to {applied} of {len(by_id)} "
          f"rostered players")
    return rows


def current_rank(cfg: dict, fetch: Fetcher) -> list[dict]:
    return fantasypros(pick_seasonal_url(cfg["current"]), fetch)


def inspect(cfg: dict, fetch: Fetcher) -> None:
    hkb = _harryknowsball(cfg["sources"]["hkb"], fetch)
    print(f"harryknowsball: {len(hkb)} ranked players")
    for r in hkb[:5]:
        print("   ", r)
    fx = _fantrax(cfg["name_authority"], fetch)
    print(f"\nfantrax: {len(fx)} players")
    for r in fx[:5]:
        print("   ", r)
