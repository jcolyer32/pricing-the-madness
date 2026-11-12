#!/usr/bin/env python3
"""
"Pricing the Madness" — research data build.

    python run_research.py              # build everything (first run ~15 min, then ~1 min)
    python run_research.py --season 2025   # just one season
    python run_research.py --no-tr      # skip TeamRankings (ESPN data only)

Writes output/ncaam_research.xlsx (open in Excel) and output/gretl/research_*.csv
+ output/gretl/ncaam_research_gretl.xlsx (open in gretl).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from pipeline import output                                     # noqa: E402
from pipeline.http import Fetcher                                # noqa: E402
from pipeline.research import bracket as br                     # noqa: E402
from pipeline.research import data as rdata                     # noqa: E402
from pipeline.research import model as rmodel                   # noqa: E402
from pipeline.sources import espn, teamrankings                 # noqa: E402
from pipeline.teams import TeamMatcher                          # noqa: E402

log = logging.getLogger("research")
MODELS = {  # name -> (rating column, variance column or None)
    "M0 Strength, constant volatility": ("Rating", None),
    "M1 Strength + team volatility": ("Rating", "V"),
    "M2 Strength + style-predicted volatility": ("Rating", "V_Style"),
    "B1 TeamRankings rating, constant volatility": ("TR_Predictive_Rating", None),
    # M3: constant volatility widened for March by a factor k estimated ONLY from the
    # other seasons' tournaments (leave-one-season-out), so every score stays out of sample.
    "M3 Strength, tournament-widened volatility (LOSO)": ("Rating", "SCALE"),
}


def tournament_scale(ts_all, tg_all, holdout) -> float:
    """MLE of k in P(winner) = Phi((r_w - r_l) / (k * sigma)) on tournament games of
    every season EXCEPT `holdout`. k > 1 means games are more uncertain than ratings say."""
    if tg_all.empty:
        return 1.0
    g = tg_all[tg_all["Season"] != holdout]
    if g.empty:
        return 1.0
    z = []
    for season, d in g.groupby("Season"):
        s = ts_all[ts_all["Season"] == season]
        rw = d["Winner_ID"].map(s["Rating"]).astype(float)
        rl = d["Loser_ID"].map(s["Rating"]).astype(float)
        z.append(((rw - rl) / float(s["Sigma_Const"].iloc[0])).dropna().to_numpy())
    z = np.concatenate(z)
    grid = np.arange(0.70, 2.51, 0.005)
    ll = [np.log(np.clip(rmodel.phi(z / k), 1e-9, 1)).sum() for k in grid]
    return float(grid[int(np.argmax(ll))])


def setup_logging():
    (ROOT / "logs").mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not root.handlers:
        for h in (logging.StreamHandler(sys.stdout),
                  logging.FileHandler(ROOT / "logs" / f"research_{datetime.now():%Y-%m-%d}.log")):
            h.setFormatter(fmt)
            root.addHandler(h)


# ====================================================================== per season
def build_season(season: int, sc: dict, cfg: dict, fetcher, espn_teams, status: list):
    m = cfg["model"]
    start, ss, end = (rdata.as_date(sc[k]) for k in ("start", "selection_sunday", "end"))
    log.info("Season %d (Selection Sunday %s)", season, ss)
    games = rdata.collect_season(fetcher, ROOT, start, end)
    rounds = games["Note"].apply(rdata.tournament_round)
    games["Round"] = [r[0] for r in rounds]
    games["Round_Name"] = [r[1] for r in rounds]
    games["Region"] = [r[2] for r in rounds]
    games["Date_d"] = pd.to_datetime(games["Date"]).dt.date
    pre = games[(games["Date_d"] <= ss) & games["Round"].isna()]
    tour = games[games["Round"].notna()].copy()

    g = rmodel.d1_games(pre, m["min_games"])
    if len(g) < 1000:
        raise RuntimeError(f"only {len(g)} pre-tournament D-I games found — download incomplete?")
    rating, hca, pred, resid = rmodel.fit_ratings(g, m["ridge"], m["margin_cap"])
    V, V_raw, n_games, sigma_c = rmodel.fit_volatility(g, resid, m["shrink_games"])

    # H1: is volatility persistent? estimate it separately in each half of the season
    mid = g["Date_d"].sort_values().iloc[len(g) // 2]
    first = (g["Date_d"] <= mid).to_numpy()
    V1 = rmodel.fit_volatility(g[first], resid[first], m["shrink_games"])[0]
    V2 = rmodel.fit_volatility(g[~first], resid[~first], m["shrink_games"])[0]

    ts = rmodel.team_style(g)
    ts.insert(0, "Season", season)
    ts["Rating"] = rating
    ts["V"] = V
    ts["Volatility"] = np.sqrt(2 * V)
    ts["Volatility_Raw"] = np.sqrt(2 * V_raw)
    ts["Volatility_H1"] = np.sqrt(2 * V1)
    ts["Volatility_H2"] = np.sqrt(2 * V2)
    ts["Sigma_Const"] = sigma_c
    ts["HCA"] = hca
    in_tour = set(tour["H_ID"]) | set(tour["A_ID"])
    ts["In_Tournament"] = ts.index.isin(in_tour).astype(int)

    # ------------------------------------------------ TeamRankings as of Selection Sunday
    if cfg["teamrankings"].get("enabled") and espn_teams is not None:
        try:
            ts = ts.join(tr_as_of(fetcher, cfg["teamrankings"], ss, espn_teams), how="left")
        except Exception as exc:
            status.append(dict(Season=season, Step="TeamRankings", Result="FAILED – ESPN-only for this season",
                               Details=str(exc)[:300]))
            log.warning("TeamRankings %d failed: %s", season, exc)
    ts.index.name = "TeamID"

    # ------------------------------------------------ games table (for the heteroskedasticity tests)
    gt = g[["GameID", "Date", "H_ID", "H_Team", "A_ID", "A_Team", "Home", "Margin"]].copy()
    gt.insert(0, "Season", season)
    gt["Pred_Margin"] = pred
    gt["Resid"] = resid
    gt["Resid_Sq"] = resid ** 2
    gt["Abs_Resid"] = np.abs(resid)
    for c in ("ThreePA_Rate", "Pace", "FT_Rate"):
        gt[f"Sum_{c}"] = g["H_ID"].map(ts[c]).to_numpy() + g["A_ID"].map(ts[c]).to_numpy()
    gt["Game_Volatility"] = np.sqrt(g["H_ID"].map(V).to_numpy() + g["A_ID"].map(V).to_numpy())

    # ------------------------------------------------ tournament games
    tg = pd.DataFrame()
    if not tour.empty:
        tg = pd.DataFrame({
            "Season": season, "GameID": tour["GameID"], "Date": tour["Date"],
            "Round": tour["Round"].astype(int), "Round_Name": tour["Round_Name"], "Region": tour["Region"],
            "Team1_ID": tour["H_ID"], "Team2_ID": tour["A_ID"],
            "Team1_PTS": tour["H_PTS"], "Team2_PTS": tour["A_PTS"],
        })
        tg["Winner_ID"] = np.where(tg["Team1_PTS"] > tg["Team2_PTS"], tg["Team1_ID"], tg["Team2_ID"])
        tg["Loser_ID"] = np.where(tg["Team1_PTS"] > tg["Team2_PTS"], tg["Team2_ID"], tg["Team1_ID"])
        tg = tg.sort_values(["Round", "Date", "GameID"]).reset_index(drop=True)
        status.append(dict(Season=season, Step="Tournament games", Result="OK" if len(tg) == 67 else "CHECK",
                           Details=f"{len(tg)} games (a full tournament has 67)"))
    status.append(dict(Season=season, Step="Ratings & volatility", Result="OK",
                       Details=f"{len(g)} games, {len(rating)} teams, HCA {hca:.2f}, "
                               f"rating SD {rating.std():.1f}, game SD {sigma_c:.1f}"))
    return ts, gt, tg


def tr_as_of(fetcher, trcfg, ss: date, espn_teams) -> pd.DataFrame:
    store = ROOT / "data" / "research" / f"tr_{ss.isoformat()}.csv"
    if store.exists():
        return pd.read_csv(store, dtype={"TeamID": str}).set_index("TeamID")
    got = teamrankings.fetch(fetcher, trcfg, as_of=ss.isoformat())
    got.pop("_warnings", None)
    matcher = TeamMatcher(ROOT, espn_teams)
    out = None
    for name, df in got.items():
        d = matcher.attach(df, "teamrankings").dropna(subset=["TeamKey"])
        keep = [c for c in d.columns if c not in ("Team", "teamrankings_Name", "Record", "Rank")
                and pd.api.types.is_numeric_dtype(d[c])]
        if name != "TR_Stats":
            keep = [c for c in keep if c == "Rating"]
            d = d.rename(columns={"Rating": f"{name}_Rating"})
            keep = [f"{name}_Rating"]
        else:
            d = d.rename(columns={c: "TR_" + c for c in keep})
            keep = ["TR_" + c for c in keep]
        part = d.set_index("TeamKey")[keep]
        out = part if out is None else out.join(part, how="outer")
    matcher.save()
    out.index.name = "TeamID"
    out.to_csv(store)
    return out


# ====================================================================== tournament analysis
def tournament_table(tg: pd.DataFrame, ts: pd.DataFrame) -> pd.DataFrame:
    """One row per tournament game, oriented favourite vs underdog (favourite = higher Rating)."""
    rows = []
    for _, g in tg.iterrows():
        s = ts.loc[ts["Season"] == g["Season"]]
        a, b = g["Team1_ID"], g["Team2_ID"]
        if a not in s.index or b not in s.index:
            continue
        fav, dog = (a, b) if s.at[a, "Rating"] >= s.at[b, "Rating"] else (b, a)
        F, D = s.loc[fav], s.loc[dog]
        mu = F["Rating"] - D["Rating"]
        sig_t = np.sqrt(F["V"] + D["V"])
        sig_s = np.sqrt(F.get("V_Style", np.nan) + D.get("V_Style", np.nan))
        fav_pts = g["Team1_PTS"] if fav == a else g["Team2_PTS"]
        dog_pts = g["Team2_PTS"] if fav == a else g["Team1_PTS"]
        r = {
            "Season": g["Season"], "Date": g["Date"], "Round": g["Round"], "Round_Name": g["Round_Name"],
            "Region": g["Region"], "Fav": F["Team"], "Dog": D["Team"], "Fav_ID": fav, "Dog_ID": dog,
            "Upset": int(g["Winner_ID"] == dog), "Margin_Fav": fav_pts - dog_pts,
            "Exp_Margin": mu, "Resid": (fav_pts - dog_pts) - mu,
            "Sigma_Const": F["Sigma_Const"], "Game_Volatility": sig_t, "Game_Volatility_Style": sig_s,
            "Log_Game_Volatility": np.log(sig_t),
            "Z_Const": mu / F["Sigma_Const"], "Z_Team": mu / sig_t,
            "P_Upset_M0": float(rmodel.phi(-mu / F["Sigma_Const"])),
            "P_Upset_M1": float(rmodel.phi(-mu / sig_t)),
            "P_Upset_M2": float(rmodel.phi(-mu / sig_s)) if np.isfinite(sig_s) else np.nan,
            "Fav_Volatility": F["Volatility"], "Dog_Volatility": D["Volatility"],
        }
        for c in rmodel.STYLE_VARS + ["FT_Pct", "Close_Win_Pct"]:
            r[f"Fav_{c}"], r[f"Dog_{c}"] = F[c], D[c]
        r["Sum_ThreePA_Rate"] = F["ThreePA_Rate"] + D["ThreePA_Rate"]
        r["Avg_Pace"] = (F["Pace"] + D["Pace"]) / 2
        if "TR_Predictive_Rating" in s.columns:
            r["TR_Exp_Margin"] = F.get("TR_Predictive_Rating") - D.get("TR_Predictive_Rating")
            r["P_Upset_B1"] = float(rmodel.phi(-r["TR_Exp_Margin"] / F["Sigma_Const"])) \
                if pd.notna(r["TR_Exp_Margin"]) else np.nan
        for side, T in (("Fav", F), ("Dog", D)):
            if "Seed" in s.columns:
                r[f"{side}_Seed"] = T.get("Seed")
        rows.append(r)
    out = pd.DataFrame(rows)
    if "Fav_Seed" in out:
        out["Seed_Upset"] = (out["Dog_Seed"] < out["Fav_Seed"]).astype(int)
    return out


def run_brackets(seasons, ts_all, tg_all, cfg, status):
    adv_rows, score_rows = [], []
    nsim, seed = cfg["model"]["simulations"], cfg["model"]["seed"]
    for season in seasons:
        s = ts_all[ts_all["Season"] == season]
        tg = tg_all[tg_all["Season"] == season] if not tg_all.empty else pd.DataFrame()
        bfile = ROOT / "data" / "research" / f"bracket_{season}.csv"
        try:
            if len(tg) == 67:
                nodes, kind = br.tree_from_games(tg), "played"
            elif bfile.exists():
                nodes, kind = br.tree_from_bracket(read_bracket(bfile, s)), "bracket file"
            else:
                continue
        except Exception as exc:
            status.append(dict(Season=season, Step="Bracket", Result="FAILED", Details=str(exc)[:300]))
            continue
        frames = []
        for mname, (rcol, vcol) in MODELS.items():
            scale = 1.0
            if vcol == "SCALE":
                scale = tournament_scale(ts_all, tg_all, season)
                status.append(dict(Season=season, Step="M3 scale", Result="OK",
                                   Details=f"k = {scale:.3f} (estimated from the other seasons' tournaments)"))
                vcol = None
            if rcol not in s.columns or s[rcol].isna().all() or (vcol and (vcol not in s or s[vcol].isna().all())):
                continue
            rating = s[rcol].dropna().to_dict()
            var = s[vcol].dropna().to_dict() if vcol else None
            sig = float(s["Sigma_Const"].iloc[0]) * scale
            p = br.simulate(nodes, rating, var, sig, nsim, seed)
            p.columns = [f"{c}" for c in p.columns]
            p.insert(0, "Model", mname)
            frames.append(p)
            if kind == "played":
                act = br.actual_advancement(nodes, tg)
                sc = {"Season": season, "Model": mname}
                sc.update(br.score_games(br.game_probs(tg, rating, var, sig)))
                sc.update(br.score_advancement(p, act))
                champ = tg.loc[tg["Round"] == 6, "Winner_ID"].iat[0]
                sc["P_Actual_Champion"] = float(p.loc[champ, "P_Champion"]) if champ in p.index else np.nan
                score_rows.append(sc)
        if not frames:
            continue
        adv = pd.concat(frames).reset_index()
        adv.insert(0, "Season", season)
        adv.insert(2, "Team", adv["TeamID"].map(s["Team"]))
        adv = adv.merge(s[["Rating", "Volatility"]], left_on="TeamID", right_index=True, how="left")
        if kind == "played":
            adv = adv.merge(br.actual_advancement(nodes, tg), left_on="TeamID", right_index=True, how="left")
        adv_rows.append(adv)
        status.append(dict(Season=season, Step="Monte Carlo", Result="OK",
                           Details=f"{nsim:,} brackets × {len(frames)} models ({kind})"))
    adv = pd.concat(adv_rows, ignore_index=True) if adv_rows else pd.DataFrame()
    scores = pd.DataFrame(score_rows)
    if not scores.empty:
        overall = scores.groupby("Model").agg(
            Games=("Games", "sum"), Game_LogLoss=("Game_LogLoss", "mean"), Game_Brier=("Game_Brier", "mean"),
            Game_Accuracy=("Game_Accuracy", "mean"), Adv_Brier=("Adv_Brier", "mean"),
            Adv_LogLoss=("Adv_LogLoss", "mean"), P_Actual_Champion=("P_Actual_Champion", "mean")).reset_index()
        overall.insert(0, "Season", "ALL")
        scores = pd.concat([overall, scores], ignore_index=True)
    return adv, scores


def read_bracket(path: Path, s: pd.DataFrame) -> pd.DataFrame:
    """bracket_<season>.csv columns: Slot (1-64, bracket order), Team, Team2 (First Four opponent, optional).
    Team names as ESPN writes them (the Team column of Team_Season)."""
    b = pd.read_csv(path, dtype=str).fillna("")
    lookup = {str(n).lower(): i for i, n in s["Team"].items()}
    def find(name):
        if not name:
            return ""
        if name.lower() not in lookup:
            raise ValueError(f"{path.name}: team '{name}' not found in Team_Season for this season")
        return lookup[name.lower()]
    b["Slot"] = b["Slot"].astype(int)
    b["Team_ID"] = b["Team"].map(find)
    b["Team2_ID"] = b.get("Team2", pd.Series("", index=b.index)).map(find)
    if len(b) != 64:
        raise ValueError(f"{path.name}: needs 64 slots, found {len(b)}")
    return b


def calibration(adv: pd.DataFrame) -> pd.DataFrame:
    rows = []
    pairs = [(br.ADV_COLS[k], br.ADV_COLS[k].replace("P_", "Actual_")) for k in range(1, 7)]
    for mname, d in adv.groupby("Model"):
        if pairs[0][1] not in d:
            continue
        P = np.concatenate([d[p].to_numpy(float) for p, _ in pairs])
        A = np.concatenate([d[a].to_numpy(float) for _, a in pairs])
        bins = [0, .05, .1, .2, .3, .4, .5, .6, .7, .8, .9, 1.0001]
        cut = pd.cut(P, bins, right=False)
        g = pd.DataFrame({"P": P, "A": A, "bin": cut}).groupby("bin", observed=True)
        for b, x in g:
            rows.append({"Model": mname, "Prob_Bin": str(b), "Forecasts": len(x),
                         "Mean_Predicted": x["P"].mean(), "Observed_Rate": x["A"].mean()})
    return pd.DataFrame(rows)


# ====================================================================== main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season", type=int)
    ap.add_argument("--no-tr", action="store_true")
    ap.add_argument("--set", action="append", default=[], metavar="section.key=value",
                    help="override a research.yaml setting, e.g. --set model.margin_cap=25")
    ap.add_argument("--out", help="write results to this folder instead (robustness runs), "
                                  "e.g. output/robustness/cap25")
    args = ap.parse_args()
    setup_logging()
    cfg = yaml.safe_load((ROOT / "research.yaml").read_text())
    for item in args.set:
        key, val = item.split("=", 1)
        sec, k = key.split(".", 1)
        cfg[sec][k] = yaml.safe_load(val)
        log.info("override: %s = %s", key, cfg[sec][k])
    if args.out:
        cfg["output"]["workbook"] = f"{args.out}/ncaam_research.xlsx"
        cfg["output"]["gretl_folder"] = f"{args.out}/gretl"
    main_cfg = yaml.safe_load((ROOT / "config.yaml").read_text()) if (ROOT / "config.yaml").exists() else {}
    h = main_cfg.get("http", {"delay_seconds": 3, "timeout_seconds": 30, "retries": 3, "cache_minutes": 60})
    fetcher = Fetcher(ROOT, h["delay_seconds"], h["timeout_seconds"], h["retries"], h["cache_minutes"],
                      host_delays=h.get("host_delays", {"site.api.espn.com": 1}))
    if args.no_tr:
        cfg["teamrankings"]["enabled"] = False
    seasons = [args.season] if args.season else sorted(cfg["seasons"])
    status: list[dict] = []

    try:
        teams_json = json.loads(fetcher.get(f"{espn.API}/teams?groups=50&limit=500", key="espn_teams"))
        espn_teams = espn.parse_teams(teams_json)
    except Exception as exc:
        espn_teams = None
        status.append(dict(Season="all", Step="ESPN team list", Result="FAILED – TeamRankings skipped", Details=str(exc)))

    ts_list, gt_list, tg_list = [], [], []
    for season in seasons:
        try:
            ts, gt, tg = build_season(season, cfg["seasons"][season], cfg, fetcher, espn_teams, status)
            ts_list.append(ts); gt_list.append(gt); tg_list.append(tg)
        except Exception as exc:
            log.exception("Season %s failed", season)
            status.append(dict(Season=season, Step="Season build", Result="FAILED", Details=str(exc)[:300]))
    if not ts_list:
        log.error("No season built — see the log.")
        return 2
    ts_all = pd.concat(ts_list)
    ts_all = add_seeds(ts_all)
    ts_all["V_Style"] = rmodel.style_predicted_variance(ts_all.reset_index()).to_numpy() \
        if len(ts_all) > 50 else np.nan
    ts_all["Volatility_Style"] = np.sqrt(2 * ts_all["V_Style"])
    gt_all = pd.concat(gt_list, ignore_index=True)
    tg_all = pd.concat([t for t in tg_list if not t.empty], ignore_index=True) if any(
        not t.empty for t in tg_list) else pd.DataFrame()

    tour = tournament_table(tg_all, ts_all) if not tg_all.empty else pd.DataFrame()
    adv, scores = run_brackets(seasons, ts_all, tg_all, cfg, status)
    calib = calibration(adv) if not adv.empty else pd.DataFrame()

    team_out = ts_all.reset_index().rename(columns={"index": "TeamID"})
    front = ["Season", "TeamID", "Team", "In_Tournament", "Games", "Win_Pct", "Rating", "Volatility",
             "Volatility_Raw", "Volatility_H1", "Volatility_H2", "Volatility_Style"]
    team_out = team_out[[c for c in front if c in team_out] + [c for c in team_out if c not in front]]
    team_out = team_out.drop(columns=["V", "V_Style"], errors="ignore")
    if not tour.empty:
        tour = tour.drop(columns=["Fav_ID", "Dog_ID"])

    st = pd.DataFrame(status)
    st.insert(0, "Run", datetime.now().strftime("%Y-%m-%d %H:%M"))
    tables = {"_Status": st, "Guide": guide(), "Model_Scores": scores, "Calibration": calib,
              "Tourney_Games": tour, "Advancement": adv, "Team_Season": team_out, "Games": gt_all}
    tables = {k: (v.round(5) if isinstance(v, pd.DataFrame) else v) for k, v in tables.items()}
    out = cfg["output"]
    output.write_workbook(ROOT / out["workbook"], tables)
    write_gretl(ROOT / out["gretl_folder"], {"research_tourney_games": tour, "research_team_season": team_out,
                                             "research_games": gt_all, "research_advancement": adv})
    log.info("Wrote %s and gretl files. %d status lines (see _Status).", out["workbook"], len(st))
    return 1 if (st["Result"].astype(str).str.startswith("FAILED")).any() else 0


def add_seeds(ts: pd.DataFrame) -> pd.DataFrame:
    """Optional data/research/seeds.csv: Season, Team, Seed (Team as in Team_Season)."""
    f = ROOT / "data" / "research" / "seeds.csv"
    if not f.exists():
        return ts
    s = pd.read_csv(f)
    key = ts["Season"].astype(str) + "|" + ts["Team"].str.lower()
    seeds = dict(zip(s["Season"].astype(str) + "|" + s["Team"].str.lower(), s["Seed"]))
    ts["Seed"] = key.map(seeds)
    return ts


def write_gretl(folder: Path, tables: dict):
    folder.mkdir(parents=True, exist_ok=True)
    ready = {}
    for name, df in tables.items():
        if df is None or df.empty:
            continue
        d = df.copy()
        d.columns = [gretl_name(c) for c in d.columns]
        d = d.round(6)
        d.to_csv(folder / f"{name}.csv", index=False, na_rep="NA")
        ready[name] = d
    try:
        import xlsxwriter  # noqa: F401
        fd, tmp = tempfile.mkstemp(suffix=".xlsx", dir=folder)
        os.close(fd)
        with pd.ExcelWriter(tmp, engine="xlsxwriter") as xw:
            for name, d in ready.items():
                d.to_excel(xw, sheet_name=name.replace("research_", "")[:31], index=False)
        os.replace(tmp, folder / "ncaam_research_gretl.xlsx")
    except ImportError:
        log.warning("xlsxwriter missing — gretl workbook skipped (the CSVs work).")


def gretl_name(header: str) -> str:
    """Valid gretl variable name: letters/digits/_ , starts with a letter, max 31 chars."""
    import re
    t = str(header).replace("%", " Pct").replace("#", " Num")
    t = re.sub(r"[^A-Za-z0-9]+", "_", t).strip("_")
    if not t or not t[0].isalpha():
        t = "x_" + t
    return t[:31]


def guide() -> pd.DataFrame:
    rows = [
        ("Model_Scores", "", "Out-of-sample scores per model and season (ALL = average). Lower log loss / Brier = better."),
        ("Calibration", "", "Advancement forecasts grouped by predicted probability vs how often it happened."),
        ("Tourney_Games", "", "One row per NCAA tournament game, favourite vs underdog (favourite = higher Rating). H3 test."),
        ("Advancement", "", "Monte Carlo probability of each team reaching each round, per model. H4 test."),
        ("Team_Season", "", "One row per team per season, measured on Selection Sunday. H1 and H2 tests."),
        ("Games", "", "Every pre-tournament D-I game with its model residual. Heteroskedasticity tests."),
        ("Team_Season", "Rating", "Points better than an average D-I team on a neutral court (least squares on margins)."),
        ("Team_Season", "Volatility", "Team's game-to-game unpredictability, in points (SD of margin vs expectation)."),
        ("Team_Season", "Volatility_H1 / _H2", "Volatility estimated from the first / second half of the season (H1)."),
        ("Team_Season", "Volatility_Style", "Volatility predicted from playing style alone (H2 fitted values)."),
        ("Team_Season", "ThreePA_Rate", "Three-point attempts / field-goal attempts."),
        ("Team_Season", "FT_Rate", "Free-throw attempts / field-goal attempts."),
        ("Team_Season", "Pace", "Shot volume per game (FGA + 0.475 FTA, both teams averaged) — a pace proxy."),
        ("Team_Season", "Reb_Share", "Share of all rebounds in the team's games that it grabbed."),
        ("Team_Season", "Close_Win_Pct", "Win % in games decided by 5 points or fewer (luck check)."),
        ("Team_Season", "TR_*", "TeamRankings stats as of Selection Sunday (possessions, turnover rates, ...)."),
        ("Team_Season", "HCA / Sigma_Const", "Season home-court advantage and average game SD (points)."),
        ("Tourney_Games", "Upset", "1 if the lower-rated team won."),
        ("Tourney_Games", "Exp_Margin", "Favourite's Rating minus underdog's (expected margin, points)."),
        ("Tourney_Games", "Game_Volatility", "sqrt(vA + vB): expected SD of this game's margin, points."),
        ("Tourney_Games", "Z_Const / Z_Team", "Exp_Margin divided by constant / game-specific volatility."),
        ("Tourney_Games", "P_Upset_M0/M1/M2/B1", "Upset probability under each model (see Model_Scores for names)."),
        ("Advancement", "P_Reach_*", "Share of simulated brackets in which the team reached that round."),
        ("Advancement", "Actual_Reach_*", "1 if the team actually reached that round."),
    ]
    return pd.DataFrame(rows, columns=["Sheet", "Variable", "Meaning"])


if __name__ == "__main__":
    sys.exit(main())
