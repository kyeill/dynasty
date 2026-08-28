"""
Shared machinery for every sport.

Nothing here knows about basketball, football or baseball. The sport-specific
parsers live in sources_<sport>.py and call into this.

NO PANDAS, NO NUMPY. Windows Smart App Control blocks numpy's ARM64 binaries
on this machine -- they are unsigned and too rarely downloaded to have built up
a reputation -- which took the whole project down on 2026-08-22. Everything
here runs on the standard library plus requests, lxml and openpyxl, none of
which SAC objects to. Do not reintroduce pandas.

Tables are plain `list[dict]`. Indexed tables are `dict[key, row]`.
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
import time
import unicodedata
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

HERE = Path(__file__).resolve().parent
CACHE_DIR = HERE / ".cache"
IMPORTS_DIR = HERE / "imports"
OUTPUT_DIR = HERE / "output"
HISTORY_DIR = OUTPUT_DIR / "history"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


# ------------------------------------------------------------- tiny table ----


def to_num(value):
    """Text or number -> float, or None. The stand-in for pd.to_numeric."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return None if value != value else float(value)   # NaN check
    s = str(value).strip().replace(",", "")
    if not s or s.lower() in ("nan", "none", "-", "--"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def fmt_num(value):
    """Blank for None, otherwise the value's own repr.

    Deliberately does NOT strip ".0" from floats. Ranks are stored as int and
    scores as float, and collapsing 1.0 to 1 would erase that distinction --
    a score column would start looking like a rank column.
    """
    return "" if value is None else str(value)


def read_csv(path, comment: str | None = None) -> list[dict]:
    """CSV -> list of dicts. utf-8-sig so a BOM never becomes part of a header."""
    path = Path(path)
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        lines = fh.readlines()
    if comment:
        lines = [ln for ln in lines if not ln.lstrip().startswith(comment)]
    return list(csv.DictReader(lines))


def write_csv(path, rows: list[dict], fieldnames: list[str] | None = None) -> None:
    """list of dicts -> CSV, UTF-8 with BOM so Excel keeps its accents."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = list(rows[0].keys()) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: fmt_num(r.get(k)) if isinstance(r.get(k), float)
                        else ("" if r.get(k) is None else r.get(k))
                        for k in fieldnames})


# ----------------------------------------------------------------- fetch ----


class Fetcher:
    """HTTP with a cache that can be given a per-source lifetime.

    `max_age_hours` matters for player-list pages: they cost many paginated
    requests but only yield names and positions, which barely change. Refresh
    those weekly and the ranking pages every run.
    """

    def __init__(self, use_cache: bool = False, delay: float = 2):
        self.use_cache = use_cache
        self.delay = delay
        CACHE_DIR.mkdir(exist_ok=True)

    def _fresh(self, cached: Path, max_age_hours: float) -> bool:
        if not cached.exists():
            return False
        if self.use_cache:
            return True
        return (max_age_hours > 0
                and (time.time() - cached.stat().st_mtime) / 3600 < max_age_hours)

    def __call__(self, url: str, slug: str | None = None,
                 max_age_hours: float = 0) -> str:
        if slug is None:
            slug = re.sub(r"[^a-z0-9]+", "-", url.lower().split("://", 1)[-1]).strip("-")
        cached = CACHE_DIR / f"{slug[:120]}.html"
        if self._fresh(cached, max_age_hours):
            return cached.read_text(encoding="utf-8", errors="replace")

        print(f"[fetch] GET {url[:110]}")
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=45)
        resp.raise_for_status()
        cached.write_text(resp.text, encoding="utf-8")
        time.sleep(self.delay)  # be a polite guest on someone else's server
        return resp.text

    def post_json(self, url: str, payload: dict, slug: str,
                  max_age_hours: float = 0) -> dict:
        """Same caching, for sources that only answer a JSON POST (Fantrax)."""
        cached = CACHE_DIR / f"{slug}.json"
        if self._fresh(cached, max_age_hours):
            return json.loads(cached.read_text(encoding="utf-8"))

        print(f"[fetch] POST {url[:110]}")
        resp = requests.post(url, json=payload, timeout=45,
                             headers={"User-Agent": UA,
                                      "Content-Type": "application/json"})
        resp.raise_for_status()
        cached.write_text(resp.text, encoding="utf-8")
        time.sleep(self.delay)
        return resp.json()


def page_text(html: str) -> str:
    """Visible text of a page, tags collapsed to single spaces.

    Always work from this or the raw HTML -- never a browser's innerText, which
    reflects CSS text-transform and will lie to you about capitalisation.
    """
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return re.sub(r"[ \t\xa0]+", " ", soup.get_text(" "))


def html_tables(html: str) -> list[list[list[str]]]:
    """Every <table> as a grid of cell text. The stand-in for pd.read_html."""
    from lxml import html as lhtml
    try:
        doc = lhtml.fromstring(html)
    except Exception:  # noqa: BLE001 - malformed markup is not our problem
        return []
    grids = []
    for table in doc.xpath("//table"):
        grid = []
        for tr in table.xpath(".//tr"):
            cells = tr.xpath("./td | ./th")
            if cells:
                grid.append([c.text_content().strip() for c in cells])
        if grid:
            grids.append(grid)
    return grids


def grid_to_rows(grid: list[list[str]]) -> list[dict]:
    """First row is the header. Blank/duplicate headers get positional names."""
    if not grid:
        return []
    header, seen = [], {}
    for i, h in enumerate(grid[0]):
        h = h.strip() or f"col{i}"
        if h in seen:
            seen[h] += 1
            h = f"{h}.{seen[h]}"
        else:
            seen[h] = 0
        header.append(h)
    return [dict(zip(header, r + [""] * (len(header) - len(r))))
            for r in grid[1:]]


# ------------------------------------------------------------ name keys ----

SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def normalize_name(raw) -> str:
    """Fold a display name to a stable join key.

    Only ever used for matching -- never for output. Display names come from
    the naming authority verbatim, diacritics and all.
    """
    s = unicodedata.normalize("NFKD", str(raw))
    s = "".join(c for c in s if not unicodedata.combining(c))  # Jokić -> Jokic
    s = s.lower().replace("\xa0", " ")
    s = re.sub(r"[.'’`]", "", s)            # P.J. -> pj, O'Neale -> oneale
    s = re.sub(r"[^a-z0-9]+", " ", s)            # hyphens -> space
    parts = [p for p in s.split() if p]
    while len(parts) > 2 and parts[-1] in SUFFIXES:
        parts.pop()
    return " ".join(parts)


def initial_key(key: str) -> str | None:
    """'giannis antetokounmpo' -> 'g antetokounmpo'."""
    parts = key.split()
    if len(parts) < 2:
        return None
    return parts[0][0] + " " + " ".join(parts[1:])


# Dynasty lists rank tradeable draft picks alongside people. They will never
# be on a roster, so they must not be treated as unmatched players.
#   Hashtag keeper : "2026 Draft Pick 1", "2026 Draft Picks 3-4"
#   HarryKnowsBall : "2027 early 1st", "2028 mid 1st"
_NOT_A_PLAYER = re.compile(
    r"\bdraft\s+picks?\b"
    r"|\bpick\s+\d"
    r"|^\s*(?:19|20)\d{2}\s+(?:early|mid|late|comp)?\s*\d*(?:st|nd|rd|th)\b",
    re.I,
)


def is_player(name) -> bool:
    return not _NOT_A_PLAYER.search(str(name))


# Yahoo shortens the first name when the full one is too long for its column:
# "G. Antetokounmpo", "M. Valdes-Scantling". One initial, a dot, then a space.
# Real names survive: "P.J. Washington" has a second initial where the space
# would be, so it does not match.
ABBREVIATED = re.compile(r"^[A-Z]\.\s")


def expand_abbreviated(name, uid, sources) -> str:
    """Recover a full first name from a ranking source, which never abbreviates.

    Data-driven rather than rule-based: ask the other sources what they call
    this player instead of guessing which initials are shorthand.
    "J. Michael Gyllenborg" becomes "John Michael Gyllenborg" because
    FantasyPros has the long form, while "J. Michael Sturdivant" stays put
    because every source agrees that IS his name.
    """
    if not isinstance(name, str) or not ABBREVIATED.match(name):
        return name
    best = name
    for table in sources:
        row = (table or {}).get(uid)
        if not row:
            continue
        cand = row.get("name")
        if (isinstance(cand, str) and not ABBREVIATED.match(cand)
                and len(cand) > len(best)):
            best = cand
    return best


def save_fullnames(mapping: dict, sport: str) -> None:
    """Share expansions with rosters.py, which has no ranking sources of its own."""
    if not mapping:
        return
    CACHE_DIR.mkdir(exist_ok=True)
    (CACHE_DIR / f"fullnames-{sport}.json").write_text(
        json.dumps(mapping, indent=1, ensure_ascii=False), encoding="utf-8")


def load_fullnames(sport: str) -> dict:
    path = CACHE_DIR / f"fullnames-{sport}.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def load_aliases(path: Path) -> dict:
    """aliases-<sport>.csv: any_spelling,authority_spelling (both normalized)."""
    path = Path(path)
    if not path.exists():
        path.write_text(
            "# Manual overrides for names that do not match automatically.\n"
            "# Only needed when the FIRST INITIAL differs between two sites --\n"
            "# accents, suffixes, punctuation, abbreviated first names and\n"
            "# space-less names are all handled automatically.\n"
            "# Format: source_spelling,authority_spelling\n",
            encoding="utf-8")
        return {}
    mapping = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "," not in line:
            continue
        alias, canonical = line.split(",", 1)
        mapping[normalize_name(alias)] = normalize_name(canonical)
    return mapping


def _tokens(val) -> set:
    return {p.strip().upper() for p in re.split(r"[/,]", str(val or "")) if p.strip()}


def prepare_authority(rows: list[dict], aliases: dict) -> dict:
    """Index the naming authority, giving genuinely distinct players distinct ids.

    Baseball breaks the assumption that a name identifies a player. Fantrax's
    MLB pool has 152 duplicated names in 9,926 -- three Jose Ramirezes (CLE 3B,
    DET OF, DET RP). Collapsing those would quietly bind a star's ranking to a
    middle reliever.

    So the index is a uid: the plain name key when unique, else name|team, else
    name|team|pos. Two players still identical after that are genuinely
    indistinguishable from the data we have.
    """
    keyed = []
    counts = {}
    for r in rows:
        k = normalize_name(r.get("name"))
        k = aliases.get(k, k)
        keyed.append((k, r))
        counts[k] = counts.get(k, 0) + 1

    out, dup_uid = {}, {}
    for k, r in keyed:
        uid = k if counts[k] == 1 else f"{k}|{str(r.get('team') or '?').lower()}"
        dup_uid[uid] = dup_uid.get(uid, 0) + 1
    for k, r in keyed:
        uid = k if counts[k] == 1 else f"{k}|{str(r.get('team') or '?').lower()}"
        if dup_uid[uid] > 1:
            uid = f"{uid}|{str(r.get('pos') or '?').lower()}"
        if uid in out:
            continue                      # first wins, as before
        row = dict(r)
        row["key"] = k
        out[uid] = row
    return out


def build_resolver(auth: dict):
    """Map a source's (key, team, pos) onto the authority's uid for that player.

    Passes, each firing only when the one before missed:

      1. exact key
      2. exact key + team   -- only when the name alone is ambiguous
      3. exact key + pos    -- when team cannot settle it either
      4. first-initial      -- Yahoo shortens the first name when two players
                               share a surname ('G. Antetokounmpo') and uses
                               nicknames ('Alex Sarr', 'Nic Claxton')
      5. space-less         -- exported CSVs lose the space ('JoshAllen')

    A pass only counts when it lands on exactly one player. OKC rosters both
    Jalen and Jaylin Williams; guessing between them is worse than reporting
    the miss, and the same goes for the Jose Ramirezes.
    """
    by_key: dict[str, list[str]] = {}
    for uid, row in auth.items():
        by_key.setdefault(row["key"], []).append(uid)

    unique = {k: v[0] for k, v in by_key.items() if len(v) == 1}
    by_initial: dict[str, list[str]] = {}
    by_squashed: dict[str, list[str]] = {}
    for k, uid in unique.items():
        ik = initial_key(k)
        if ik:
            by_initial.setdefault(ik, []).append(uid)
        by_squashed.setdefault(k.replace(" ", ""), []).append(uid)

    def resolve(key: str, team=None, pos=None) -> str | None:
        cands = by_key.get(key)
        if cands:
            if len(cands) == 1:
                return cands[0]
            # Team first. A traded player's team can be slash-joined on the
            # authority side -- Fantrax writes "ATH/SD" for Mason Miller while
            # every other source says "SD" -- so compare token sets.
            if team:
                want = _tokens(team)
                hit = [u for u in cands if want & _tokens(auth[u].get("team"))]
                if len(hit) == 1:
                    return hit[0]
                if len(hit) > 1:
                    cands = hit
            # Position, when team cannot settle it. Both Jared Joneses are on
            # PIT (SP vs 1B), and Edwin Diaz was traded to LAD while the
            # authority still has him on NYM, so his team matches neither.
            if pos:
                want = _tokens(pos)
                hit = [u for u in cands if want & _tokens(auth[u].get("pos"))]
                if len(hit) == 1:
                    return hit[0]
            return None  # ambiguous -- report it rather than guess
        ik = initial_key(key)
        if ik and len(by_initial.get(ik, [])) == 1:
            return by_initial[ik][0]
        if " " not in key and len(by_squashed.get(key, [])) == 1:
            return by_squashed[key][0]
        return None

    return resolve


def index_by_key(rows: list[dict], aliases: dict, resolve=None) -> dict:
    """Index a source table by the authority's uid.

    Passes the source's own team and position through when it has them, so an
    ambiguous name can still be resolved -- that is what saves the three Jose
    Ramirezes.
    """
    out = {}
    for r in rows:
        k = normalize_name(r.get("name"))
        k = aliases.get(k, k)
        uid = k
        on_auth = True
        if resolve is not None:
            hit = resolve(k, r.get("team"), r.get("pos"))
            on_auth = hit is not None
            uid = hit if hit is not None else k
        if uid in out:
            continue                      # first wins
        row = dict(r)
        row["key"] = k
        row["on_authority"] = on_auth
        out[uid] = row
    return out


# ----------------------------------------------------------------- blend ----


def blend(frames: dict, weights: dict, policy: str) -> list[dict]:
    """Weighted blend of ranking sources. Lower is better.

    blended_score = sum(weight * rank) / sum(weight), sorted ascending.
    """
    all_keys = set()
    for f in frames.values():
        all_keys |= set(f)
    lasts = {n: max((to_num(r.get("rank")) or 0) for r in f.values()) if f else 0
             for n, f in frames.items()}

    rows = []
    for key in sorted(all_keys):
        ranks, used, matched = {}, {}, []
        for name, frame in frames.items():
            if key in frame:
                ranks[name] = to_num(frame[key].get("rank"))
                used[name] = weights[name]
                matched.append(name)
            elif policy == "penalty":
                ranks[name] = float(lasts[name]) + 1
                used[name] = weights[name]

        if policy == "drop" and len(matched) < len(frames):
            continue
        if not matched:
            continue

        total_w = sum(used.values()) or 1.0
        row = {
            "key": key,
            "blended_score": round(
                sum(used[n] * ranks[n] for n in used) / total_w, 3),
            # Counts sources that actually LISTED the player. Under "penalty"
            # every source contributes a number, so this must not be derived
            # from the scoring inputs or it would be constant.
            "sources_matched": len(matched),
        }
        for name in frames:
            row[f"{name}_rank"] = int(ranks[name]) if name in matched else None
        rows.append(row)

    # Ties break toward the player confirmed by more sources.
    rows.sort(key=lambda r: (r["blended_score"], -r["sources_matched"]))
    return rows


# ----------------------------------------------------------------- value ----


def value_scale(replacement: int, top20_ratio: float = 2.1, anchor: int = 20):
    """Build a rank -> value function. #1 is 100, replacement level is 0.

        value(r) = 100 * (r^-a - R^-a) / (1 - R^-a)

    `R` is the replacement rank -- the last rostered player in the league, so
    beyond it a player is freely available and worth nothing. That zero point
    is what makes the numbers additive in any meaningful sense: two players
    worth 20 really do represent 40 of surplus over what you could have for
    free. Without it, stacking enough bench players would "equal" a star.

    `a` controls top-heaviness and is solved from `top20_ratio` -- how many
    top-20 players the #1 player is worth -- rather than being set directly,
    so the shape stays honest if roster sizes change. Solving per sport also
    makes the sports agree at the anchor despite very different depths.
    """
    R = int(replacement)

    def curve(r, a):
        r = to_num(r)
        if r is None or r >= R:
            return 0.0
        r = max(r, 1.0)
        # Below this, R**-a rounds to exactly 1.0 and the term collapses to
        # 0/0. The a->0 limit is the logarithmic curve, so use it directly.
        if a < 1e-6:
            return 100.0 * math.log(R / r) / math.log(R)
        w = R ** -a
        return 100.0 * (r ** -a - w) / (1 - w)

    flattest = curve(1, 0.0) / curve(anchor, 0.0)
    # Tolerance, because NFL's flattest is 2.0999... against a 2.1 target and
    # warning about that every run is noise, not information.
    if top20_ratio < flattest - 0.01:
        print(f"[warn] value: #1 = {top20_ratio}x #{anchor} is flatter than "
              f"this curve allows (min {flattest:.2f}x) -- using the flattest")
        a = 0.0
    else:
        lo, hi = 0.0, 2.0
        for _ in range(200):
            mid = (lo + hi) / 2
            if curve(1, mid) / curve(anchor, mid) < top20_ratio:
                lo = mid
            else:
                hi = mid
        a = (lo + hi) / 2

    return lambda r: round(curve(r, a), 1)


# ------------------------------------------------------- shared sources ----


# "SAS - C", "Det - RB". The spaces around the hyphen are load-bearing: Yahoo
# puts an injury badge in an EARLIER span -- "PUP-P" for the physically-unable-
# to-perform list -- which without them parses as team "PUP", position "P" and
# beat the real "Ind - WR" to it. Six NFL players were mis-positioned that way.
_YAHOO_POS_RE = re.compile(r"^([A-Za-z]{2,3})\s+-\s+(.+)$")


def yahoo_rows(html: str) -> list[dict]:
    """(name, team, position) from one Yahoo fantasy player-list page.

    Yahoo renders the name in <a class="name"> and "TEAM - POS" in a small
    sibling span, both inside the same <td>. The team abbreviation is
    upper-case on the NBA site ("SAS - C") but title-case on the NFL one
    ("Det - RB"), so match either and normalize up.
    """
    soup = BeautifulSoup(html, "lxml")
    rows = []
    for a in soup.select("a.name"):
        name = a.get_text(strip=True)
        if not name:
            continue
        cell = a.find_parent("td") or a.parent
        team = pos = None
        for span in cell.find_all("span"):
            m = _YAHOO_POS_RE.match(span.get_text(" ", strip=True))
            if m:
                team, pos = m.group(1).upper(), re.sub(r"\s+", "", m.group(2))
                break
        rows.append({"name": name, "team": team, "pos": pos})
    return rows


def yahoo_player_list(cfg: dict, fetch: Fetcher) -> list[dict]:
    """Walk a Yahoo player list until a page comes back empty.

    `count` is a row OFFSET, not a page size -- it cannot be widened, so a full
    list costs one request per 25 players. Hence the long cache lifetime.
    """
    tmpl = cfg["url_template"]
    ttl = float(cfg.get("cache_hours", 168))
    step = int(cfg.get("page_size", 25))
    rows, seen, offset, pages = [], set(), 0, 0
    for _ in range(int(cfg.get("max_pages", 40))):
        html = fetch(tmpl.format(offset=offset),
                     slug=f"{cfg['slug']}-{offset:04d}", max_age_hours=ttl)
        page = yahoo_rows(html)
        if not page:
            break
        for r in page:
            if r["name"] not in seen:
                seen.add(r["name"])
                rows.append(r)
        offset += step
        pages += 1
    print(f"[fetch] {cfg['slug']}: {pages} pages")
    return rows


def pick_seasonal_url(cfg: dict, today=None) -> str:
    """Choose a URL based on the month.

    Every sport wants preseason rankings out of season and rest-of-season
    rankings during it, and the changeover month differs per sport. Windows are
    listed explicitly rather than computed, because they wrap the year end
    (Nov-Apr) and explicit month lists are easier to check than date
    arithmetic.
    """
    if "url" in cfg and "windows" not in cfg:
        return cfg["url"]
    month = (today or date.today()).month
    for window in cfg.get("windows", []):
        if month in window.get("months", []):
            return window["url"]
    fallback = cfg.get("windows", [{}])[0].get("url", cfg.get("url", ""))
    print(f"[warn] no seasonal window covers month {month} -- using {fallback}")
    return fallback


def fantasypros(url: str, fetch: Fetcher) -> list[dict]:
    """FantasyPros ships the full ranking inline as `var ecrData = {...}`.

    Go after the JSON, not the rendered table: the table abbreviates names
    ("J. Williams" is ambiguous -- OKC has two). Identical structure across
    NBA, NFL and MLB.
    """
    html = fetch(url)
    i = html.find("var ecrData")
    if i == -1:
        return []
    start = html.find("{", i)
    if start == -1:
        return []
    try:
        obj, _ = json.JSONDecoder().raw_decode(html, start)
    except ValueError:
        return []

    rows = []
    for p in obj.get("players", []):
        rank = to_num(p.get("rank_ecr"))
        if rank is None:
            continue
        rows.append({"name": (p.get("player_name") or "").strip(),
                     "team": p.get("player_team_id"),
                     "current_rank": int(rank)})
    return rows


# --------------------------------------------------------------- imports ----


# A Google Sheet shared "anyone with the link can view" exposes each tab as CSV
# here, with no credentials at all. That is what lets rosters live in a sheet
# Kyle edits from anywhere while GitHub Actions reads them unauthenticated.
SHEET_CSV = ("https://docs.google.com/spreadsheets/d/{id}"
             "/gviz/tq?tqx=out:csv&sheet={tab}")


def sheet_grid(sheet_id: str, tab: str, fetch, max_age_hours: float = 0,
               expect: set | None = None) -> list[list[str]]:
    """One tab of a link-viewable Google Sheet, as a grid of cell text.

    `expect` is a set of column names the header must contain. Pass it whenever
    you can: a tab name that does not exist does NOT error -- Google silently
    returns the FIRST tab instead. Without this check a typo in the config
    would quietly feed the wrong sheet's data into the pipeline.
    """
    from urllib.parse import quote
    text = fetch(SHEET_CSV.format(id=sheet_id, tab=quote(tab)),
                 slug=f"sheet-{sheet_id[:10]}-{tab.lower()}",
                 max_age_hours=max_age_hours)
    if "docs.google.com/accounts" in text[:400] or text.lstrip().startswith("<"):
        print(f"[warn] sheet tab {tab!r} did not return CSV -- is the sheet "
              f"shared 'anyone with the link can view'?")
        return []
    grid = [row for row in csv.reader(io.StringIO(text))]

    if expect:
        if not grid:
            print(f"[warn] sheet tab {tab!r} is empty -- expected a header row "
                  f"with {sorted(expect)}")
            return []
        header = {str(c).strip().lower() for c in grid[0]}
        missing = {c.lower() for c in expect} - header
        if missing:
            print(f"[warn] sheet tab {tab!r} is missing column(s) "
                  f"{sorted(missing)} -- got {sorted(header)}. A tab name that "
                  f"does not exist silently returns the FIRST tab, so check the "
                  f"name as well as the columns.")
            return []
    return grid


# Last-known-good copies of every source, so one going dark does not take a
# board with it. Tracked in git, NOT in .cache: GitHub Actions evicts caches
# after 7 days of no access, and a cache miss would lose the fallback exactly
# when it is needed.
LASTGOOD_DIR = HERE / "lastgood"


def guard_source(rows: list[dict], sport: str, source: str,
                 floor: int) -> tuple[list[dict], dict]:
    """Bank a healthy pull; fall back to the last good one when a source dies.

    Hashtag's keeper page went from 760 rows to zero on 2026-08-28 -- returning
    HTTP 200 the whole time, just with the table gone while they reloaded their
    voting system. That took the entire NBA board down for days.

    Returns (rows, status). The status is written to output/_source_status.csv
    so a board built on stale data says so somewhere the Sheets can see, rather
    than only in a log nobody reads.

    Deliberately NOT silent: a stale source blended into a fresh board is the
    quiet-wrong-answer case, so every fallback is reported with its age.
    """
    path = LASTGOOD_DIR / f"{sport}_{source}.csv"
    healthy = bool(rows) and len(rows) >= floor

    if healthy:
        keys = []
        for r in rows:
            for k in r:
                if k not in keys:
                    keys.append(k)
        write_csv(path, rows, keys)
        return rows, {"source": source, "rows": len(rows), "stale": "",
                      "age_days": "", "note": ""}

    prev = read_csv(path) if path.exists() else []
    if not prev:
        print(f"[warn] {source}: {len(rows)} rows (floor {floor}) and no "
              f"last-good copy to fall back on")
        return rows, {"source": source, "rows": len(rows), "stale": "",
                      "age_days": "", "note": "below floor, no fallback"}

    age = (time.time() - path.stat().st_mtime) / 86400
    print(f"[STALE] {source}: got {len(rows)} rows (floor {floor}) -- using the "
          f"last good copy, {len(prev)} rows from {age:.1f} days ago")
    if age > 14:
        print(f"[warn] {source}: that fallback is {age:.0f} days old. If the "
              f"source is not coming back, replace it rather than coasting.")
    return prev, {"source": source, "rows": len(prev), "stale": "YES",
                  "age_days": round(age, 1), "note": f"live pull had {len(rows)}"}


def newest_import(sport: str, pattern: str) -> Path | None:
    """Most recent manually-exported CSV matching a pattern."""
    folder = IMPORTS_DIR / sport
    if not folder.exists():
        return None
    hits = [p for p in folder.glob("*.csv") if re.search(pattern, p.name, re.I)]
    return max(hits, key=lambda p: p.stat().st_mtime) if hits else None


def read_import(sport: str, pattern: str, label: str,
                stale_days: float = 45) -> list[dict]:
    """Read the newest matching export, and say how old it is."""
    path = newest_import(sport, pattern)
    if path is None:
        print(f"[warn] no {label} export found in imports/{sport}/ "
              f"(looking for /{pattern}/)")
        return []
    age_d = (time.time() - path.stat().st_mtime) / 86400
    note = f"  <-- {age_d:.0f} days old, consider re-exporting" if age_d > stale_days else ""
    print(f"[import] {label}: {path.name} ({age_d:.0f}d){note}")
    return read_csv(path)


def snapshot(rows: list[dict], sport: str, kind: str,
             fieldnames: list[str] | None = None) -> Path | None:
    """Archive today's output so trends can be computed later.

    Movement -- who is rising and falling -- cannot be reconstructed after the
    fact, because every run overwrites the live file. One file per day; a
    re-run overwrites that day rather than accumulating duplicates.
    """
    if not rows:
        return None
    folder = HISTORY_DIR / kind / sport
    path = folder / f"{date.today().isoformat()}.csv"
    write_csv(path, rows, fieldnames)
    return path
