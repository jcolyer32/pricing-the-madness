"""
ESPN's public JSON feed (the same data that powers espn.com scoreboards).

Produces:
  ESPN_Teams     — every D-I team with ESPN's id (used as the master team list)
  ESPN_Games     — every completed game this season, one row per game
  ESPN_Schedule  — upcoming games for the next N days (+ lines when available)

The game log is INCREMENTAL: finished days are saved in data/espn_games.csv and
only new days (plus the last couple, to catch late finals) are downloaded.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)
API = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball"


GAME_COLS = ["GameID", "GameDate", "StartUTC", "Status", "Completed", "Neutral", "ConfGame",
             "Note", "Venue", "Home_ESPN_ID", "Home_Team", "Home_Score", "Home_APRank",
             "Away_ESPN_ID", "Away_Team", "Away_Score", "Away_APRank", "Line", "Spread",
             "OverUnder", "Home_ML", "Away_ML", "Margin_Home", "Total"]


def _g(d, *path, default=None):
    """Safe nested get: _g(obj, 'a', 0, 'b') never throws."""
    for p in path:
        try:
            d = d[p]
        except (KeyError, IndexError, TypeError):
            return default
    return d


# ---------------------------------------------------------------- teams
def parse_teams(payload: dict) -> pd.DataFrame:
    teams = _g(payload, "sports", 0, "leagues", 0, "teams", default=[]) or []
    rows = []
    for t in teams:
        t = t.get("team", t)
        rows.append({
            "ESPN_ID": str(t.get("id")),
            "ESPN_Location": t.get("location"),        # "UConn", "Miami"
            "ESPN_Name": t.get("displayName"),         # "UConn Huskies"
            "ESPN_Short": t.get("shortDisplayName"),
            "ESPN_Nickname": t.get("name"),            # "Huskies"
            "ESPN_Abbr": t.get("abbreviation"),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- games
def parse_scoreboard(payload: dict, game_date: str) -> list[dict]:
    rows = []
    for ev in payload.get("events", []) or []:
        comp = _g(ev, "competitions", 0, default={}) or {}
        teams = {c.get("homeAway"): c for c in comp.get("competitors", [])}
        home, away = teams.get("home", {}), teams.get("away", {})
        odds = _g(comp, "odds", 0, default={}) or {}
        status = _g(comp, "status", "type", default={}) or _g(ev, "status", "type", default={}) or {}

        def side(c, pfx):
            score = pd.to_numeric(c.get("score"), errors="coerce")
            rank = _g(c, "curatedRank", "current")
            return {
                f"{pfx}_ESPN_ID": str(_g(c, "team", "id")),
                f"{pfx}_Team": _g(c, "team", "location"),
                f"{pfx}_Score": score,
                f"{pfx}_APRank": rank if rank and rank < 99 else None,
            }

        row = {
            "GameID": str(ev.get("id")),
            "GameDate": game_date,
            "StartUTC": ev.get("date"),
            "Status": status.get("name"),
            "Completed": bool(status.get("completed")),
            "Neutral": bool(comp.get("neutralSite")),
            "ConfGame": bool(comp.get("conferenceCompetition")),
            "Note": _g(comp, "notes", 0, "headline"),
            "Venue": _g(comp, "venue", "fullName"),
            **side(home, "Home"),
            **side(away, "Away"),
            "Line": odds.get("details"),            # e.g. "DUKE -7.5"
            "Spread": odds.get("spread"),
            "OverUnder": odds.get("overUnder"),
            "Home_ML": _g(odds, "homeTeamOdds", "moneyLine"),
            "Away_ML": _g(odds, "awayTeamOdds", "moneyLine"),
        }
        if row["Completed"] and pd.notna(row["Home_Score"]) and pd.notna(row["Away_Score"]):
            row["Margin_Home"] = row["Home_Score"] - row["Away_Score"]
            row["Total"] = row["Home_Score"] + row["Away_Score"]
        rows.append(row)
    return rows


def _scoreboard(fetcher, d: date) -> list[dict]:
    ds = d.strftime("%Y%m%d")
    url = f"{API}/scoreboard?dates={ds}&groups=50&limit=500"
    # cache key includes the date; today's/recent pages must not be cached long,
    # so the Fetcher's cache window (config) keeps them fresh.
    text = fetcher.get(url, key=f"espn_sb_{ds}")
    return parse_scoreboard(json.loads(text), d.isoformat())


def fetch(fetcher, cfg, season: dict, root: Path, today: date | None = None) -> dict[str, pd.DataFrame]:
    today = today or date.today()
    out: dict[str, pd.DataFrame] = {}

    # --- team list
    teams_json = json.loads(fetcher.get(f"{API}/teams?groups=50&limit=500", key="espn_teams"))
    out["ESPN_Teams"] = parse_teams(teams_json)
    log.info("  ESPN teams: %d", len(out["ESPN_Teams"]))

    # --- season game log (incremental)
    if cfg.get("game_log", True):
        store = root / "data" / "espn_games.csv"
        start = _as_date(season["start_date"])
        end = min(_as_date(season["end_date"]), today)
        old = pd.read_csv(store, dtype=str) if store.exists() else pd.DataFrame()
        if not old.empty:
            last = datetime.strptime(old["GameDate"].max(), "%Y-%m-%d").date()
            start = max(start, last - timedelta(days=int(cfg.get("recheck_days", 2))))
        new_rows = []
        d = start
        while d <= end:
            day_rows = _scoreboard(fetcher, d)
            new_rows += [r for r in day_rows if r["Completed"]]
            d += timedelta(days=1)
        new = pd.DataFrame(new_rows).astype(str) if new_rows else pd.DataFrame()
        games = pd.concat([old, new], ignore_index=True)
        if not games.empty:
            # newest copy of each game wins (fixes any score corrections)
            games = games.drop_duplicates("GameID", keep="last").sort_values(["GameDate", "GameID"])
            store.parent.mkdir(parents=True, exist_ok=True)
            games.to_csv(store, index=False)
            games = _retype(games)
        out["ESPN_Games"] = games if not games.empty else pd.DataFrame(columns=GAME_COLS)
        log.info("  ESPN games: %d total (%d fetched this run, days %s..%s)",
                 len(games), len(new), start, end)

    # --- upcoming schedule
    ahead = int(cfg.get("schedule_days_ahead", 0))
    if ahead:
        rows = []
        for i in range(ahead + 1):
            rows += [r for r in _scoreboard(fetcher, today + timedelta(days=i)) if not r["Completed"]]
        out["ESPN_Schedule"] = _retype(pd.DataFrame(rows)) if rows else pd.DataFrame(columns=GAME_COLS)
        log.info("  ESPN upcoming games: %d", len(rows))
    return out


def _as_date(x) -> date:
    return x if isinstance(x, date) else datetime.strptime(str(x), "%Y-%m-%d").date()


def _retype(df: pd.DataFrame) -> pd.DataFrame:
    """CSV round-trips make everything text; restore numbers/booleans."""
    df = df.replace({"nan": None, "None": None})
    for c in ["Home_Score", "Away_Score", "Home_APRank", "Away_APRank", "Spread",
              "OverUnder", "Home_ML", "Away_ML", "Margin_Home", "Total"]:
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in ["Completed", "Neutral", "ConfGame"]:
        if c in df:
            df[c] = df[c].astype(str).str.lower().eq("true")
    return df
