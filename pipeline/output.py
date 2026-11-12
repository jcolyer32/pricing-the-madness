"""
Writing results: feed workbook, CSVs, dated snapshots, and the last-good store.
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

SHEET_ORDER = ["_Status", "Master", "KenPom", "TR_Stats", "TR_Predictive",
               "ESPN_Games", "ESPN_Schedule", "ESPN_Teams", "Crosswalk", "_Unmatched"]


class LastGood:
    """Keeps the most recent table that PASSED validation, per table name."""
    def __init__(self, folder: Path):
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)

    def load(self, name: str) -> pd.DataFrame | None:
        p = self.folder / f"{name}.pkl"
        return pd.read_pickle(p) if p.exists() else None

    def saved_at(self, name: str) -> str | None:
        p = self.folder / f"{name}.pkl"
        return datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M") if p.exists() else None

    def save(self, name: str, df: pd.DataFrame) -> None:
        df.to_pickle(self.folder / f"{name}.pkl")


def write_snapshot(folder: Path, tables: dict[str, pd.DataFrame], stamp: str) -> None:
    day = folder / stamp[:10]
    day.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        if not name.startswith("_") and name not in ("ESPN_Games",):   # game log is already cumulative
            df.to_csv(day / f"{name}.csv", index=False)


def write_csvs(folder: Path, tables: dict[str, pd.DataFrame]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df.to_csv(folder / f"{name}.csv", index=False)


def write_workbook(path: Path, tables: dict[str, pd.DataFrame], first: list[str] | None = None) -> None:
    """Write to a temp file then swap it in, so a crash never leaves a half-written
    workbook for Excel to read."""
    path.parent.mkdir(parents=True, exist_ok=True)
    wanted = SHEET_ORDER[:1] + list(first or []) + SHEET_ORDER[1:]
    order = [n for n in wanted if n in tables] + [n for n in tables if n not in wanted]
    fd, tmp = tempfile.mkstemp(suffix=".xlsx", dir=path.parent)
    os.close(fd)
    try:
        with pd.ExcelWriter(tmp, engine="openpyxl") as xw:
            for name in order:
                df = tables[name]
                df.to_excel(xw, sheet_name=name[:31], index=False)
                _format(xw.sheets[name[:31]], df, name)
        os.replace(tmp, path)
    except PermissionError as exc:
        os.remove(tmp)
        raise PermissionError(f"Can't write {path.name} — is it open in Excel? Close it "
                              "(your MODEL workbook can stay open).") from exc
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def _format(ws, df: pd.DataFrame, name: str) -> None:
    ws.freeze_panes = "A2"
    bold = Font(bold=True)
    for cell in ws[1]:
        cell.font = bold
    for i, col in enumerate(df.columns, start=1):
        width = max(len(str(col)), *(len(str(v)) for v in df[col].head(50))) if len(df) else len(str(col))
        ws.column_dimensions[get_column_letter(i)].width = min(max(width + 2, 8), 45)
    if name == "_Status" and "Result" in df.columns:
        red, green, amber = (PatternFill("solid", fgColor=c) for c in ("F8CBAD", "C6EFCE", "FFEB9C"))
        rcol = list(df.columns).index("Result") + 1
        for row in range(2, len(df) + 2):
            v = str(ws.cell(row=row, column=rcol).value or "")
            fill = green if v.startswith("OK") else red if v.startswith("FAILED") else amber
            ws.cell(row=row, column=rcol).fill = fill


# ---------------------------------------------------------------- gretl
def gretl_name(header: str) -> str:
    """'Effective FG %' -> 'Effective_FG_Pct', '3PM' -> 'x_3PM', 'OFF/DEF Diff' -> 'OFF_DEF_Diff'.
    gretl names: letters, digits, underscore; must start with a letter; max 31 chars."""
    import re
    s = str(header).replace("%", " Pct").replace("#", " Num").replace("+", " plus ")
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")
    if not s or not s[0].isalpha():
        s = "x_" + s
    return s[:31]


def write_gretl(folder: Path, tables: dict[str, pd.DataFrame], sheets: list[str]) -> None:
    """gretl-ready copies of the model sheets: output/gretl/ncaam_gretl.xlsx (one sheet per
    model) + one CSV each. Team is the first column so gretl uses it as observation labels."""
    folder.mkdir(parents=True, exist_ok=True)
    ready = {}
    for name in sheets:
        if name not in tables:
            continue
        df = tables[name].drop(columns=["TeamKey"], errors="ignore").copy()
        df = df[df["Team"].notna()].drop_duplicates("Team")
        new = [gretl_name(c) for c in df.columns]
        seen = {}
        for i, n in enumerate(new):                 # make duplicates unique
            seen[n] = seen.get(n, 0) + 1
            if seen[n] > 1:
                new[i] = f"{n[:28]}_{seen[n]}"
        df.columns = new
        ready[name] = df
        df.to_csv(folder / f"{name}.csv", index=False, na_rep="NA")
    if ready:
        fd, tmp = tempfile.mkstemp(suffix=".xlsx", dir=folder)
        os.close(fd)
        # gretl's reader chokes on files written by openpyxl ("no valid sheets");
        # xlsxwriter produces files laid out exactly like Excel's own.
        try:
            import xlsxwriter  # noqa: F401
            engine = "xlsxwriter"
        except ImportError:
            engine = "openpyxl"
        with pd.ExcelWriter(tmp, engine=engine) as xw:
            for name, df in ready.items():
                df.to_excel(xw, sheet_name=name[:31], index=False)
        os.replace(tmp, folder / "ncaam_gretl.xlsx")
        if engine == "openpyxl":
            import logging
            logging.getLogger(__name__).warning(
                "xlsxwriter not installed — gretl may not open ncaam_gretl.xlsx. "
                "Run: .venv/bin/pip install xlsxwriter   (the CSV works either way)")
