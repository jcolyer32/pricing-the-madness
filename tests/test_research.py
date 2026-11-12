"""
Offline test of the research pipeline on a SYNTHETIC league whose true ratings,
home-court advantage and team volatilities are known. Checks the models recover
them and that the bracket simulation is coherent.   python -m pytest tests/test_research.py -q
"""
from __future__ import annotations

import importlib
import json
import shutil
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RNG = np.random.default_rng(7)
N_TEAMS, HCA = 150, 3.2
TRUE_R = RNG.normal(0, 9, N_TEAMS)
THREE_RATE = RNG.uniform(0.30, 0.50, N_TEAMS)
TRUE_SD = 6 + 18 * (THREE_RATE - 0.30)          # per-team noise, rising with 3-point reliance
IDS = [str(1000 + i) for i in range(N_TEAMS)]
NAMES = [f"Team {i:03d}" for i in range(N_TEAMS)]
SS = date(2025, 3, 16)


def side(i, pts):
    fga = 58 + RNG.integers(-5, 6)
    tpa = int(round(fga * THREE_RATE[i]))
    fta = 20 + RNG.integers(-6, 7)
    stats = {"fieldGoalsAttempted": fga, "fieldGoalsMade": int(fga * .45),
             "threePointFieldGoalsAttempted": tpa, "threePointFieldGoalsMade": int(tpa * .34),
             "freeThrowsAttempted": fta, "freeThrowsMade": int(fta * .72), "rebounds": 35}
    return {"team": {"id": IDS[i], "location": NAMES[i]}, "score": str(int(pts)),
            "statistics": [{"name": k, "displayValue": str(v)} for k, v in stats.items()]}


def event(gid, h, a, neutral, note="", force_winner=None):
    mu = TRUE_R[h] - TRUE_R[a] + (0 if neutral else HCA)
    margin = mu + RNG.normal(0, np.sqrt((TRUE_SD[h] ** 2 + TRUE_SD[a] ** 2) / 2))
    if force_winner is not None:
        margin = abs(margin) + 1 if force_winner == h else -abs(margin) - 1
    base = 70
    hp, ap = base + margin / 2, base - margin / 2
    if round(hp) == round(ap):
        hp += 1
    hs, as_ = side(h, round(hp)), side(a, round(ap))
    hs["homeAway"], as_["homeAway"] = "home", "away"
    return {"id": str(gid), "season": {"type": 2},
            "competitions": [{"neutralSite": neutral, "conferenceCompetition": False,
                              "notes": [{"headline": note}] if note else [],
                              "status": {"type": {"completed": True, "name": "STATUS_FINAL"}},
                              "competitors": [hs, as_]}]}


