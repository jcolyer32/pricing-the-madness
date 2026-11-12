"""
Model sheets: small, hand-picked tables laid out exactly the way your model wants
them (your column headers, your units), defined in config.yaml under `model_sheets`.

Each column is one of:
    {header: X, stat: <teamrankings stat slug>, split: Season, scale: 1}
    {header: X, ranking: <teamrankings ranking slug>, field: Rating}
    {header: X, diff: [<header A>, <header B>]}        # A minus B
    {header: X, master: <any column name in Master>}    # e.g. KP_AdjT

Any stat/ranking page a model sheet needs is added to the TeamRankings fetch
automatically, so you only list it once.
"""
from __future__ import annotations

import re

import pandas as pd


def _auto_name(slug: str) -> str:
    return "M_" + re.sub(r"[^A-Za-z0-9]+", "_", slug).strip("_")


def prepare(cfg: dict) -> dict[str, list[dict]]:
    """Make sure every page a model sheet needs is fetched; resolve each column to a
    Master column name. Returns {sheet_name: [column specs with 'master' filled in]}."""
    sheets = cfg.get("model_sheets") or {}
    tr = cfg["sources"].setdefault("teamrankings", {})
    stats = tr.setdefault("stats", [])
    ranks = tr.setdefault("rankings", [])
    keep = tr.setdefault("keep_columns", ["Season"])

    resolved: dict[str, list[dict]] = {}
    for sheet, cols in sheets.items():
        out = []
        for c in cols:
            c = dict(c)
            if "stat" in c:
                entry = next((s for s in stats if s["slug"] == c["stat"]), None)
                if entry is None:
                    entry = {"slug": c["stat"], "name": _auto_name(c["stat"])}
                    stats.append(entry)
                split = c.get("split", "Season")
                if split not in keep:
                    keep.append(split)
                c["master"] = f"TR_{entry['name']}_{split}"
            elif "ranking" in c:
                entry = next((r for r in ranks if r["slug"] == c["ranking"]), None)
                if entry is None:
                    entry = {"slug": c["ranking"], "name": "TR" + _auto_name(c["ranking"])[1:]}
                    ranks.append(entry)
                c["master"] = f"{entry['name']}_{c.get('field', 'Rating')}"
            out.append(c)
        resolved[sheet] = out
    return resolved


def build(master: pd.DataFrame, cols: list[dict]) -> tuple[pd.DataFrame, list[str]]:
    """Return (sheet, problems)."""
    df = pd.DataFrame({"Team": master["Team"]})
    problems = []
    for c in cols:
        h = c["header"]
        if "diff" in c:
            a, b = c["diff"]
            df[h] = df[a] - df[b] if a in df and b in df else None
        elif c.get("master") in master.columns:
            df[h] = pd.to_numeric(master[c["master"]], errors="coerce") * float(c.get("scale", 1))
        else:
            df[h] = None
            problems.append(f"{h}: no data ({c.get('master')} missing — did that page fail?)")
            continue
        blank = df[h].isna().mean()
        if blank > 0.10:
            problems.append(f"{h}: {blank:.0%} of teams blank")
    num = df.columns.drop("Team")
    df[num] = df[num].apply(pd.to_numeric, errors="coerce").round(6)
    df["TeamKey"] = master["TeamKey"].values
    df = df.sort_values("Team").reset_index(drop=True)
    return df, problems
