# dynasty

Blends dynasty ranking sources into one board per sport, keyed to the exact
player names used by a chosen **naming authority**, with position and a
"current" consensus rank appended — replacing the copy-paste-into-Sheets step.

```bash
python rankings.py --sport nba
python rankings.py --sport all --cache
python rankings.py --sport nfl --weights fp_dynasty=0.5,ktc=0.5
python rankings.py --sport nba --missing drop
python rankings.py --sport nba --cache --inspect
```

Outputs land in `output/` as `combined_rankings_<sport>.csv` and
`unmatched_<sport>.csv`.

## No pandas, deliberately

On 2026-08-22 Windows **Smart App Control** started blocking numpy's ARM64
binaries, and since pandas imports numpy at load time, every script in the
project stopped running. Nothing local had changed — SAC allows unsigned code
that has earned cloud *reputation*, and numpy-on-Windows-ARM64 is niche enough
never to have had any. (`lxml` is unsigned too and was fine.)

So the project was rebuilt on the standard library plus `requests`, `lxml` and
`openpyxl`, none of which SAC objects to:

| Was | Now |
|---|---|
| `pd.read_csv` / `to_csv` | `common.read_csv` / `write_csv` (stdlib `csv`) |
| `pd.read_html` | `common.html_tables` (lxml) |
| `pd.read_excel` | `openpyxl` |
| `DataFrame` | `list[dict]`; indexed tables are `dict[key, row]` |

The rebuild was verified against the pandas-era baselines: identical boards for
all three sports, the only differences being the intended first-name
expansions. **Don't reintroduce pandas** — it would put the project back at the
mercy of a reputation score nobody controls.

## Why one folder for three sports

The sources differ wildly; the *pipeline* is identical. Fetching, name
matching, blending, the degraded-source guards and the output writing are all
sport-agnostic, and the name matching in particular keeps needing fixes — five
so far, every one of them applying to all three sports. Three copies would
drift apart silently.

| File | What's in it |
|---|---|
| `rankings.py` | Entry point, per-sport dispatch, guards, output |
| `common.py` | Fetch/cache, name keys + resolver, blending, Yahoo + FantasyPros readers |
| `sources_<sport>.py` | Everything specific to one sport |
| `config/<sport>.json` | URLs, weights, cache lifetimes, sanity floors |
| `imports/<sport>/` | Manually-exported CSVs (nothing uses this now) |
| `output/` | The boards |

Each `sources_<sport>.py` provides three functions:

```python
rank_sources()   -> {name: DataFrame}   the lists that get blended
name_authority() -> DataFrame           AUTHORITY for player name + position
current_rank()   -> DataFrame           an extra column, never part of the ordering
```

Adding a sport means writing one module and one config. Nothing else changes.

## The sports

| | NBA | NFL | MLB |
|---|---|---|---|
| Blended | Hashtag dynasty 0.67 / keeper 0.33 | FantasyPros dynasty SF 0.67 / KeepTradeCut 0.33 | HarryKnowsBall 1.0 |
| Names | Yahoo league 667 | Yahoo league 101 (offense) | Fantrax league (JSON API) |
| Current | FantasyPros points-Yahoo | FantasyPros half-PPR SF | FantasyPros Roto |

The Yahoo and Fantrax leagues are **arbitrary public leagues**, used only as a
source of canonical names and positions. No rosters, no ownership, and their
scoring format is irrelevant.

## Manual imports (currently unused)

Every source is now scraped, so nothing needs exporting by hand. NFL used to
read paid DLF exports from `imports/nfl/` until 2026-08-22, when FantasyPros
dynasty superflex and KeepTradeCut replaced them.

`common.read_import()` remains for the next paywalled source: it takes the
**newest** file matching a pattern, so the folder never needs clearing, and it
warns when the newest is over 45 days old. Create `imports/<sport>/` and drop
files in when something needs it.

The old DLF CSVs were deleted once nothing read them. To get them back:

```bash
git checkout d362829 -- imports/nfl/
```

## Rosters

```bash
python rosters.py --sport mlb
python rosters.py --sport all --cache
```

Reads a **Google Sheet** — one tab per sport, plus `Mapping` and `Overrides` —
and writes `output/rosters_<sport>.csv`:

```csv
player,fantasy_team
Shohei Ohtani,Jonathan (4)
Bobby Witt Jr.,Tommy (1)
```

