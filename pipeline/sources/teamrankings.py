"""
TeamRankings stat pages and ranking pages.

Stat pages (…/ncaa-basketball/stat/<slug>) all share one layout:
    Rank | Team | 2026 | Last 3 | Last 1 | Home | Away | 2025
The year headers change every season, so we rename columns by POSITION to
stable names (Season, PrevSeason…) — your Excel formulas never break in October.

All stat pages are combined into ONE wide sheet, TR_Stats: one row per team,
columns like PPG_Season, PPG_Last3, eFG_Home …
"""
from __future__ import annotations

import logging
import re
from io import StringIO

import pandas as pd

log = logging.getLogger(__name__)
BASE = "https://www.teamrankings.com/ncaa-basketball"
STAT_COLS = ["Rank", "Team", "Season", "Last3", "Last1", "Home", "Away", "PrevSeason"]
EXPECTED_MIDDLE = ["last 3", "last 1", "home", "away"]   # used to detect layout changes


def _to_number(s: pd.Series) -> pd.Series:
    """'45.2%' -> 45.2, '--' -> blank, '1,234' -> 1234"""
    return pd.to_numeric(
        s.astype(str).str.replace(r"[%,+]", "", regex=True).str.strip()
         .replace({"--": None, "": None, "nan": None}),
        errors="coerce")


def _main_table(html: str) -> pd.DataFrame:
    tables = pd.read_html(StringIO(html))
    # the data table is the biggest one with a "Team" column
    cands = [t for t in tables if any(str(c).strip().lower() == "team" for c in t.columns)]
    if not cands:
        raise ValueError("no table with a 'Team' column")
    return max(cands, key=len)


def parse_stat(html: str) -> pd.DataFrame:
    df = _main_table(html)
    headers = [str(c).strip().lower() for c in df.columns]
    if len(df.columns) != len(STAT_COLS) or headers[3:7] != EXPECTED_MIDDLE:
        raise ValueError(f"unexpected stat-page columns {list(df.columns)} — layout changed?")
    df.columns = STAT_COLS
    df["Team"] = df["Team"].astype(str).str.strip()
    for c in STAT_COLS:
        if c != "Team":
            df[c] = _to_number(df[c])
    return df


def parse_ranking(html: str) -> pd.DataFrame:
    df = _main_table(html)
    df.columns = [re.sub(r"\W+", "_", str(c)).strip("_") or f"col{i}"
                  for i, c in enumerate(df.columns)]
    # "Michigan (37-3)" -> Team="Michigan", Record="37-3"
    parts = df["Team"].astype(str).str.extract(r"^(.*?)\s*\((\d+-\d+)\)\s*$")
    df["Record"] = parts[1]
    df["Team"] = parts[0].fillna(df["Team"].astype(str)).str.strip()
    for c in df.columns:
        if c not in ("Team", "Record"):
            conv = _to_number(df[c])
            if conv.notna().mean() > 0.8:        # mostly numbers -> treat as numeric
                df[c] = conv
    return df


def fetch(fetcher, cfg, as_of: str | None = None) -> dict[str, pd.DataFrame]:
    """as_of='2027-01-15' pulls the numbers as they stood on that date (backfill)."""
    q = f"?date={as_of}" if as_of else ""
    tag = f"_{as_of}" if as_of else ""
    keep = cfg.get("keep_columns", ["Season"])
    out: dict[str, pd.DataFrame] = {}
    errors: list[str] = []

    wide = None
    for stat in cfg.get("stats", []):
        slug, name = stat["slug"], stat["name"]
        try:
            html = fetcher.get(f"{BASE}/stat/{slug}{q}", key=f"tr_{slug}{tag}")
            df = parse_stat(html)[["Team"] + keep]
            df = df.rename(columns={c: f"{name}_{c}" for c in keep})
            wide = df if wide is None else wide.merge(df, on="Team", how="outer")
            log.info("  TeamRankings %-35s %d teams", slug, len(df))
        except Exception as exc:
            errors.append(f"{slug}: {exc}")
            log.error("  TeamRankings %s FAILED: %s", slug, exc)
    if wide is not None:
        out["TR_Stats"] = wide

    for rk in cfg.get("rankings", []):
        try:
            html = fetcher.get(f"{BASE}/ranking/{rk['slug']}{q}", key=f"tr_rank_{rk['slug']}{tag}")
            out[rk["name"]] = parse_ranking(html)
            log.info("  TeamRankings %-35s %d teams", rk["slug"], len(out[rk["name"]]))
        except Exception as exc:
            errors.append(f"{rk['slug']}: {exc}")
            log.error("  TeamRankings %s FAILED: %s", rk["slug"], exc)

    if errors and not out:
        raise RuntimeError("; ".join(errors))
    if errors:
        out["_warnings"] = pd.DataFrame({"warning": errors})   # partial success
    return out
