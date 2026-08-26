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
                    pick_seasonal_url, to_num)

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
        })
    return rows


def _latest_post(feed_url: str, fetch: Fetcher):
    """Newest post URL and date from a WordPress category feed.

    Parse <item> blocks rather than every <link> -- the channel's own <link>
    comes first in an RSS document and is the category page, not a post.
    """
    xml = fetch(feed_url, max_age_hours=6)
    items = re.findall(r"<item>(.*?)</item>", xml, re.S)
    if not items:
        return None, None
    link = re.search(r"<link>\s*(https?://[^<\s]+)", items[0])
    pub = re.search(r"<pubDate>([^<]+)</pubDate>", items[0])
    return (link.group(1) if link else None, pub.group(1)[:16] if pub else None)


def _pitcherlist(feed_url: str, fetch: Fetcher, label: str) -> list[dict]:
    """This week's PitcherList ranking grid.

    Both post types end with the grid we want, but the reliever post is a trap:
    it contains THREE tables with identical headers -- closers (50), holds
    (100) and SOLDs (100) -- so matching on headers alone silently grabs the
    wrong list. Always take the LAST match, which is the SOLDs board.
    """
    post, posted = _latest_post(feed_url, fetch)
    if not post:
        print(f"[warn] {label}: no posts in feed {feed_url}")
        return []
    print(f"[fetch] {label}: {posted} -- {post.rstrip('/').split('/')[-1][:60]}")

    html = fetch(post, max_age_hours=24)
    hits = [g for g in html_tables(html)
            if PL_HEADERS <= {h.strip() for h in g[0]}]
    if not hits:
        print(f"[warn] {label}: no table with headers {sorted(PL_HEADERS)}")
        return []

    rows = []
    for r in grid_to_rows(hits[-1]):
        rank = to_num(r.get("Rank"))
        if rank is None:
            continue
        name = _TIER_SUFFIX.sub("", str(r.get("Pitcher", "")).strip()).strip()
        rows.append({"name": name, "team": str(r.get("Team", "")).strip(),
                     "rank": int(rank)})
    return rows


# ------------------------------------------------------------ interface ----


def rank_sources(cfg: dict, fetch: Fetcher) -> dict:
    return {"hkb": _harryknowsball(cfg["sources"]["hkb"], fetch)}


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


def name_authority(cfg: dict, fetch: Fetcher) -> list[dict]:
    return _fantrax(cfg["name_authority"], fetch)


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