The sheet is shared "anyone with the link can view", so each tab is fetchable
as CSV with no credentials — which is what lets GitHub Actions read it. Paste
each site's roster page in exactly as it comes; the shape is *detected* rather
than configured, so the Fantrax export and the two Yahoo pastes all work
through one path.

`player` is the naming authority's spelling, so it joins straight onto
`combined_rankings_<sport>.csv`. `fantasy_team` is your own code from the
Mapping tab, so one owner reads the same across all three sports and a renamed
fantasy team doesn't look like every player on it being traded at once.

A player the authority doesn't recognise is **kept**, using the site's own
spelling, and reported separately — the roster and the authority come from the
same site, so dropping him would quietly shrink a real roster.

Local files under `rosters/<sport>/` still work as a fallback if `sheet_id` is
removed from a config, but nothing uses that path now.

**Matching is id-first where the export carries ids.** Fantrax exports wrap them
in asterisks (`*05ucd*`) and they're the same global player ids the API
returns, so they're exact where names aren't — 666 of 669 rows on the current
MLB roster resolve by id, including the sixteen whose names are ambiguous in
the pool. Names are the fallback.

Which columns to read is per sport in `config/<sport>.json`:

```json
"rosters": { "id_col": "ID", "player_col": "Player", "team_col": "Status" }
```

For a Fantrax league export, `Status` holds the fantasy team abbreviation.
`Roster Status` is the Active/Reserve/Minors state — not what you want.

**Yahoo pastes (XLSX)** need no config — `roster_readers.py` works them out.
Each team block starts with a `Pos` header and the team name is the cell above
it. NBA pastes stack vertically in one column; NFL pastes use two.

### Undecorating names without a suffix list

Yahoo welds UI text onto player names, and for names it *abbreviates* there is
no clean copy anywhere — `G. AntetokounmpoINJPlayer Note` is the only version
on the page.

Stripping known suffixes is a losing game. The two current files alone contain
**270 distinct suffix combinations**, built from `INJ` / `GTD` / `NA` / `Q` /
`Video Forecast` / three spellings of "Player Note" / a trailing `TEAM - POS` —
and Yahoo can add a flag whenever it likes.

So the primary path inverts it: `known_prefix()` asks **which real player name
this cell starts with**, using the naming authority we already load. New
decoration costs nothing, because it's never enumerated. The character after
the match must not be lower-case, so `Jalen Green` can't be pulled out of
`Jalen Greenwood`, and the longest match wins.

`strip_decoration()` survives as a fallback for players absent from the
authority. It carries one trap worth remembering: the `TEAM - POS` pattern must
require spaces around the hyphen *and* an upper-case position, or
`N. Alexander-Walker` ends in something that looks exactly like it
(`der-Walker`) and gets truncated to `N. Alexan`.

### Keeping rosters current out of season

Yahoo leagues go dormant, so from roughly January the NFL pages stop reflecting
reality — but teams keep trading. Editing `output/rosters_<sport>.csv` is no
good; it's regenerated on every run.

Manual edits live in the **`Overrides` tab** of the roster Sheet and are
re-applied on top of the parsed roster every run:

| sport | player | fantasy_team |
|---|---|---|
| nfl | Josh Allen | Tommy (1) |
| nfl | Bo Nix | |

Fill `fantasy_team` to move or add; leave it blank to drop. One tab covers all
three sports — rows are filtered by the `sport` column.

It has to live in the Sheet rather than a file: `rosters/` is gitignored, so
GitHub Actions would never see it. That gap made the feature dead on arrival
when the pipeline moved to the cloud, and it would only have surfaced in
January when a trade failed to stick.

Names go through the same matcher, so `josh jacobs` resolves. A name matching
nothing is still added, but prints a **WARNING**, because that's exactly what a
typo looks like. Every edit is reported as it's applied:

```
[manual]  3 override(s)
          moved 'Josh Allen' Tommy (1) -> Blake (2)
          dropped 'Bo Nix' from Kevin (5)
          moved 'Josh Jacobs' ... (matched 'josh jacobs')
```

**Traded twice? Add two rows.** They apply top to bottom and the last wins, so
append as trades happen rather than hunting for a row to edit — just never
re-sort the tab, because order is what makes the sequence work. Clear the tab
when a fresh export already reflects those trades; a stale override can undo a
newer one.