def build_days():
    days, gid = {}, 1
    d = date(2024, 11, 1)
    while d <= SS:
        evs = []
        order = RNG.permutation(N_TEAMS)
        for k in range(0, 40, 2):
            evs.append(event(gid, order[k], order[k + 1], bool(RNG.random() < 0.1))); gid += 1
        days[d.strftime("%Y%m%d")] = evs
        d += timedelta(days=1)
    # tournament: top 68 by true rating; 4 First Four games among the last 8
    field = list(np.argsort(-TRUE_R)[:68])
    main, ff = field[:60], field[60:]
    ff_games = [(ff[0], ff[1]), (ff[2], ff[3]), (ff[4], ff[5]), (ff[6], ff[7])]
    regions = ["East", "West", "South", "Midwest"]
    tour_day = lambda n: (SS + timedelta(days=n)).strftime("%Y%m%d")
    ff_w = []
    for x, y in ff_games:
        e = event(gid, x, y, True, "Men's Basketball Championship - First Four"); gid += 1
        days.setdefault(tour_day(2), []).append(e)
        c = e["competitions"][0]["competitors"]
        ff_w.append(x if int(c[0]["score"]) > int(c[1]["score"]) else y)
    slots = main + ff_w                       # 64 teams
    RNG.shuffle(slots)
    names = {1: "1st Round", 2: "2nd Round", 3: "Sweet 16", 4: "Elite 8", 5: "Final Four", 6: "National Championship"}
    alive, rnd = slots, 1
    while len(alive) > 1:
        nxt = []
        for k in range(0, len(alive), 2):
            reg = regions[(k * 4) // len(alive)] if rnd <= 4 else None
            note = f"Men's Basketball Championship - {reg} Region - {names[rnd]}" if reg else \
                f"Men's Basketball Championship - {names[rnd]}"
            e = event(gid, alive[k], alive[k + 1], True, note); gid += 1
            days.setdefault(tour_day(3 + 3 * rnd), []).append(e)
            c = e["competitions"][0]["competitors"]
            nxt.append(alive[k] if int(c[0]["score"]) > int(c[1]["score"]) else alive[k + 1])
        alive, rnd = nxt, rnd + 1
    return days


DAYS = build_days()


class FakeFetcher:
    def __init__(self, *a, **k): pass

    def get(self, url, key=None):
        if "/teams" in url:
            return json.dumps({"sports": [{"leagues": [{"teams": [
                {"team": {"id": i, "location": n, "displayName": n, "shortDisplayName": n,
                          "name": "X", "abbreviation": n[:4]}} for i, n in zip(IDS, NAMES)]}]}]})
        ds = url.split("dates=")[1][:8]
        return json.dumps({"events": DAYS.get(ds, [])})


def test_end_to_end():
    tmp = Path(tempfile.mkdtemp()) / "proj"
    shutil.copytree(ROOT, tmp, ignore=shutil.ignore_patterns("output", "logs", "__pycache__", "research", ".venv", "data", "excel", "_to_delete", ".pytest_cache"))
    shutil.copytree(ROOT / "pipeline" / "research", tmp / "pipeline" / "research",
                    ignore=shutil.ignore_patterns("__pycache__"))
    (tmp / "research.yaml").write_text((ROOT / "research.yaml").read_text().replace(
        "  2022:", "  #2022:").replace("  2023:", "  #2023:").replace("  2024:", "  #2024:")
        .replace("  2026:", "  #2026:").replace("enabled: true", "enabled: false")
        .replace("simulations: 20000", "simulations: 4000"))
    sys.path.insert(0, str(tmp))
    for m in [m for m in sys.modules if m == "run_research" or m.startswith("pipeline")]:
        del sys.modules[m]
    run = importlib.import_module("run_research")
    run.ROOT = tmp
    run.Fetcher = FakeFetcher
    import pipeline.research.data as rd
    rd.date = type("D", (date,), {"today": classmethod(lambda c: date(2025, 6, 1))})
    sys.argv = ["run_research.py"]
    code = run.main()
    x = pd.read_excel(tmp / "output" / "ncaam_research.xlsx", sheet_name=None)
    print(x["_Status"].to_string())
    assert code == 0
    ts = x["Team_Season"]
    truth_r = dict(zip(NAMES, TRUE_R)); truth_sd = dict(zip(NAMES, TRUE_SD))
    r_corr = np.corrcoef(ts["Rating"], ts["Team"].map(truth_r))[0, 1]
    v_corr = np.corrcoef(ts["Volatility"], ts["Team"].map(truth_sd))[0, 1]
    print(f"rating corr {r_corr:.3f}  volatility corr {v_corr:.3f}  HCA {ts['HCA'].iat[0]:.2f}")
    assert r_corr > 0.95 and v_corr > 0.4 and abs(ts["HCA"].iat[0] - HCA) < 1.0
    h1 = np.corrcoef(ts["Volatility_H1"], ts["Volatility_H2"])[0, 1]
    print(f"H1 persistence corr {h1:.3f}")
    tg = x["Tourney_Games"]
    assert len(tg) == 67 and tg["Upset"].isin([0, 1]).all()
    adv = x["Advancement"]
    for m, d in adv.groupby("Model"):
        assert abs(d["P_Champion"].sum() - 1) < 1e-6, m
        assert abs(d["P_Reach_F4"].sum() - 4) < 1e-6, m
        assert d["Actual_Champion"].sum() == 1
    sc = x["Model_Scores"]
    print(sc[["Season", "Model", "Game_LogLoss", "Adv_Brier", "P_Actual_Champion"]].to_string())
    assert {"M0 Strength, constant volatility", "M1 Strength + team volatility"} <= set(sc["Model"])
    g = pd.read_csv(tmp / "output" / "gretl" / "research_tourney_games.csv")
    assert {"Upset", "Exp_Margin", "Game_Volatility", "Log_Game_Volatility"} <= set(g.columns)
    assert (tmp / "output" / "gretl" / "ncaam_research_gretl.xlsx").exists()


if __name__ == "__main__":
    test_end_to_end()
    print("PASS")
