#!/usr/bin/env python3
"""
NCAAM data refresh — run this, then hit Data ▸ Refresh All in Excel.

    python run.py                 # normal refresh
    python run.py --fresh         # ignore the 1-hour page cache
    python run.py --only kenpom   # just one source (kenpom, teamrankings, espn)
    python run.py --diagnose      # test each site's connection & parsing, write nothing
    python run.py --tr-asof 2027-01-15   # TeamRankings numbers as of a past date (backtesting)

Flow:  fetch ─▶ match team names ─▶ validate ─▶ (fallback to last good) ─▶ write feed workbook
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from pipeline import model_sheets, output, validate                       # noqa: E402
from pipeline.http import Fetcher                            # noqa: E402
from pipeline.sources import espn, kenpom, teamrankings      # noqa: E402
from pipeline.teams import TeamMatcher                       # noqa: E402

log = logging.getLogger("ncaam")


def setup_logging() -> Path:
    logdir = ROOT / "logs"
    logdir.mkdir(exist_ok=True)
    logfile = logdir / f"run_{datetime.now():%Y-%m-%d}.log"
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if root.handlers:            # already set up (e.g. called twice in tests)
        return logfile
    for h in (logging.StreamHandler(sys.stdout), logging.FileHandler(logfile)):
        h.setFormatter(fmt)
        root.addHandler(h)
    return logfile


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fresh", action="store_true", help="ignore page cache")
    ap.add_argument("--only", help="comma list: kenpom,teamrankings,espn")
    ap.add_argument("--diagnose", action="store_true", help="test connections, write nothing")
    ap.add_argument("--tr-asof", help="YYYY-MM-DD: TeamRankings snapshot for a past date")
    args = ap.parse_args()

    logfile = setup_logging()
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    models = model_sheets.prepare(cfg)      # adds any pages the model sheets need
    h = cfg["http"]
    fetcher = Fetcher(ROOT, h["delay_seconds"], h["timeout_seconds"], h["retries"],
                      h["cache_minutes"], use_cache=not (args.fresh or args.diagnose),
                      host_delays=h.get("host_delays"))
    if args.diagnose:
        return diagnose(fetcher, cfg)
    if args.tr_asof:
        return tr_backfill(fetcher, cfg, args.tr_asof)

    only = set(args.only.split(",")) if args.only else None
    src = cfg["sources"]
    want = lambda s: src.get(s, {}).get("enabled", False) and (only is None or s in only)  # noqa: E731
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    season_start = datetime.strptime(str(cfg["season"]["start_date"]), "%Y-%m-%d").date()
    early = (datetime.now().date() - season_start).days < int(cfg["season"].get("early_season_days", 28))
    log.info("=== NCAAM refresh %s ===%s", stamp, "  (early season: blank stats allowed)" if early else "")

    # ---------------------------------------------------------------- 1. FETCH
    raw: dict[str, pd.DataFrame] = {}
    fetch_errors: dict[str, str] = {}
    jobs = {
        "kenpom": lambda: kenpom.fetch(fetcher, src["kenpom"]),
        "teamrankings": lambda: teamrankings.fetch(fetcher, src["teamrankings"]),
        "espn": lambda: espn.fetch(fetcher, src["espn"], cfg["season"], ROOT),
    }
    table_source = {"KenPom": "kenpom", "TR_Stats": "teamrankings", "ESPN_Teams": "espn",
                    "ESPN_Games": "espn", "ESPN_Schedule": "espn"}
    table_source.update({r["name"]: "teamrankings" for r in src["teamrankings"].get("rankings", [])})
    warnings: dict[str, str] = {}
    for name, job in jobs.items():
        if not want(name):
            continue
        log.info("Fetching %s …", name)
        try:
            got = job()
            if "_warnings" in got:
                warnings[name] = "; ".join(got.pop("_warnings")["warning"])
            raw.update(got)
        except Exception as exc:
            fetch_errors[name] = str(exc)
            log.error("%s FAILED: %s", name, exc)

    # ---------------------------------------------------------------- 2. VALIDATE + FALLBACK
    last_good = output.LastGood(ROOT / cfg["output"]["last_good_folder"])
    tables: dict[str, pd.DataFrame] = {}
    status: list[dict] = []

    def settle(name: str, df: pd.DataFrame | None, extra_required=None, err: str | None = None):
        prev = last_good.load(name)
        source = table_source.get(name, "")
        if df is None and err is None and not want(source):          # skipped via --only / disabled
            if prev is not None:
                tables[name] = prev
                status.append({"Table": name, "Source": source, "Result": "SKIPPED – last good copy",
                               "Rows": len(prev), "Data_As_Of": last_good.saved_at(name), "Details": ""})
            return
        problems = [err] if err else validate.check(
            name, df, extra_required, len(prev) if prev is not None else None, early_season=early)
        if not problems:
            last_good.save(name, df)
            tables[name] = df
            note = warnings.get(source, "")
            status.append({"Table": name, "Source": source, "Result": "OK" + (" (with warnings)" if note else ""),
                           "Rows": len(df), "Data_As_Of": stamp, "Details": note})
        elif prev is not None:
            tables[name] = prev
            status.append({"Table": name, "Source": source, "Result": "FAILED – using last good",
                           "Rows": len(prev), "Data_As_Of": last_good.saved_at(name),
                           "Details": " | ".join(problems)})
            log.warning("%s: %s → kept last good copy from %s", name, problems, last_good.saved_at(name))
        else:
            status.append({"Table": name, "Source": source, "Result": "FAILED – no data",
                           "Rows": 0, "Data_As_Of": "", "Details": " | ".join(problems)})
            log.error("%s: %s (no earlier copy to fall back on)", name, problems)

    # ESPN team list first — it's the master list every other source is matched to
    def err_for(name: str) -> str | None:
        """Error text for a table that didn't arrive this run (None = it did, or was skipped)."""
        s = table_source.get(name, "")
        if name in raw or not want(s):
            return None
        return fetch_errors.get(s, "table missing from fetch results")

    settle("ESPN_Teams", raw.get("ESPN_Teams"), err=err_for("ESPN_Teams"))
    if "ESPN_Teams" not in tables:
        log.error("Can't match team names without the ESPN team list. Stopping.")
        return 2
    matcher = TeamMatcher(ROOT, tables["ESPN_Teams"])
    teams_df = tables["ESPN_Teams"].rename(columns={"ESPN_ID": "TeamKey"})
    teams_df.insert(1, "Team", teams_df["ESPN_Location"])
    tables["ESPN_Teams"] = teams_df

    tr = src.get("teamrankings", {})
    tr_cols = [f"{s['name']}_{c}" for s in tr.get("stats", []) for c in tr.get("keep_columns", ["Season"])]
    team_tables = [("KenPom", "kenpom", None), ("TR_Stats", "teamrankings", tr_cols)] + \
                  [(r["name"], "teamrankings", None) for r in tr.get("rankings", [])]

    for name, source, extra in team_tables:
        if not src.get(source, {}).get("enabled", False):
            continue
        if name in raw:
            try:
                settle(name, matcher.attach(raw[name], source), extra)
            except Exception as exc:
                settle(name, None, err=f"team matching: {exc}")
        else:
            settle(name, None, err=err_for(name))

    if src.get("espn", {}).get("enabled", False):
        for name in ("ESPN_Games", "ESPN_Schedule"):
            settle(name, raw.get(name), err=err_for(name))

    # ---------------------------------------------------------------- 3. MASTER TABLE
    tables["Master"] = build_master(tables)
    for sheet, cols in models.items():
        df, problems = model_sheets.build(tables["Master"], cols)
        tables[sheet] = df
        status.append({"Table": sheet, "Source": "model sheet",
                       "Result": "OK" if not problems else "CHECK – some columns blank",
                       "Rows": len(df), "Data_As_Of": stamp, "Details": " | ".join(problems)})
    matcher.save()
    xw = matcher.xwalk.copy()
    tables["Crosswalk"] = xw.sort_values(["match_type", "source", "source_name"])
    unmatched = matcher.unmatched_df()
    if not unmatched.empty:
        tables["_Unmatched"] = unmatched
        status.append({"Table": "_Unmatched", "Source": "", "Result": f"ACTION – {len(unmatched)} team names unmatched",
                       "Rows": len(unmatched), "Data_As_Of": stamp,
                       "Details": "Add them to data/team_aliases.csv and re-run"})
    if matcher.new_fuzzy:
        status.append({"Table": "Crosswalk", "Source": "", "Result": f"CHECK – {len(matcher.new_fuzzy)} new fuzzy matches",
                       "Rows": len(matcher.new_fuzzy), "Data_As_Of": stamp,
                       "Details": "; ".join(f"{r['source']}:{r['source_name']}→{r['Team']}" for r in matcher.new_fuzzy)})

    header = pd.DataFrame([{"Table": "LAST RUN", "Source": "", "Result": "", "Rows": None,
                            "Data_As_Of": stamp, "Details": f"log: logs/{logfile.name}"}])
    tables["_Status"] = pd.concat([header, pd.DataFrame(status)], ignore_index=True)

    # ---------------------------------------------------------------- 4. WRITE
    out = cfg["output"]
    output.write_workbook(ROOT / out["feed_workbook"], tables, first=list(models))
    output.write_csvs(ROOT / out["csv_folder"], tables)
    output.write_snapshot(ROOT / out["snapshot_folder"], tables, stamp)
    output.write_gretl(ROOT / "output" / "gretl", tables, list(models))

    failed = [s for s in status if s["Result"].startswith("FAILED")]
    log.info("Wrote %s (%d sheets). %s", out["feed_workbook"], len(tables),
             f"{len(failed)} table(s) FAILED — see _Status" if failed else "All tables OK.")
    if not unmatched.empty:
        log.warning("%d unmatched team names — see _Unmatched sheet:\n%s", len(unmatched), unmatched.to_string(index=False))
    return 1 if failed else 0


