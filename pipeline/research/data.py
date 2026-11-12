"""
Collect every D-I game (scores + box-score totals) from ESPN's public feed, one
day at a time, for each research season.

Finished days are saved to data/research/espn_days/<YYYYMMDD>.csv and never
downloaded again, so only the first run is slow.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)
API = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball"

STAT_KEYS = {  # ESPN statistic name -> our column suffix
    "fieldGoalsAttempted": "FGA", "fieldGoalsMade": "FGM",
    "threePointFieldGoalsAttempted": "3PA", "threePointFieldGoalsMade": "3PM",
    "freeThrowsAttempted": "FTA", "freeThrowsMade": "FTM", "rebounds": "REB",
}
ROUNDS = [  # (text in ESPN's note, round number, round name)
    ("first four", 0, "First Four"), ("1st round", 1, "Round of 64"),
    ("2nd round", 2, "Round of 32"), ("sweet 16", 3, "Sweet 16"),
    ("elite 8", 4, "Elite 8"), ("final four", 5, "Final Four"),
    ("national championship", 6, "Championship"),
]
COLUMNS = (["GameID", "Date", "SeasonType", "Neutral", "ConfGame", "Note", "Completed"]
           + [f"{s}_{c}" for s in ("H", "A") for c in ["ID", "Team", "PTS"] + list(STAT_KEYS.values())])


def _g(d, *path, default=None):
    for p in path:
        try:
            d = d[p]
        except (KeyError, IndexError, TypeError):
            return default
    return d


def parse_day(payload: dict, day: str) -> pd.DataFrame:
    rows = []
    for ev in payload.get("events", []) or []:
        comp = _g(ev, "competitions", 0, default={}) or {}
        sides = {c.get("homeAway"): c for c in comp.get("competitors", [])}
        if "home" not in sides or "away" not in sides:
            continue
        row = {
            "GameID": str(ev.get("id")), "Date": day,
            "SeasonType": _g(ev, "season", "type"),
            "Neutral": bool(comp.get("neutralSite")),
            "ConfGame": bool(comp.get("conferenceCompetition")),
            "Note": _g(comp, "notes", 0, "headline") or "",
            "Completed": bool(_g(comp, "status", "type", "completed")),
        }
        for tag, side in (("H", sides["home"]), ("A", sides["away"])):
            row[f"{tag}_ID"] = str(_g(side, "team", "id"))
            row[f"{tag}_Team"] = _g(side, "team", "location")
            row[f"{tag}_PTS"] = pd.to_numeric(side.get("score"), errors="coerce")
            stats = {s.get("name"): s.get("displayValue") for s in side.get("statistics", []) or []}
            for k, col in STAT_KEYS.items():
                row[f"{tag}_{col}"] = pd.to_numeric(stats.get(k), errors="coerce")
        rows.append(row)
    return pd.DataFrame(rows, columns=COLUMNS)


def tournament_round(note: str) -> tuple[int | None, str | None, str | None]:
    """ESPN note -> (round number, round name, region) for NCAA tournament games."""
    note = note if isinstance(note, str) else ""     # blank notes come back from CSV as NaN
    n = note.lower()
    if "men's basketball championship" not in n:
        return None, None, None
    region = re.search(r"-\s*([A-Za-z]+) Region", note or "")
    for key, num, name in ROUNDS:
        if key in n:
            return num, name, region.group(1) if region else None
    return None, None, None


def collect_season(fetcher, root: Path, start: date, end: date, today: date | None = None) -> pd.DataFrame:
    today = today or date.today()
    store = root / "data" / "research" / "espn_days"
    store.mkdir(parents=True, exist_ok=True)
    frames, fetched, d = [], 0, start
    last = min(end, today)
    while d <= last:
        ds = d.strftime("%Y%m%d")
        f = store / f"{ds}.csv"
        if f.exists() and (today - d).days > 3:          # finished days never change
            frames.append(pd.read_csv(f, dtype={"GameID": str, "H_ID": str, "A_ID": str, "Note": str},
                                      keep_default_na=False, na_values={c: [""] for c in COLUMNS if c != "Note"}))
        else:
            text = fetcher.get(f"{API}/scoreboard?dates={ds}&groups=50&limit=500", key=f"rs_{ds}")
            day = parse_day(json.loads(text), d.isoformat())
            day.to_csv(f, index=False)
            frames.append(day)
            fetched += 1
        d += timedelta(days=1)
    games = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLUMNS)
    for c in COLUMNS:                                   # numbers back to numbers after CSV round-trip
        if c.endswith(("_PTS",) + tuple("_" + v for v in STAT_KEYS.values())):
            games[c] = pd.to_numeric(games[c], errors="coerce")
    games = games[games["Completed"].astype(str).str.lower().eq("true")]
    games = games.drop_duplicates("GameID", keep="last")
    log.info("  %s..%s: %d games (%d days downloaded, rest from disk)", start, last, len(games), fetched)
    return games


def as_date(x) -> date:
    return x if isinstance(x, date) else datetime.strptime(str(x), "%Y-%m-%d").date()
