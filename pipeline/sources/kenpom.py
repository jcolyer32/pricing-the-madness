"""
KenPom front page (public ratings table) -> one row per team.

The table has 21 cells per row: most stats are followed by a small rank cell.
If KenPom ever changes its layout, the cell count changes and we STOP with a
clear error instead of silently putting numbers in the wrong columns.
"""
from __future__ import annotations

import re

import pandas as pd
from bs4 import BeautifulSoup

# Position -> column name, in the order the cells appear on the page.
COLUMNS = [
    "KP_Rank", "Team", "Conf", "W_L",
    "NetRtg",
    "ORtg", "ORtg_Rk",
    "DRtg", "DRtg_Rk",
    "AdjT", "AdjT_Rk",
    "Luck", "Luck_Rk",
    "SOS_NetRtg", "SOS_NetRtg_Rk",
    "SOS_ORtg", "SOS_ORtg_Rk",
    "SOS_DRtg", "SOS_DRtg_Rk",
    "NCSOS_NetRtg", "NCSOS_NetRtg_Rk",
]
TEXT_COLS = {"Team", "Conf", "W_L"}


def parse(html: str) -> pd.DataFrame:
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", id="ratings-table") or soup.find("table")
    if table is None:
        raise ValueError("KenPom: ratings table not found on page")

    rows, bad_widths = [], set()
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if not tds:                       # header rows use <th>
            continue
        if len(tds) != len(COLUMNS):
            bad_widths.add(len(tds))
            continue
        cells = []
        for i, td in enumerate(tds):
            if i == 1:                    # team cell: "<a>Duke</a> <span class=seed>1</span>"
                a = td.find("a")
                name = a.get_text(strip=True) if a else td.get_text(" ", strip=True)
                name = re.sub(r"\s+\d+$", "", name)   # drop tournament seed if no <a>
                cells.append(name)
            else:
                cells.append(td.get_text(strip=True))
        rows.append(cells)

    if not rows:
        raise ValueError(
            f"KenPom: no rows with {len(COLUMNS)} cells (saw widths {sorted(bad_widths)}). "
            "The page layout probably changed — update COLUMNS in sources/kenpom.py.")

    df = pd.DataFrame(rows, columns=COLUMNS)
    for c in df.columns:
        if c not in TEXT_COLS:
            df[c] = pd.to_numeric(df[c].str.replace("+", "", regex=False), errors="coerce")
    # Before a team's first game KenPom shows schedule-strength placeholders like -99;
    # treat those as "no data yet" rather than a real rating.
    for c in [c for c in df.columns if c.startswith(("SOS_", "NCSOS_")) and not c.endswith("_Rk")]:
        df.loc[df[c] <= -90, c] = float("nan")
    wl = df["W_L"].str.extract(r"(\d+)-(\d+)").astype("Int64")
    df.insert(4, "Wins", wl[0])
    df.insert(5, "Losses", wl[1])
    return df


def fetch(fetcher, cfg) -> dict[str, pd.DataFrame]:
    html = fetcher.get(cfg["url"], key="kenpom_home")
    return {"KenPom": parse(html)}