When a fresh export lands, the run warns that it's newer than the overrides —
edits already baked into the new export are redundant, and a stale one can
quietly undo a newer trade.

### When a file can't be parsed

An outright failure is easy — no `Pos` headers means zero players and a hard
exit. The real risk is a parse that half-works and produces a plausible-looking
file, so `validate()` checks for that specifically:

| Symptom | What it usually means |
|---|---|
| A team over the cap (35 NBA/NFL, 70 MLB) | Block boundaries merged |
| Fewer than 2 teams | Only part of the league pasted |
| Teams under 5 players | Boundaries fragmented |
| Names still carrying `Player Note`, ` - `, etc. | Undecorating failed |
| Blank names, duplicate player/team rows | Structural damage |

Each prints a `[warn]` naming the offending teams. A clean run prints none of
them, so silence is meaningful.

Unresolvable players are always listed by name at the end rather than dropped
silently.

Players on the roster but absent from the board are simply unranked by the
dynasty source — expected for deep prospects and fringe relievers. A handful
are missing from the *naming authority* instead, because those public leagues
carry a narrower player pool than yours: Brandon Clarke isn't among Yahoo 667's
662 players, and two MLB prospects aren't in the Fantrax pool.

## When a source goes dark

Hashtag's keeper page went from 760 rows to zero on 2026-08-26 -- HTTP 200 the
whole time, just with the table gone. That took the NBA board down for days.

Every source now passes through `guard_source()`. A pull at or above the config
floor is banked to `lastgood/<sport>_<source>.csv`; one below it falls back to
the banked copy, and the board is built rather than lost. Never silently: the
fallback prints, and `output/_source_status_<sport>.csv` carries it to a Sheet
tab, because stale data blended into a fresh board is the quiet-wrong-answer
case.

When a source comes back, the run says so: `[RECOVERED]` with how long it was
gone, and a matching note in the status file. Recovery used to be silent -- the
`[STALE]` line simply stopped appearing -- and that is the moment worth seeing,
because it is when a board stops being partly historical.

The banked date lives in a `.banked` sidecar, **not** in the file's mtime.
Actions clones fresh every run, so checkout stamps every file with the current
time and a copy banked three weeks ago would report as 0.0 days old -- exactly
the reading the 14-day staleness warning exists to catch.

## prospect_rank (MLB)

A second ranking over the same board, restricted to players still in the minors
— "who is the best prospect here", which `combined_rank` cannot answer because
established major leaguers sit above them. Dense 1..N in `combined_rank` order,
627 players currently.

Which rungs count is config, not code:

```json
"prospect_rank": { "levels": ["ROK", "A", "A+", "AA", "AAA"] }
```

Two things worth knowing:

- **HKB spells its levels differently** — `LOW_A`, `HIGH_A`, `ROOKIE_BALL` — and
  `_HKB_LEVELS` relabels them to the way they are said out loud. An unmapped
  rung passes through unchanged so it shows up in the `level` column rather
  than quietly dropping a player out of the prospect list.
- **It is NOT HKB's own `prospectRank`.** That field counts 691 players as
  prospects including ones already in the majors, so it answers a different
  question.

An unknown level leaves `prospect_rank` blank rather than sorting last: a
player with no level is one no ranking source placed, so ranking him worst
would assert something nobody measured.

## Rostered but unranked

Someone can be owned in the league while no ranking source covers him -- a deep
prospect, a recent call-up. Those players are appended to the **bottom** of the
board with `sources_matched = 0` and a **blank** `value` -- blank rather than 0
so they read as "not valued" rather than "valued at nothing" -- and the board
never silently disagrees with the roster.

They are **derived from the roster on every run**, not added permanently. Drop a
player and he simply stops being appended -- there is no list to maintain and
nothing to clean up.

Matched on name **and** team, never name alone: MLB has 152 duplicated names in
the pool, so Fernando Cruz is both a Yankees reliever and a Cubs shortstop.
Keying on name alone skipped 15 real players who happened to share one.

## The blend

`blended_score = Σ(weight × rank) / Σ(weight)`, sorted ascending. Lower is
better. Weights are renormalized, so `0.67/0.33` and `2/1` are the same thing.
Ties break toward the player confirmed by more sources.

`missing_policy` decides what happens when a player is in one source but not
another:

- **`penalty`** (default) — a missing rank counts as *last place in that source + 1*.
- **`drop`** — only rank players present in every source.
- **`renormalize`** — average whichever sources have him, rescaling the weights.

