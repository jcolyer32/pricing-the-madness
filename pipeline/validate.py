"""
Sanity checks run on every table BEFORE it is allowed into your feed workbook.

If a table fails, the previous good copy is used instead and the _Status sheet
says so in red. This is what stops a site redesign or a half-loaded page from
quietly feeding garbage into your model.

Rules are plain data below — tighten or loosen them as you learn the data.
"""
from __future__ import annotations

import pandas as pd

# table -> rules. "ranges": column -> (min, max) that every non-blank value must fall in.
RULES: dict[str, dict] = {
    "KenPom": {
        "min_rows": 340, "max_rows": 380,
        "required": ["KP_Rank", "Team", "Conf", "NetRtg", "ORtg", "DRtg", "AdjT", "Luck"],
        "ranges": {"ORtg": (75, 140), "DRtg": (75, 140), "AdjT": (55, 80),
                   "NetRtg": (-45, 50), "Luck": (-0.3, 0.3),
                   "SOS_NetRtg": (-30, 30), "NCSOS_NetRtg": (-30, 30)},
        "max_blank_share": 0.02,
    },
    "TR_Stats": {"min_rows": 340, "max_rows": 380, "required": ["Team"],
                 "max_blank_share": 0.10},
    "TR_Predictive": {"min_rows": 340, "max_rows": 380, "required": ["Team", "Rating"]},
    "ESPN_Teams": {"min_rows": 340, "max_rows": 400, "required": ["ESPN_ID", "ESPN_Location"]},
    "ESPN_Games": {"min_rows": 0, "required": ["GameID", "GameDate", "Home_Team", "Away_Team",
                                               "Home_Score", "Away_Score"],
                   "ranges": {"Home_Score": (0, 200), "Away_Score": (0, 200)},
                   "unique": "GameID"},
    "ESPN_Schedule": {"min_rows": 0},
}


def check(name: str, df: pd.DataFrame, extra_required: list[str] | None = None,
          previous_rows: int | None = None, early_season: bool = False) -> list[str]:
    """Return a list of problems (empty list = passed)."""
    r = RULES.get(name, {})
    problems: list[str] = []

    if df is None:
        return ["no data returned"]
    n = len(df)
    if n < r.get("min_rows", 1):
        problems.append(f"only {n} rows (expected ≥ {r.get('min_rows', 1)})")
    if "max_rows" in r and n > r["max_rows"]:
        problems.append(f"{n} rows (expected ≤ {r['max_rows']}) — duplicated data?")

    required = list(r.get("required", [])) + list(extra_required or [])
    missing = [c for c in required if c not in df.columns]
    if missing:
        problems.append(f"missing columns {missing}")

    for col, (lo, hi) in r.get("ranges", {}).items():
        if col in df.columns:
            vals = pd.to_numeric(df[col], errors="coerce").dropna()
            bad = vals[(vals < lo) | (vals > hi)]
            if len(bad):
                problems.append(f"{len(bad)} values of {col} outside {lo}..{hi} "
                                f"(e.g. {bad.iloc[0]}) — columns shifted?")

    # In the first weeks of a season many teams haven't played yet, so blank stats are
    # expected — the blank-column rule only applies once the season is under way.
    if "max_blank_share" in r and n and not early_season:
        num = df.select_dtypes("number")
        if not num.empty:
            share = num.isna().mean().max()
            if share > r["max_blank_share"]:
                col = num.isna().mean().idxmax()
                problems.append(f"column {col} is {share:.0%} blank")

    ucol = r.get("unique", "TeamKey" if "TeamKey" in df.columns else None)
    if ucol and ucol in df.columns:
        d = df[ucol].dropna().duplicated().sum()
        if d:
            problems.append(f"{d} duplicate {ucol} values")

    # a sudden big drop vs last good run usually means a partial page load
    if previous_rows and n < 0.8 * previous_rows and name != "ESPN_Schedule":
        problems.append(f"row count fell from {previous_rows} to {n}")
    return problems
