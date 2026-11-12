"""
Stage 4 — Monte Carlo bracket.

The bracket tree is rebuilt from the tournament games ESPN recorded (who played whom in
each round tells us which earlier games fed each slot), or, for an upcoming tournament,
read from data/research/bracket_<season>.csv. Each simulated game is won by team i over
team j with probability  Phi( (r_i - r_j) / sigma_ij ).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .model import phi

ROUND_NAMES = {0: "First Four", 1: "Round of 64", 2: "Round of 32", 3: "Sweet 16",
               4: "Elite 8", 5: "Final Four", 6: "Championship"}
ADV_COLS = {0: "P_Win_FirstFour", 1: "P_Reach_R32", 2: "P_Reach_S16", 3: "P_Reach_E8",
            4: "P_Reach_F4", 5: "P_Reach_Final", 6: "P_Champion"}


def tree_from_games(tg: pd.DataFrame) -> list[dict]:
    """tg: tournament games with Round, Team1_ID, Team2_ID, Winner_ID. Returns nodes in play order."""
    nodes, winner_of = [], {}           # winner_of[(round, team)] = node index
    for rnd in sorted(tg["Round"].unique()):
        for _, g in tg[tg["Round"] == rnd].iterrows():
            srcs = []
            for t in (g["Team1_ID"], g["Team2_ID"]):
                prev = winner_of.get((rnd - 1, t))
                if prev is None and rnd >= 2:
                    raise ValueError(f"bracket gap: {t} plays round {rnd} without a recorded round-{rnd - 1} win")
                srcs.append(("game", prev) if prev is not None else ("team", t))
            nodes.append({"round": int(rnd), "src": srcs, "game_id": g.get("GameID")})
            winner_of[(rnd, g["Winner_ID"])] = len(nodes) - 1
    return nodes


def tree_from_bracket(br: pd.DataFrame) -> list[dict]:
    """br: rows in bracket order (slot 1..64); optional Team2_ID = First Four opponent."""
    br = br.sort_values("Slot")
    nodes, level = [], []
    for _, r in br.iterrows():
        if isinstance(r.get("Team2_ID"), str) and r["Team2_ID"]:
            nodes.append({"round": 0, "src": [("team", r["Team_ID"]), ("team", r["Team2_ID"])]})
            level.append(("game", len(nodes) - 1))
        else:
            level.append(("team", r["Team_ID"]))
    rnd = 1
    while len(level) > 1:
        nxt = []
        for i in range(0, len(level), 2):
            nodes.append({"round": rnd, "src": [level[i], level[i + 1]]})
            nxt.append(("game", len(nodes) - 1))
        level, rnd = nxt, rnd + 1
    return nodes


def simulate(nodes, rating: dict, var: dict, sigma_const: float | None, n_sims: int, seed: int):
    """Returns DataFrame: TeamID x round -> P(win that round's game). var=None -> constant sigma."""
    rng = np.random.default_rng(seed)
    teams = sorted({s[1] for nd in nodes for s in nd["src"] if s[0] == "team"})
    ix = {t: i for i, t in enumerate(teams)}
    r = np.array([rating.get(t, np.nan) for t in teams], float)
    r = np.where(np.isnan(r), np.nanmin(r) if np.isfinite(np.nanmin(r)) else 0, r)  # unknown team = weakest
    v = None if var is None else np.array([var.get(t, np.nan) for t in teams], float)
    if v is not None:
        v = np.where(np.isnan(v), np.nanmean(v), v)
    wins = np.zeros((len(teams), 7))
    played = np.zeros((len(teams), 7), bool)
    out = []
    for nd in nodes:
        sides = [np.full(n_sims, ix[s[1]]) if s[0] == "team" else out[s[1]] for s in nd["src"]]
        a, b = sides
        sig = np.full(n_sims, sigma_const) if v is None else np.sqrt(v[a] + v[b])
        p_a = phi((r[a] - r[b]) / sig)
        w = np.where(rng.random(n_sims) < p_a, a, b)
        out.append(w)
        rd = nd["round"]
        np.add.at(wins[:, rd], w, 1)
        for s in (a, b):
            played[np.unique(s), rd] = True
    probs = wins / n_sims
    df = pd.DataFrame({ADV_COLS[k]: probs[:, k] for k in range(7)}, index=teams)
    for k in range(7):                       # a round a team could never reach stays blank only for First Four
        if k == 0:
            df.loc[~played[:, 0], ADV_COLS[0]] = np.nan
    df.index.name = "TeamID"
    return df


def actual_advancement(nodes, tg: pd.DataFrame) -> pd.DataFrame:
    teams = sorted({s[1] for nd in nodes for s in nd["src"] if s[0] == "team"})
    act = pd.DataFrame(0.0, index=teams, columns=[c.replace("P_", "Actual_") for c in ADV_COLS.values()])
    ff_teams = set(tg.loc[tg["Round"] == 0, "Team1_ID"]) | set(tg.loc[tg["Round"] == 0, "Team2_ID"])
    for _, g in tg.iterrows():
        act.loc[g["Winner_ID"], ADV_COLS[int(g["Round"])].replace("P_", "Actual_")] = 1.0
    act.loc[~act.index.isin(ff_teams), "Actual_Win_FirstFour"] = np.nan
    act.index.name = "TeamID"
    return act


def game_probs(tg: pd.DataFrame, rating: dict, var: dict | None, sigma_const: float) -> np.ndarray:
    """P(actual winner wins) for each played game under a model."""
    rw = tg["Winner_ID"].map(rating).astype(float)
    rl = tg["Loser_ID"].map(rating).astype(float)
    if var is None:
        sig = np.full(len(tg), sigma_const)
    else:
        sig = np.sqrt(tg["Winner_ID"].map(var).astype(float) + tg["Loser_ID"].map(var).astype(float))
    return phi(((rw - rl) / sig).to_numpy())


def score_games(p: np.ndarray) -> dict:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return {"Games": len(p), "Game_LogLoss": float(-np.mean(np.log(p))),
            "Game_Brier": float(np.mean((1 - p) ** 2)), "Game_Accuracy": float(np.mean(p > 0.5))}


def score_advancement(pred: pd.DataFrame, act: pd.DataFrame) -> dict:
    cols = [ADV_COLS[k] for k in range(1, 7)]
    P = pred[cols].to_numpy(float)
    A = act[[c.replace("P_", "Actual_") for c in cols]].to_numpy(float)
    Pc = np.clip(P, 1e-6, 1 - 1e-6)
    return {"Adv_Brier": float(np.mean((P - A) ** 2)),
            "Adv_LogLoss": float(-np.mean(A * np.log(Pc) + (1 - A) * np.log(1 - Pc)))}
