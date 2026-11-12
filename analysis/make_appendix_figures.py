"""Extra appendix figures: model fit, residuals, persistence, Monte Carlo.
Run from the repo root:  python analysis/make_appendix_figures.py"""
import math, sys, warnings
from pathlib import Path
import numpy as np, pandas as pd, matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt
warnings.filterwarnings("ignore")
sys.path.insert(0, ".")
from pipeline.research import bracket as br

OUT = Path("results/exhibits")
INK, MUTED, GRID, AXIS, BG = "#111111", "#5f5e57", "#e1e0d9", "#c3c2b7", "#ffffff"
BLUE, ORANGE = "#2a78d6", "#eb6834"
plt.rcParams.update({"font.family": "serif", "font.serif": ["Times New Roman", "Times", "DejaVu Serif"],
                     "font.size": 10, "axes.edgecolor": AXIS, "savefig.facecolor": BG})
Phi = np.vectorize(lambda z: 0.5 * (1 + math.erf(z / math.sqrt(2))))
def style(ax, grid="both"):
    for s in ("top", "right"): ax.spines[s].set_visible(False)
    ax.grid(axis=grid, color=GRID, lw=0.6); ax.set_axisbelow(True); ax.tick_params(colors=MUTED)
import textwrap
def note(fig, s):
    w = int(fig.get_figwidth() * 15.5)
    fig.text(0.01, 0.012, textwrap.fill(s, w), fontsize=7.4, color=MUTED, fontstyle="italic", va="bottom")
def save(fig, name, title):
    fig.suptitle(title, x=0.01, ha="left", fontsize=11.5, fontweight="bold")
    fig.tight_layout(rect=(0, 0.075, 1, 0.94)); fig.savefig(OUT / name, dpi=220); plt.close(fig); print("wrote", name)

games = pd.read_csv("output/gretl/research_games.csv")
ts = pd.read_csv("output/gretl/research_team_season.csv")
tg = pd.read_csv("output/gretl/research_tourney_games.csv")
adv = pd.read_csv("output/gretl/research_advancement.csv")

# ---------------- A2: Stage 1 fit (predicted vs actual margins, residual distribution, Q-Q)
fig, ax = plt.subplots(1, 3, figsize=(11, 3.9), dpi=220)
hb = ax[0].hexbin(games.Pred_Margin, games.Margin, gridsize=45, cmap="Blues", mincnt=1, linewidths=0)
lim = [-50, 60]; ax[0].plot(lim, lim, color=INK, lw=1, ls=(0, (4, 3)))
r2 = 1 - games.Resid.var() / games.Margin.var()
ax[0].set(xlim=lim, ylim=lim, xlabel="Predicted margin (pts)", ylabel="Actual margin (pts)")
ax[0].set_title(f"Fitted vs. actual (R² = {r2:.2f})", loc="left", fontsize=10, fontweight="bold"); style(ax[0])
e = games.Resid; sd = e.std()
ax[1].hist(e, bins=60, density=True, color=BLUE, alpha=0.85, edgecolor=BG, lw=0.4)
g = np.linspace(-45, 45, 300); ax[1].plot(g, np.exp(-g**2/(2*sd**2))/(sd*math.sqrt(2*math.pi)), color=INK, lw=1.3, label=f"Normal, SD {sd:.1f}")
ax[1].set(xlim=(-45, 45), ylim=(0, 0.05), xlabel="Residual (pts)", ylabel="Density"); ax[1].legend(frameon=False, fontsize=8.5, loc="upper left")
ax[1].set_title("Residuals are close to normal", loc="left", fontsize=10, fontweight="bold"); style(ax[1], "y")
q = np.sort(e.sample(4000, random_state=1).to_numpy()); n = len(q)
from statistics import NormalDist
th = np.array([NormalDist().inv_cdf((i + 0.5) / n) for i in range(n)]) * sd
ax[2].scatter(th, q, s=4, color=BLUE, alpha=0.5, linewidths=0); ax[2].plot([-40, 40], [-40, 40], color=INK, lw=1, ls=(0, (4, 3)))
ax[2].set(xlim=(-40, 40), ylim=(-50, 50), xlabel="Normal quantile (pts)", ylabel="Residual quantile (pts)")
ax[2].set_title("Q–Q plot: tails track the normal", loc="left", fontsize=10, fontweight="bold"); style(ax[2])
note(fig, f"{len(games):,} regular season D-I games before Selection Sunday, 2022–2026. Stage 1 least-squares ratings with home court. Q–Q plot uses a random 4,000-game sample.")
save(fig, "FigureA2_rating_model_fit.png", "Figure A2. The strength model fits regular season margins, and residuals are close to normal")