`sources_matched` counts sources that *actually listed* the player — under
`penalty` every source contributes a number, so it is deliberately not derived
from the scoring inputs.

## Value

Every board carries a `value` column: **100 for the best player, 0 at
replacement level**, on a scale where addition is meaningful.

```
value(r) = 100 × (r^-a − R^-a) / (1 − R^-a)
```

**`R` is the replacement rank** — the last rostered player in the league, taken
from the parsed rosters (NBA 337, NFL 303, MLB 667). That zero point is what
makes the numbers add up honestly: two players worth 20 really do represent 40
of surplus over what you could have for free. Without it, stacking enough bench
players would "equal" a star, which is nonsense.

**`a` is top-heaviness**, and it isn't set directly. It's solved from
`top_20_ratio` — how many top-20 players the #1 player is worth, currently
**2.1** — so the shape stays honest if roster sizes change, and all three
sports agree at the anchor despite very different depths. Both live in
`config/<sport>.json` under `value`.

| Rank | NBA | NFL | MLB |
|---|---|---|---|
| 1 | 100 | 100 | 100 |
| 10 | 60 | 60 | 59 |
| 20 | 48 | 48 | 48 |
| 50 | 32 | 32 | 34 |
| 100 | 20 | 19 | 24 |
| 300 | 2 | 0 | 10 |

They agree through the top ~50 and diverge only in the deep tail, which is
correct rather than a flaw: MLB rosters 667 players, so its #300 is genuinely
worth something, while NFL's #300 is replacement level.

Sanity check — a 40 plus a 20 lands on a 60:

```
LaMelo Ball (40.1) + Joel Embiid (20.1) = 60.2  ~  Jayson Tatum (59.6)
```

`current_value` applies the same curve to `current_rank`, so the gap between
the two columns is a buy-low / sell-high signal. Cooper Flagg is 71.6 dynasty
against 31.0 current; Jokić is 75.5 against 100.

**Consolidation:** the stored values are deliberately pure and additive. If you
want the roster-spot cost that real markets price in, apply it when comparing
*packages* — around 2 points per extra player received — rather than baking it
into per-player values.

## Name matching

Every source is rewritten onto the authority's keys before anything is joined,
so one player has one identity. Three passes, each firing only when the one
before missed:

1. **Exact**, on a normalized key — folds accents (`Jokić` → `jokic`), strips
   punctuation (`P.J.` → `pj`), hyphens → spaces, drops `Jr/Sr/II/III/IV`.
2. **First-initial** — `giannis antetokounmpo` → `g antetokounmpo`. Yahoo
   shortens the first name when two players share a surname
   (`G. Antetokounmpo`, `N. Alexander-Walker`) and uses nicknames
   (`Alex Sarr`, `Nic Claxton`). None of those can match on the full name.
3. **Space-less** — `joshallen` → `josh allen`. Found in DLF's ADP export,
   which wrote the #1 overall player as `JoshAllen` with no space. That source
   is gone, but exported CSVs do this often enough to keep the pass.

A pass only counts when it lands on **exactly one** player — OKC rosters both
Jalen and Jaylin Williams, and guessing is worse than reporting the miss.

### Names don't identify players (mostly a baseball problem)

Fantrax's MLB pool has **152 duplicated names in 9,926** — there are three Jose
Ramirezes (CLE 3B, DET OF, DET RP). Collapsing them would quietly bind a star's
ranking to a middle reliever.

So the authority is indexed by a **uid**: the plain name key when unique, else
`name|team`, else `name|team|pos`. Sources pass their own team *and position*
through, and both are tried in that order. When a name is still ambiguous, the
match is *refused and reported* rather than guessed.

Position is not redundant with team — it rescues two distinct cases:

- **Team can't possibly settle it.** Both Jared Joneses are on PIT, one an SP
  and one a 1B.
- **The authority's team is stale.** Edwin Diaz was traded to LAD; the pool
  still lists him on NYM, so his team matches *neither* candidate. Position
  (RP vs SS) does.

Adding the position pass recovered 14 MLB players who were being silently
dropped as unresolvable.

**A traded player's team may be slash-joined** on the authority side — Fantrax
writes `ATH/SD` for Mason Miller while every other source says `SD`. Team
comparison splits on `/` and tests membership. Before that fix the resolver saw
two Mason Millers, couldn't separate them, and correctly refused to guess —
silently dropping the #1 reliever off the board.

