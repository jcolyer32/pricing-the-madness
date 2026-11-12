# NCAAM Data Pipeline — your model's data template

```
 KenPom (front page) ─┐
 TeamRankings stats  ─┼─▶ run.py ─▶ match team names ─▶ validate ─▶ output/ncaam_data.xlsx ─▶ Power Query ─▶ YOUR MODEL
 ESPN scores/schedule ┘              (one TeamKey)       (or keep last good)     (the "feed")        (Refresh All)
```

**Main idea:** Python does the downloading, and Excel only reads a clean file.
Your model workbook never talks to a website. It links to one tidy feed workbook
that the script rebuilds on every run. That split is what keeps it robust:

| Problem | What handles it |
|---|---|
| Sites spell teams differently ("Connecticut" vs "UConn") | Every team gets one `TeamKey` (ESPN's team id), via `data/team_aliases.csv` + auto-matching |
| A site is down or blocks you | Retries, then the **last good copy** is used. `_Status` turns red |
| A site redesigns its table | Parsers check the layout and **refuse** to guess. Old data is kept and you get a clear error |
| Half-loaded page or bad numbers | `pipeline/validate.py` checks row counts, value ranges, duplicates |
| Season-year column headers change ("2026" → "2027") | Columns get stable names like `Season`, `Last3`, `Home` |
| You want to backtest | Every run saves a dated copy in `data/snapshots/` |

---

## 1. One-time setup (≈5 min)

1. Check you have Python 3.10 or newer. Open **Terminal** and run `python3 --version`. If it's missing, install it from python.org.
2. Double-click **`Setup (run once).command`**. If macOS blocks it, right-click ▸ Open ▸ Open.
   This does three things:
   - creates a private Python environment in `.venv`
   - installs the libraries
   - runs the offline tests and a live connection check (`--diagnose`)
3. Check the diagnose output. You want a ✔ for KenPom, TeamRankings and ESPN.
   A ✘ with `HTTP 403` means the site blocked the script. See §5.

## 2. Refreshing

Double-click **`Refresh NCAAM Data.command`**. (In Terminal: `source .venv/bin/activate && python run.py`.)

- The first run of the season downloads every game day since opening night, one request per second, so it takes a few minutes. After that, runs only fetch new days and take well under a minute.
- Useful options: `python run.py --fresh` (ignore the 1-hour page cache) and `python run.py --only kenpom`.

Then open your model workbook and click **Data ▸ Refresh All**.

## 3. Connecting your model workbook (Power Query) — do this once

> Excel for Mac's Power Query can't read web pages, but it reads Excel files fine.
> That's the reason for the feed-workbook design.

1. Run the refresh once so that `output/ncaam_data.xlsx` exists.
2. In your model workbook: **Data ▸ Get Data (Power Query) ▸ Excel workbook** ▸ browse to `ncaam_pipeline/output/ncaam_data.xlsx`.
3. Tick the sheets you want. Start with **Master**, **ESPN_Games**, **ESPN_Schedule** and **_Status**. Click **Load**, and each one lands as an Excel Table on its own sheet.
4. For each query: **Data ▸ Queries & Connections** ▸ right-click ▸ **Properties** ▸ tick **Refresh data when opening the file**.
5. Optional, recommended: in the Power Query editor, **set column types** (Transform ▸ Data Type). Then a stray text value shows up as an error instead of quietly becoming 0.


### If Power Query errors on Mac: use the FeedImport macro instead

Excel for Mac sometimes refuses Power Query ("this query can't be run in Excel for Mac"). `excel/FeedImport.bas` does the same job with a small macro:

1. Save your model workbook as **.xlsm**.
2. **Tools ▸ Macro ▸ Visual Basic Editor ▸ File ▸ Import File…** ▸ pick `excel/FeedImport.bas`.
3. **Tools ▸ Macro ▸ Macros ▸ RefreshFeed ▸ Run.** The first time, click **Grant Access** when Mac asks.

It copies the sheets listed in `SHEETS_TO_IMPORT` (default `Modelv26,_Status`) into your workbook under the same names. Look teams up by header so column order never matters:
`=XLOOKUP($A2, Modelv26!$A:$A, XLOOKUP("SOS", Modelv26!$1:$1, Modelv26!$A:$Z))`

### Rule for your model sheets: look things up by team, never by row

Row order changes on every refresh, because rankings move. So never reference `Master!F37`. Look up by team:

```excel
=XLOOKUP($A2, Master[Team], Master[KP_ORtg])            ' A2 = "Duke"
=XLOOKUP($A2, Master[Team], Master[TR_eFG_Season])
=XLOOKUP($A2, Master[Team], Master[TR_Predictive_Rating])
```

Keep your own inputs (weights, home-court value, manual adjustments) on separate sheets that Power Query never touches.
Your formulas then survive every refresh, and adding a column to the feed never breaks them.

**What's in `Master`:** one row per D-I team (about 362). It has:
- `TeamKey`, `Team`
- KenPom columns (`KP_NetRtg`, `KP_ORtg`, `KP_DRtg`, `KP_AdjT`, `KP_Luck`, `KP_SOS_*`…)
- TeamRankings columns (`TR_PPG_Season`, `TR_eFG_Home`, …)
- `TR_Predictive_*`

`ESPN_Games` has one row per finished game: scores, margin, total, neutral site, conference game, and the line when ESPN has it.
`ESPN_Schedule` has the next 7 days of games, which is what your model predicts.

## 4. Customizing (this is the "template" part)

Normally you only edit **`config.yaml`**:

- **Add a TeamRankings stat.** Open the stat on teamrankings.com. The URL ends in `/stat/free-throw-pct`, so add
  `- {slug: free-throw-pct, name: FT_Pct}`. Re-run and you get new columns `TR_FT_Pct_Season` etc.
- **Choose which splits to keep:** `keep_columns: [Season, Last3, Home, Away]`.
- **New season:** change `season.year`, `start_date` and `end_date`. Then delete `data/espn_games.csv` (it holds last season's game log).
- **Turn a source off:** set `enabled: false`.

Adding a whole new source means copying a file in `pipeline/sources/` (kenpom.py is the simplest example). It needs:
- a `parse(html) -> DataFrame` function
- a `fetch(fetcher, cfg) -> {"SheetName": df}` function

Then add it to the `jobs` in `run.py` and a rule block in `validate.py`.

### Model sheets (e.g. `Modelv26`)

`model_sheets` in `config.yaml` defines sheets laid out exactly like your model. Each one has your headers, your units, one row per team, and is sorted A–Z.
`Modelv26` = SOS, OFF/DEF Efficiency, Effective FG %, Floor %, FG%, REB, TO, 3PM, OFF/DEF Diff.
To start a new model version, copy the block, rename it (e.g. `Modelv27`), and add, remove or reorder lines. Any page a model sheet needs is fetched automatically.
In Excel, load that sheet with Power Query (as in §3) and look teams up with `=XLOOKUP($A2, Modelv26[Team], Modelv26[SOS])`.

### gretl

Every refresh also writes gretl-ready copies of the model sheets to `output/gretl/`:
- `ncaam_gretl.xlsx` has one sheet per model (e.g. Modelv26).
- There's also one CSV per model.

Headers are turned into valid gretl names (`Effective FG %` → `Effective_FG_Pct`, `3PM` → `x_3PM`). `Team` is the first column, so gretl uses it as the observation labels.
To load it in gretl: File ▸ Open data ▸ User file ▸ file type "Excel files (*.xlsx)" ▸ `ncaam_gretl.xlsx` ▸ pick the sheet. Or open `Modelv26.csv` for the fewest prompts.

## 5. When something goes wrong

Open the **`_Status`** sheet first. The feed workbook carries it, and your model can load it too:

| Result | Meaning | Do this |
|---|---|---|
| OK | Fresh data | — |
| FAILED – using last good | This run's data failed. Yesterday's good copy is in use (see `Data_As_Of`) | Read `Details`. Usually a one-off outage, so re-run later |
| FAILED – no data | Failed, and there's no earlier copy to fall back on | Read `Details` / the log in `logs/` |
| ACTION – N team names unmatched | A site used a spelling nobody recognizes. The name is **left blank, not guessed** | See the `_Unmatched` sheet. Add a line to `data/team_aliases.csv`, e.g. `teamrankings,E Tenn St,East Tennessee State`, then re-run |
| CHECK – new fuzzy matches | A close spelling was auto-matched | Look at the pairs once. If one is wrong, fix it in `data/team_crosswalk.csv` |

**Common errors:**

- **HTTP 403 (blocked).** `curl_cffi` is installed by setup and usually gets past this. If a site still blocks you:
  1. Open the page in your browser.
  2. Choose **File ▸ Save Page As…** (format "Page Source").
  3. Save it into `data/manual_html/` with the file name the error message gives, e.g. `kenpom_home.html`.

  The script uses that file whenever the live fetch fails.
- **"layout changed" / "unexpected columns".** The site redesigned its table. Update the column list at the top of that source's file: `COLUMNS` in `kenpom.py`, `STAT_COLS` in `teamrankings.py`. Until you do, your model keeps using the last good data.
- **"Can't write ncaam_data.xlsx — is it open?"** Close the *feed* workbook. Your model workbook can stay open.
- **Early season (first week or so):** TeamRankings splits are mostly blank, so the blank-share check may fail. Loosen `max_blank_share` in `validate.py`, or drop `Last3`, `Home` and `Away` from `keep_columns` for now.

## 6. Backtesting data

- `data/snapshots/YYYY-MM-DD/` keeps every table as it looked on each day you ran it.
- `python run.py --tr-asof 2027-01-15` pulls TeamRankings stats as they stood on a past date. Use it to rebuild history for testing the model.
- KenPom's free front page shows current ratings only, so daily snapshots are the only way to keep KenPom history. That's a good reason to refresh every day.

## 7. Optional: refresh automatically every morning

```bash
crontab -e
# add (runs 8:15 every day):
15 8 * * * cd ~/Documents/ncaam_pipeline && .venv/bin/python run.py >> logs/cron.log 2>&1
```

macOS may block scheduled jobs from reading the Documents folder. If `logs/cron.log` shows "Operation not permitted", give Terminal/cron **Full Disk Access** (System Settings ▸ Privacy & Security), or move the project out of Documents.

## 8. Being a good citizen

- **KenPom:** the free front page only, one request per run. Don't point this at pages that need a subscription.
- **TeamRankings:** requests are 3 seconds apart, and pages are cached for an hour.
- **ESPN:** this is the undocumented feed behind espn.com. It's stable in practice but not guaranteed. The validator will catch it if it changes.

Check each site's terms of use if you ever share or publish the data.

## Project map

```
config.yaml                 ← what to fetch (edit this)
run.py                      ← the refresh; start reading here
pipeline/http.py            ← polite fetching, retries, cache, manual fallback
pipeline/sources/*.py       ← one parser per website
pipeline/teams.py           ← team-name matching (TeamKey)
pipeline/validate.py        ← sanity rules per table
pipeline/output.py          ← workbook / CSV / snapshots / last-good
data/team_aliases.csv       ← your name fixes (edit this)
data/team_crosswalk.csv     ← every name→team match ever made (auto, reviewable)
output/ncaam_data.xlsx      ← THE FEED — Power Query points here
tests/test_pipeline.py      ← offline tests (python -m pytest tests -q)
```