# ---------------- A3: H1 persistence
fig, ax = plt.subplots(figsize=(6.2, 4.8), dpi=220)
d = ts.dropna(subset=["Volatility_H1", "Volatility_H2"])
ax.scatter(d.Volatility_H1, d.Volatility_H2, s=8, color=BLUE, alpha=0.35, linewidths=0)
b = np.polyfit(d.Volatility_H1, d.Volatility_H2, 1); xx = np.linspace(d.Volatility_H1.min(), d.Volatility_H1.max(), 50)
ax.plot(xx, np.polyval(b, xx), color=ORANGE, lw=2, label=f"OLS fit, slope {b[0]:.2f}")
mn, mx = 6.5, 18.5; ax.plot([mn, mx], [mn, mx], color=INK, lw=1, ls=(0, (4, 3)), label="Perfect persistence")
r = d.Volatility_H1.corr(d.Volatility_H2)
ax.set(xlim=(mn, mx), ylim=(mn, mx), xlabel="Volatility, first half of season (pts)", ylabel="Volatility, second half of season (pts)")
ax.legend(frameon=False, fontsize=8.5, loc="upper left"); style(ax)
note(fig, f"{len(d):,} team seasons. r = {r:.3f}; Spearman–Brown full-season reliability ≈ {2*r/(1+r):.2f}.")
save(fig, "FigureA3_H1_persistence.png", "Figure A3. Team volatility barely carries over\nfrom one half of the season to the next (H1)")

# ---------------- A4: H2 fitted vs actual and residuals vs fitted (preferred log model)
H2 = ["ThreePA_Rate", "Opp_ThreePA_Rate", "FT_Rate", "TR_TOV_Poss_Season", "TR_OppTOV_Poss_Season", "Pace", "Rating"]
d = ts.dropna(subset=H2 + ["Volatility"]).copy(); d["sq"] = d.Rating ** 2
yrs = sorted(d.Season.unique())
X = np.column_stack([np.ones(len(d))] + [d[c] for c in H2 + ["sq"]] + [(d.Season == s).astype(float) for s in yrs[1:]])
y = np.log(d.Volatility.to_numpy()); bb = np.linalg.lstsq(X, y, rcond=None)[0]; fit = X @ bb; res = y - fit
fig, ax = plt.subplots(1, 2, figsize=(10, 4.2), dpi=220)
ax[0].scatter(np.exp(fit), d.Volatility, s=7, color=BLUE, alpha=0.35, linewidths=0)
ax[0].plot([8.8, 12], [8.8, 12], color=INK, lw=1, ls=(0, (4, 3)))
ax[0].set(xlabel="Fitted volatility from style (pts)", ylabel="Measured volatility (pts)")
ax[0].set_title("Fitted vs. measured: style explains a narrow band", loc="left", fontsize=10, fontweight="bold"); style(ax[0])
ax[1].scatter(fit, res, s=7, color=BLUE, alpha=0.35, linewidths=0); ax[1].axhline(0, color=INK, lw=1)
bins = pd.qcut(fit, 12); m = pd.Series(res).groupby(bins, observed=True).mean(); c = pd.Series(fit).groupby(bins, observed=True).mean()
ax[1].plot(c, m, color=ORANGE, lw=2, marker="o", ms=4, label="Binned mean residual")
ax[1].set(xlabel="Fitted ln(volatility)", ylabel="Residual"); ax[1].legend(frameon=False, fontsize=8.5)
ax[1].set_title("Residuals vs. fitted: no pattern left", loc="left", fontsize=10, fontweight="bold"); style(ax[1])
note(fig, "Preferred H2 specification (Table 2, column 3): ln(volatility) on style, rating, rating² and season effects; 1,812 team seasons.")
save(fig, "FigureA4_H2_fit_and_residuals.png", "Figure A4. H2 model fit and residual check")

# ---------------- A5: Monte Carlo title odds per season (M0), actual champion highlighted
m0 = adv[adv.Model.str.startswith("M0")]
fig, axes = plt.subplots(1, 5, figsize=(12.5, 3.9), dpi=220, sharex=True)
for ax, (s, dd) in zip(axes, m0.groupby("Season")):
    top = dd.sort_values("P_Champion", ascending=False).head(8).iloc[::-1]
    cols = [ORANGE if a == 1 else BLUE for a in top.Actual_Champion]
    ax.barh(top.Team, top.P_Champion * 100, color=cols, height=0.7)
    champ = dd[dd.Actual_Champion == 1].iloc[0]
    if champ.Team not in set(top.Team):
        ax.text(0.98, 0.02, f"Champion {champ.Team}: {champ.P_Champion:.0%}", transform=ax.transAxes, ha="right", fontsize=7.5, color=ORANGE)
    ax.set_title(f"{int(s)}", loc="left", fontsize=10, fontweight="bold"); ax.tick_params(axis="y", labelsize=8)
    ax.set_xlabel("P(title), %"); ax.set_xticks([0, 10, 20, 30]); style(ax, "x")