Fantrax's `scorerId` is stored but is **not** the cross-source key it looks
like — no other source carries it. It tells same-named Fantrax players apart;
the join itself still runs on name + team.

Normalization is only ever used for *matching*. Display names come from the
authority verbatim, so `Dončić`, `Şengün` and `Niederhäuser` keep their
diacritics. CSVs are UTF-8 with BOM so Excel doesn't mangle them.

### aliases-`<sport>`.csv

Only needed when the **first initial differs** between the two sites. Currently
two in total:

```
carlton carrington,Bub Carrington     # NBA
marquise brown,Hollywood Brown        # NFL
```

Check `output/unmatched_<sport>.csv` after each run. Rows labelled
`ranked, no name match` are actionable; `on authority, unranked` is expected
(the authority lists far more players than anyone ranks).

Or get everything at once:

```bash
python unresolved_report.py
```

It reads what the last runs wrote — rankings misses *and* roster misses, all
three sports — and shows each source's rank separately rather than a single
"best rank". That distinction matters: NBA blends a 400-deep dynasty list with
a 751-deep keeper one, so a keeper #170 is mid-table filler, not a near-miss
star. A blank in the dynasty column is the tell.

As of 2026-08-22 nothing meaningful is unresolved. The best-ranked miss in any
sport is NBA keeper #167 of 751, and only two of 159 NBA misses carry a dynasty
rank at all (best #357 of 400). Three rostered players aren't in their naming
authority — those can't be aliased, since the public leagues used for naming
carry narrower pools than Kyle's own leagues.

## History

Every run archives what it wrote:

```
output/history/boards/<sport>/YYYY-MM-DD.csv
output/history/rosters/<sport>/YYYY-MM-DD.csv
```

Movement — who's rising and falling, who got traded — is the one thing that
**cannot** be reconstructed after the fact, because every run overwrites the
live file. One file per day; re-running on the same day overwrites that day
rather than accumulating duplicates.

Costs about 160KB/day, so roughly 57MB/year. `output/` is gitignored, so this
doesn't touch the repo. To prune later:

```powershell
Get-ChildItem output\history -Recurse -File |
  Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-180) } | Remove-Item
```

## Automatic daily refresh

**Runs in GitHub Actions — no local machine involved.**
`.github/workflows/refresh.yml` refreshes the boards and rosters each morning
and commits whatever changed back to this repo. Your Sheets read those files
from `raw.githubusercontent.com`.

Three UTC cron slots with an Eastern-hour gate, because GitHub cron has no
timezone and 7am Eastern moves with DST. The 08 and 09 slots are catch-ups:
GitHub *queues* scheduled runs rather than guaranteeing them, so a later slot
refreshes only if the day hasn't been done. `[skip ci]` on the commit stops the
push retriggering the workflow forever.

`actions/cache` restores `.cache` between runs. Without it every run refetches
everything — Yahoo alone is 45 paginated requests for names that barely change.

To run it by hand: Actions -> Refresh boards and rosters -> Run workflow. A
manual run bypasses the time gate.

**History lives in git.** Each refresh is a dated commit, so to see an old
board:

```bash
git log --oneline -- output/combined_rankings_nba.csv
git show <sha>:output/combined_rankings_nba.csv
```

`output/history/` is still written when running locally but is gitignored — it
would only duplicate what the commits already hold.

### Running locally

Still works, for debugging or a change you want to see before pushing:

```bash
python rankings.py --sport all
python rosters.py --sport all
```

## Superseded: the old local schedule

A Windows scheduled task used to do this on Kyle's PC via `run-daily.cmd`. Both
are gone as of 2026-08-26 -- the task is disabled (not deleted, so
`Enable-ScheduledTask "dynasty daily refresh"` brings it back) and the script is
removed. It was also the only tracked file carrying a hard-coded local path,
which does not belong in a public repo.


## Google Sheets

`sheets-import.gs` — Apps Script that reads the CSVs from Drive. Runs as you, so
the files stay private and there are no credentials to store. **One sheet per
sport**: paste it into each and set `SPORT` at the top.

Each sheet gets two tabs:

| Tab | From |
|---|---|
| `Rankings` | `combined_rankings_<sport>.csv` |
| `Rosters` | `rosters_<sport>.csv` |

A missing file is skipped with a log note rather than failing the run, so a
sport with no roster yet still imports its board.

Setup is in the file's header comment. Short version: Extensions → Apps Script
→ paste → set `SPORT` → run `importRankings` and approve the "unverified app"
prompt → run `createDailyTrigger`.

Updating an already-configured sheet is just re-paste and Save — the entry
point is still `importRankings`, so existing triggers keep working.

A disabled service-account path (`push_to_sheets` + the `google_sheets` config
block) exists as an alternative. It needs a JSON key, which is a live
credential in a Drive-synced folder — prefer the Apps Script.

## The "current" rank switches with the season

FantasyPros publishes preseason/draft rankings out of season and
rest-of-season rankings during it, at different URLs, and the changeover month
differs per sport. `config/<sport>.json` lists the windows explicitly:

```json
"current": { "windows": [
  { "months": [5,6,7,8,9,10],  "url": "...overall-points-yahoo.php" },
  { "months": [11,12,1,2,3,4], "url": "...ros-overall-points-yahoo.php" }
]}
```

Explicit month lists rather than date arithmetic — the windows wrap the year
end, and a list is easy to eyeball. All 12 months must be covered; an
uncovered month warns and falls back to the first window.

| Sport | Preseason | In season |
|---|---|---|
| NBA | May–Oct `overall-points-yahoo` | Nov–Apr `ros-overall-points-yahoo` |
| NFL | Jan–Sep `half-point-ppr-superflex-cheatsheets` | Oct–Dec `ros-half-point-ppr-overall` |
| MLB | Nov–Apr `overall` | May–Oct `ros-overall` |

Expect a one-off "dropped sharply" warning at each changeover — rest-of-season
lists are shorter than preseason ones. It settles after the first run on the
new URL.

## Extra reference columns (`extra_ranks`)

A sport module may expose `extra_ranks()`, returning lists that become columns
but take **no part in the blended ordering**. MLB uses it for two weekly
PitcherList boards:

| Column | Source |
|---|---|
| `sp_rank` | "The List" — weekly top-100 starting pitchers |
| `rp_rank` | Closers/Holds/SOLDs — the **SOLDs** board |

They're pitcher-only, so blending them would treat every hitter as missing and
penalise the whole batting half of the board. They ride along like
`current_rank` instead.

Each entry declares the months it applies to (**May–Oct**). From November the
columns go null rather than carrying October's final post forward — a stale
ranking that still looks current is worse than a blank. Out-of-season runs skip
the fetch entirely.

**Finding the weekly post.** Each post lives at a brand-new URL, so rather than
guess the slug the code reads the category's WordPress RSS feed and takes item
one. Parse `<item>` blocks, not every `<link>` — the channel's own `<link>`
comes first and is the category page.

**Two traps in these posts:**

- The reliever post has **three tables with identical headers** — closers (50),
  holds (100), SOLDs (100). Matching on headers alone grabs the wrong list;
  always take the **last** match. The starter post's first table is an injury
  list, same hazard.
- Names carry welded-on tier markers: `Mason MillerT1`, `Dylan LeeT1`, on the
  first pitcher of each tier. Stripped with a trailing `T\d+` rule.

## When a source degrades

Sources return HTTP 200 and still go useless. On 2026-08-22 FantasyPros served
exactly **one** NBA player. Accepted silently, that blanks `current_rank` for
583 players, and since this runs unattended it would go unnoticed for weeks.

So each source declares a row floor (`sanity_floor` in the config). The
current-rank source gets two tiers:

- **below `sanity_floor.current`** (10) — the pull is treated as broken and
  `.cache/current-last-good-<sport>.csv` is used instead;
- **clears the floor but collapsed against the last good pull** (under half) —
  warns and proceeds. Self-calibrating, so there's no number to retune as a
  sport's ranked pool grows.

A footnote worth remembering: the NBA "current rank" looked like a dead source
for a while — one player, `total_experts: 0`. It was the wrong URL. The roto
overall page is empty out of season; `overall-points-yahoo` has 250. Check the
URL before concluding a source has died.

Severity is split deliberately: the ranking sources and the naming authority
are load-bearing and an empty parse aborts the run. The current rank is only an
extra column, so a bad pull there warns but still ships the good rows.

## Before you change common.py

```bash
python check_regression.py            # compare boards against tests/baseline_<sport>.csv
python check_regression.py --update   # re-bless after an intended change
```

One file feeds three sports, so a "harmless" tweak to the resolver can
reshuffle a board you weren't thinking about. The check re-runs the pipeline
with `--cache` (no network) and compares rank, player, position, team and
score. It deliberately ignores `current_rank` and the PitcherList columns,
which track live weekly sources and would otherwise cry wolf every few days.

It's been verified to catch a real break: disabling the first-initial pass
drops the NBA board from 584 to 574 rows.

**The baseline is a "before my change" snapshot, not permanent truth.** The
sources move most days — on 2026-08-24 the NBA board kept all 584 players but
reordered, while NFL gained 4 and lost 2 — so a baseline more than a day old
will differ for reasons that have nothing to do with your code.

The workflow that makes it meaningful:

```bash
python check_regression.py --update   # 1. bless current output
                                      # 2. make your change
python check_regression.py            # 3. any difference is YOURS
```

The check prints how old the baselines are, so a diff can be read correctly. A
few shifted values against a same-day baseline is the code; against a week-old
one it's the sites.

## Version control

`git init` was run on 2026-08-22 with everything committed except the cache
(~99MB, disposable), `output/`, and `run.log`.

One note: the repo lives inside a Drive-synced folder, which is fine for
single-machine use, but don't edit it from two machines at once — Drive can
sync `.git` mid-write.

Every source is public now, so nothing under version control is paid content.
If a future source needs manual exports under `imports/`, exclude them before
pushing anywhere.

## When a page changes

```bash
python rankings.py --sport nba --cache --inspect
```

Hard-won traps, each of which cost a real debugging pass:

- **Read raw HTML, never a browser's `innerText`.** CSS uppercases Hashtag's
  labels, so devtools shows `DYNASTY #1` while the HTML says `Dynasty#1`.
  Reading the rendered text produced a completely wrong diagnosis once already.
- **Hashtag cards are ragged.** Rookies render as `245 (NEW)` with no position,
  no stats and sometimes an empty `Keeper #`; Moussa Cisse (#290) has no age at
  all, just a bare `yo`. Only name, team, `yo` and `Dynasty #<n>` are required.
  Requiring more cost 58 players including Cameron Boozer at dynasty #10.
- **Yahoo cases team abbreviations differently per sport** — `SAS - C` for NBA,
  `Det - RB` for NFL. NFL parsed names fine while silently returning `None` for
  every position until this was fixed.
- **Yahoo's `count` is a row offset, not a page size.** It can't be widened.
- **FantasyPros:** read the `var ecrData` JSON, not the rendered table — the
  table abbreviates names (`J. Williams` is ambiguous). Same structure in all
  three sports.
- **Config files are read as `utf-8-sig`.** Notepad and PowerShell write a BOM
  that plain `json.loads` rejects.

## Not done yet

- **MLB `current_rank` covers 1,157 of 1,687.** FantasyPros ranks 1,605 players
  but HarryKnowsBall ranks deep prospects it doesn't cover — expected, not a fault.
- **62 MLB names need aliases** (`output/unmatched_mlb.csv`). Mostly prospects
  where the two sites disagree on spelling. Worth a pass when convenient.
- **NBA `current_rank` covers 244 of 584** — FantasyPros ranks 250 NBA players
  right now, so the deep end of the board has no current rank. Expected.
- Hashtag's dynasty page serves its default 400 of ~750. Going deeper needs an
  ASP.NET postback a plain GET can't do.

## When a source goes quiet

`rankings.py` refuses to build a board from a source that parsed to zero rows,
which is right: a silently half-built board is worse than none. It isolates the
sports, so one failing does not stop the others -- it returns non-zero but still
writes every board that worked.

The workflow did not respect that. A failed step skipped the commit, so a run
could compute fresh NFL and MLB boards and throw both away because NBA's source
had gone quiet. The rosters and commit steps now run unless the job was
cancelled, and the run still goes red for the sport that failed.

**Known outage:** Hashtag Basketball pulled its keeper table in August 2026 --
the page says the 2026 draft class is being loaded into the voting system and
will take a few days. NBA fails daily until it returns; NFL and MLB are
unaffected. Nothing to fix here, and `_parse_keeper` needs no change: the page
now renders client-side and serves no `<table>` at all, so if it does not come
back the source needs replacing rather than repairing.
