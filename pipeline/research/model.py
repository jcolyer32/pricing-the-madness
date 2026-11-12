"""
Stage 1 — strength:   margin = r_home - r_away + HCA*(not neutral) + e      (least squares, "Massey" ratings)
Stage 2 — volatility: E[e^2] = v_home + v_away                              (each team's share of game noise)
          team volatility = sqrt(2 v_i)  = the game SD if both teams were like team i
          game volatility = sqrt(v_A + v_B)
Everything is estimated from games on or before Selection Sunday only.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def phi(x):
    """Standard normal CDF (no scipy needed)."""
    x = np.asarray(x, dtype=float)
    return 0.5 * (1 + np.vectorize(math.erf)(x / math.sqrt(2)))


def d1_games(games: pd.DataFrame, min_games: int) -> pd.DataFrame:
    """Keep games where both teams are D-I regulars (non-D-I opponents play only a few D-I games)."""
    counts = pd.concat([games["H_ID"], games["A_ID"]]).value_counts()
    # non-D-I schools (e.g. NAIA teams on "guarantee game" road trips) play D-I teams almost
    # only away from home, so also require a few true home games
    home = games.loc[~games["Neutral"].astype(str).str.lower().eq("true"), "H_ID"].value_counts()
    keep = set(counts[counts >= min_games].index) & set(home[home >= 3].index)
    g = games[games["H_ID"].isin(keep) & games["A_ID"].isin(keep)].copy()
    g = g.dropna(subset=["H_PTS", "A_PTS"])
    g["Margin"] = g["H_PTS"] - g["A_PTS"]
    g["Home"] = (~g["Neutral"].astype(str).str.lower().eq("true")).astype(int)
    return g


def _design(g: pd.DataFrame, teams: list[str]):
    idx = {t: i for i, t in enumerate(teams)}
    n, k = len(g), len(teams)
    X = np.zeros((n, k))
    h = g["H_ID"].map(idx).to_numpy()
    a = g["A_ID"].map(idx).to_numpy()
    X[np.arange(n), h] = 1
    X[np.arange(n), a] = -1
    return X, h, a


def fit_ratings(g: pd.DataFrame, ridge: float = 0.5, margin_cap=None):
    teams = sorted(set(g["H_ID"]) | set(g["A_ID"]))
    X, h, a = _design(g, teams)
    y = g["Margin"].to_numpy(float)
    if margin_cap:
        y = np.clip(y, -margin_cap, margin_cap)
    Xf = np.column_stack([X, g["Home"].to_numpy(float)])
    k = Xf.shape[1]
    # ridge on team ratings only (not HCA) + sum-to-zero constraint row
    A = np.vstack([Xf, math.sqrt(ridge) * np.eye(k)[:-1], np.r_[np.ones(k - 1), 0] * 10])
    b = np.r_[y, np.zeros(k - 1), 0]
    coef, *_ = np.linalg.lstsq(A, b, rcond=None)
    ratings = pd.Series(coef[:-1], index=teams)
    hca = float(coef[-1])
    pred = X @ coef[:-1] + hca * g["Home"].to_numpy(float)
    resid = g["Margin"].to_numpy(float) - pred
    return ratings, hca, pred, resid


def fit_volatility(g: pd.DataFrame, resid: np.ndarray, shrink_games: float = 15):
    """Split each game's squared error into the two teams' contributions."""
    teams = sorted(set(g["H_ID"]) | set(g["A_ID"]))
    idx = {t: i for i, t in enumerate(teams)}
    n, k = len(g), len(teams)
    Z = np.zeros((n, k))
    Z[np.arange(n), g["H_ID"].map(idx).to_numpy()] = 1
    Z[np.arange(n), g["A_ID"].map(idx).to_numpy()] = 1
    e2 = resid ** 2
    v, *_ = np.linalg.lstsq(Z, e2, rcond=None)
    games_per = Z.sum(0)
    v_bar = e2.mean() / 2
    v = np.clip(v, 0.15 * v_bar, None)                         # a variance can't be negative
    w = games_per / (games_per + shrink_games)
    v_shrunk = w * v + (1 - w) * v_bar                          # pull small samples toward the average
    return (pd.Series(v_shrunk, index=teams), pd.Series(v, index=teams),
            pd.Series(games_per, index=teams), float(math.sqrt(e2.mean())))


def team_style(g: pd.DataFrame) -> pd.DataFrame:
    """Per-team style from box-score totals of games before Selection Sunday."""
    parts = []
    for me, op in (("H", "A"), ("A", "H")):
        d = pd.DataFrame({
            "TeamID": g[f"{me}_ID"], "Team": g[f"{me}_Team"],
            "FGA": g[f"{me}_FGA"], "3PA": g[f"{me}_3PA"], "3PM": g[f"{me}_3PM"],
            "FTA": g[f"{me}_FTA"], "FTM": g[f"{me}_FTM"], "REB": g[f"{me}_REB"], "PTS": g[f"{me}_PTS"],
            "oFGA": g[f"{op}_FGA"], "o3PA": g[f"{op}_3PA"], "o3PM": g[f"{op}_3PM"],
            "oFTA": g[f"{op}_FTA"], "oREB": g[f"{op}_REB"], "oPTS": g[f"{op}_PTS"],
            "Win": (g[f"{me}_PTS"] > g[f"{op}_PTS"]).astype(int),
            "Close": ((g[f"{me}_PTS"] - g[f"{op}_PTS"]).abs() <= 5).astype(int),
        })
        d["CloseWin"] = d["Close"] * d["Win"]
        parts.append(d)
    t = pd.concat(parts)
    s = t.groupby("TeamID").agg({c: "sum" for c in t.columns if c not in ("TeamID", "Team")})
    s["Team"] = t.groupby("TeamID")["Team"].agg(lambda x: x.mode().iat[0])
    s["Games"] = t.groupby("TeamID").size()
    out = pd.DataFrame(index=s.index)
    out["Team"] = s["Team"]
    out["Games"] = s["Games"]
    out["Win_Pct"] = s["Win"] / s["Games"]
    out["ThreePA_Rate"] = s["3PA"] / s["FGA"]                     # share of shots that are threes
    out["Opp_ThreePA_Rate"] = s["o3PA"] / s["oFGA"]
    out["Three_Pct"] = s["3PM"] / s["3PA"]
    out["Opp_Three_Pct"] = s["o3PM"] / s["o3PA"]
    out["FT_Rate"] = s["FTA"] / s["FGA"]                          # free-throw attempts per shot
    out["Opp_FT_Rate"] = s["oFTA"] / s["oFGA"]
    out["FT_Pct"] = s["FTM"] / s["FTA"]
    out["Pace"] = (s["FGA"] + 0.475 * s["FTA"] + s["oFGA"] + 0.475 * s["oFTA"]) / (2 * s["Games"])
    out["Reb_Share"] = s["REB"] / (s["REB"] + s["oREB"])
    out["Close_Games"] = s["Close"]
    out["Close_Win_Pct"] = np.where(s["Close"] > 0, s["CloseWin"] / s["Close"].clip(lower=1), np.nan)
    out["PPG"] = s["PTS"] / s["Games"]
    out["Opp_PPG"] = s["oPTS"] / s["Games"]
    return out


STYLE_VARS = ["ThreePA_Rate", "Opp_ThreePA_Rate", "FT_Rate", "Opp_FT_Rate", "Pace", "Three_Pct", "Reb_Share"]


def style_predicted_variance(team_season: pd.DataFrame) -> pd.Series:
    """Stage 2b: v_i explained by style (pooled OLS with season dummies). Returns fitted v."""
    d = team_season.dropna(subset=STYLE_VARS + ["V"]).copy()
    seasons = sorted(d["Season"].unique())
    X = np.column_stack([np.ones(len(d))]
                        + [d[c].to_numpy(float) for c in STYLE_VARS]
                        + [(d["Season"] == s).to_numpy(float) for s in seasons[1:]])
    beta, *_ = np.linalg.lstsq(X, d["V"].to_numpy(float), rcond=None)
    fitted = pd.Series(np.clip(X @ beta, d["V"].min(), None), index=d.index)
    return fitted.reindex(team_season.index)