note(fig, "Model M0 (strength, constant σ), 20,000 simulated brackets per season using only Selection Sunday information. Orange = actual champion.")
save(fig, "FigureA5_montecarlo_title_odds.png", "Figure A5. Monte Carlo title odds before each tournament; the champion was a top-6 favorite every year")

# ---------------- A6: Monte Carlo convergence (2026, M0)
s = 2026; sts = ts[ts.Season == s].set_index("TeamID"); name2id = {n: i for i, n in sts.Team.items()}
t = tg[tg.Season == s].copy()
t["Team1_ID"] = t.Fav.map(name2id); t["Team2_ID"] = t.Dog.map(name2id)
t["Winner_ID"] = np.where(t.Upset == 1, t.Team2_ID, t.Team1_ID)
t = t.sort_values(["Round", "Date"]).reset_index(drop=True)
nodes = br.tree_from_games(t)
rating = sts.Rating.to_dict(); sig = float(sts.Sigma_Const.iloc[0])
big = br.simulate(nodes, rating, None, sig, 200000, 7)
leader = big.P_Champion.idxmax(); p_true = big.at[leader, "P_Champion"]
Ns = [250, 500, 1000, 2500, 5000, 10000, 20000, 40000]; rows = []
for n in Ns:
    est = [br.simulate(nodes, rating, None, sig, n, 100 + k).at[leader, "P_Champion"] for k in range(25)]
    rows.append((n, np.mean(est), np.min(est), np.max(est)))
r = pd.DataFrame(rows, columns=["n", "mean", "lo", "hi"])
fig, ax = plt.subplots(figsize=(6.6, 4.4), dpi=220)
ax.fill_between(r.n, r.lo, r.hi, color=BLUE, alpha=0.18, lw=0, label="Range across 25 independent runs")
se = np.sqrt(p_true * (1 - p_true) / np.array(Ns, float))
ax.plot(Ns, p_true + 1.96 * se, color=MUTED, lw=1, ls=(0, (2, 2))); ax.plot(Ns, p_true - 1.96 * se, color=MUTED, lw=1, ls=(0, (2, 2)), label="±1.96 binomial SE")
ax.plot(r.n, r["mean"], color=BLUE, lw=2, marker="o", ms=4, label="Mean estimate")
ax.axhline(p_true, color=INK, lw=1, ls=(0, (4, 3))); ax.axvline(20000, color=ORANGE, lw=1.2)
ax.text(20000 * 1.06, ax.get_ylim()[1] * 0.98 if False else p_true + 2.4 * se[0], "20,000 used", color=ORANGE, fontsize=8.5, va="top")
ax.set_xscale("log"); ax.set_xlabel("Simulated brackets"); ax.set_ylabel(f"P({sts.at[leader, 'Team']} wins title)")
ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0)); ax.legend(frameon=False, fontsize=8.3, loc="upper right"); style(ax)
note(fig, f"2026 tournament, model M0. Reference value from 200,000 brackets: {p_true:.1%}. At 20,000 brackets the simulation error is about ±{1.96*math.sqrt(p_true*(1-p_true)/20000)*100:.1f} percentage points.")
save(fig, "FigureA6_montecarlo_convergence.png", "Figure A6. Monte Carlo estimates stabilize well before 20,000 brackets")

# ---------------- A7: expected vs actual tournament wins per team (M0)
wins_cols = ["P_Reach_R32", "P_Reach_S16", "P_Reach_E8", "P_Reach_F4", "P_Reach_Final", "P_Champion"]
act_cols = [c.replace("P_", "Actual_") for c in wins_cols]
m = m0.copy(); m["exp_w"] = m[wins_cols].sum(1); m["act_w"] = m[act_cols].sum(1)
fig, ax = plt.subplots(figsize=(6.4, 4.8), dpi=220)
jit = np.random.default_rng(3).uniform(-0.12, 0.12, len(m))
ax.scatter(m.exp_w, m.act_w + jit, s=10, color=BLUE, alpha=0.45, linewidths=0, label="Team-tournament (jittered)")
gb = m.groupby(pd.cut(m.exp_w, [0, .25, .5, .75, 1, 1.5, 2, 3, 4.5]), observed=True).agg(x=("exp_w", "mean"), y=("act_w", "mean"))
ax.plot(gb.x, gb.y, color=ORANGE, lw=2, marker="o", ms=5, label="Binned average")
ax.plot([0, 4.5], [0, 4.5], color=INK, lw=1, ls=(0, (4, 3)), label="Perfect forecast")
ax.set(xlim=(0, 4.5), ylim=(-0.3, 6.3), xlabel="Expected tournament wins (simulated)", ylabel="Actual tournament wins")
ax.legend(frameon=False, fontsize=8.5, loc="upper left"); style(ax)
note(fig, f"Expected wins = sum of a team's simulated probabilities of winning each round (M0). {len(m)} team-tournaments, 2022–2026; First Four wins excluded.")
save(fig, "FigureA7_expected_vs_actual_wins.png", "Figure A7. Simulated expected wins line up with actual wins")