def build_master(t: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """One row per D-I team, every source side by side, keyed by TeamKey."""
    if "ESPN_Teams" not in t:
        return pd.DataFrame()
    m = t["ESPN_Teams"][["TeamKey", "Team", "ESPN_Name", "ESPN_Abbr"]].copy()
    prefixes = {"KenPom": "KP_", "TR_Stats": "TR_"}
    for name, df in t.items():
        if name in ("ESPN_Teams", "ESPN_Games", "ESPN_Schedule", "Master") or name.startswith("_") \
                or "TeamKey" not in df.columns:
            continue
        pfx = prefixes.get(name, name + "_")
        add = df.drop(columns=[c for c in ("Team",) if c in df.columns]).dropna(subset=["TeamKey"])
        add = add.rename(columns={c: c if c.startswith(pfx) or c == "TeamKey" else pfx + c for c in add.columns})
        m = m.merge(add, on="TeamKey", how="left")
    if "KP_KP_Rank" in m:
        m = m.rename(columns={"KP_KP_Rank": "KP_Rank"})
    sort_col = "KP_Rank" if "KP_Rank" in m else "Team"
    return m.sort_values(sort_col, na_position="last").reset_index(drop=True)


def diagnose(fetcher, cfg) -> int:
    """Hit one page per source and report what came back. Writes nothing."""
    src, ok = cfg["sources"], True
    checks = [
        ("KenPom front page", lambda: kenpom.parse(fetcher.get(src["kenpom"]["url"], "diag_kp"))),
        ("TeamRankings stat page", lambda: teamrankings.parse_stat(fetcher.get(
            f"{teamrankings.BASE}/stat/{src['teamrankings']['stats'][0]['slug']}", "diag_tr"))),
        ("ESPN team list", lambda: espn.parse_teams(__import__("json").loads(
            fetcher.get(f"{espn.API}/teams?groups=50&limit=500", "diag_espn")))),
    ]
    for label, fn in checks:
        try:
            df = fn()
            print(f"  ✔ {label}: {len(df)} rows, columns: {list(df.columns)[:8]}…")
            print("    first row:", df.iloc[0].to_dict() if len(df) else "(empty)")
        except Exception as exc:
            ok = False
            print(f"  ✘ {label}: {exc}")
    print("\nIf a site shows ✘ with HTTP 403: run `pip install curl_cffi`, or save the page from\n"
          "your browser (File ▸ Save Page As… ▸ 'Page Source') into data/manual_html/ using the\n"
          "file name shown in the error.")
    return 0 if ok else 1


def tr_backfill(fetcher, cfg, day: str) -> int:
    got = teamrankings.fetch(fetcher, cfg["sources"]["teamrankings"], as_of=day)
    folder = ROOT / cfg["output"]["snapshot_folder"] / f"{day}_tr_backfill"
    folder.mkdir(parents=True, exist_ok=True)
    for name, df in got.items():
        df.to_csv(folder / f"{name}.csv", index=False)
    log.info("TeamRankings as of %s saved to %s", day, folder)
    return 0


if __name__ == "__main__":
    sys.exit(main())
