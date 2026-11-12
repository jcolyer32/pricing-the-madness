"""Builds every paper exhibit as a clean, consistently styled image from the research data.
Run from the repo root after run_research.py:  python analysis/make_exhibits.py"""
import math, warnings
from pathlib import Path
import numpy as np, pandas as pd, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
warnings.filterwarnings("ignore")

OUT = Path("results/exhibits"); OUT.mkdir(parents=True, exist_ok=True)
INK, MUTED, RULE, GRID, BG = "#111111", "#5f5e57", "#222222", "#e1e0d9", "#ffffff"
BLUE, ORANGE = "#2a78d6", "#eb6834"
plt.rcParams.update({"font.family": "serif", "font.serif": ["Times New Roman", "Times", "DejaVu Serif"],
                     "font.size": 10.5, "axes.edgecolor": "#c3c2b7", "savefig.facecolor": BG})
Phi = np.vectorize(lambda z: 0.5 * (1 + math.erf(z / math.sqrt(2))))

# ------------------------------------------------------------------ table renderer
def table(fname, title, header, rows, notes, widths, align=None, subheader=None):
    """rows: list of (cells, kind) kind in {'row','se','rule','section'}"""
    W = sum(widths); lh = 0.27
    n_lines = 2.6 + (1 if subheader else 0) + sum(0.7 if k == "se" else (0.35 if k == "rule" else 1) for _, k in rows)
    n_notes = sum(1 + len(t) // int(W * 13.2) for t in notes)
    H = 0.55 + lh * n_lines + 0.2 * n_notes + 0.35
    fig = plt.figure(figsize=(W, H), dpi=220)
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, W); ax.set_ylim(H, 0); ax.axis("off")
    x = np.cumsum([0] + widths[:-1]); align = align or (["left"] + ["center"] * (len(widths) - 1))
    def put(y, cells, **kw):
        for xi, w, a, c in zip(x, widths, align, cells):
            if c in (None, ""): continue
            xx = xi + 0.08 if a == "left" else xi + w / 2
            ax.text(xx, y, c, ha=a, va="center", **kw)
    y = 0.32
    ax.text(0.08, y, title, fontsize=12, fontweight="bold", color=INK, va="center"); y += 0.32
    ax.plot([0.05, W - 0.05], [y, y], color=RULE, lw=1.1); y += lh * 0.6
    two = any("\n" in str(h) for h in header)
    if two: y += lh * 0.45
    put(y, header, fontweight="bold", color=INK, linespacing=1.15); y += lh * (1.55 if two else 1)
    if subheader:
        put(y - 0.04, subheader, fontstyle="italic", color=MUTED, fontsize=9.5); y += lh
    ax.plot([0.05, W - 0.05], [y - lh * 0.45, y - lh * 0.45], color=RULE, lw=0.6)
    y += lh * 0.15
    for cells, kind in rows:
        if kind == "rule":
            ax.plot([0.05, W - 0.05], [y - lh * 0.35, y - lh * 0.35], color=RULE, lw=0.5); y += lh * 0.35; continue
        if kind == "se":
            put(y - lh * 0.22, cells, color=MUTED, fontsize=9); y += lh * 0.7; continue
        if kind == "section":
            ax.text(0.08, y, cells[0], fontstyle="italic", color=MUTED, va="center", fontsize=10); y += lh; continue
        if kind == "bold":
            put(y, cells, color=INK, fontweight="bold"); y += lh; continue
        put(y, cells, color=INK); y += lh
    ax.plot([0.05, W - 0.05], [y - lh * 0.4, y - lh * 0.4], color=RULE, lw=1.1); y += 0.1
    for t in notes:
        import textwrap
        for line in textwrap.wrap(t, int(W * 13.2)):
            ax.text(0.08, y, line, fontsize=8.6, color=MUTED, va="center", fontstyle="italic"); y += 0.2
    fig.savefig(OUT / fname, dpi=220); plt.close(fig); print("wrote", fname)

def star(p): return "***" if p < .01 else "**" if p < .05 else "*" if p < .1 else ""

# ------------------------------------------------------------------ data
ts = pd.read_csv("output/gretl/research_team_season.csv")
tg = pd.read_csv("output/gretl/research_tourney_games.csv")
ts["sq_Rating"] = ts.Rating ** 2

def ols_cluster(y, X, g):
    b = np.linalg.lstsq(X, y, rcond=None)[0]; e = y - X @ b
    XtXi = np.linalg.inv(X.T @ X); meat = np.zeros((X.shape[1],) * 2)
    for c in np.unique(g):
        m = g == c; s = X[m].T @ e[m]; meat += np.outer(s, s)
    G, n, k = len(np.unique(g)), len(y), X.shape[1]
    V = XtXi @ meat @ XtXi * G / (G - 1) * (n - 1) / (n - k)
    return b, np.sqrt(np.diag(V)), e

# ------------------------------------------------------------------ Table 1: descriptive statistics
d = ts.dropna(subset=["Volatility", "ThreePA_Rate", "TR_TOV_Poss_Season", "Pace"])
desc = [("Volatility (pts)", "Volatility", 2), ("Rating (pts vs. avg. D-I team)", "Rating", 2),
        ("Win %", "Win_Pct", 3), ("3PA rate (3PA / FGA)", "ThreePA_Rate", 3), ("Opp. 3PA rate", "Opp_ThreePA_Rate", 3),
        ("FT rate (FTA / FGA)", "FT_Rate", 3), ("Turnovers per 100 poss.", "TR_TOV_Poss_Season", 2),
        ("Opp. turnovers per 100 poss.", "TR_OppTOV_Poss_Season", 2), ("Pace (shots per game)", "Pace", 2),
        ("Games before Selection Sunday", "Games", 1)]
rows = [([lab] + [f"{v:.{k}f}".replace("-", "−") for v in (d[c].mean(), d[c].std(), d[c].min(), d[c].max())], "row") for lab, c, k in desc]
rows += [(["", "", "", "", ""], "rule"), (["Tournament games (N = 335)", "", "", "", ""], "section")]
for lab, c, k in [("Upset (1 = lower-rated team won)", "Upset", 3), ("Expected margin (pts)", "Exp_Margin", 2),
                  ("Game volatility (pts)", "Game_Volatility", 2)]:
    rows.append(([lab, f"{tg[c].mean():.{k}f}", f"{tg[c].std():.{k}f}", f"{tg[c].min():.{k}f}", f"{tg[c].max():.{k}f}"], "row"))
table("Table1_descriptive_statistics.png", "Table 1. Descriptive statistics",
      ["Variable", "Mean", "SD", "Min", "Max"], [(["Team-seasons (N = 1,812; 367 schools)", "", "", "", ""], "section")] + rows,
      ["All team measures use games played on or before Selection Sunday, seasons 2021-22 to 2025-26. Ratings are least-squares margin ratings centered on zero each season."],
      [3.3, 1.0, 1.0, 1.0, 1.0])

# ------------------------------------------------------------------ Table 2: H2
H2 = [("3PA rate", "ThreePA_Rate"), ("Opp. 3PA rate", "Opp_ThreePA_Rate"), ("FT rate", "FT_Rate"),
      ("Turnovers per poss.", "TR_TOV_Poss_Season"), ("Opp. turnovers per poss.", "TR_OppTOV_Poss_Season"),
      ("Pace", "Pace"), ("Rating", "Rating"), ("Rating²", "sq_Rating")]
d = ts.dropna(subset=[c for _, c in H2] + ["Volatility"]).copy()
yrs = sorted(d.Season.unique()); FE = [(d.Season == s).astype(float).to_numpy() for s in yrs[1:]]
specs = [("Volatility", [c for _, c in H2 if c != "sq_Rating"]), ("Volatility", [c for _, c in H2]), ("lnV", [c for _, c in H2])]
res = []
for dep, cols in specs:
    y = np.log(d.Volatility.to_numpy()) if dep == "lnV" else d.Volatility.to_numpy()
    X = np.column_stack([np.ones(len(d))] + [d[c].to_numpy() for c in cols] + FE)
    b, se, e = ols_cluster(y, X, d.TeamID.to_numpy())
    r2 = 1 - e.var() / y.var(); n, k = X.shape; ar2 = 1 - (1 - r2) * (n - 1) / (n - k)
    res.append((dict(zip(cols, zip(b[1:], se[1:]))), r2, ar2, e))
lnV_resid = res[2][3]
def fmt(v, c):
    big = abs(v) >= 0.1 if c not in ("sq_Rating",) else False
    return f"{v:.3f}" if big else (f"{v:.5f}" if c == "sq_Rating" else f"{v:.4f}")
rows = []
for lab, c in H2:
    cells, ses = [lab], [""]
    for coefs, *_ in res:
        if c in coefs:
            b, s = coefs[c]; p = 2 * (1 - Phi(abs(b / s)))
            cells.append(fmt(b, c).replace("-", "−") + star(p)); ses.append(f"({fmt(s, c)})")
        else:
            cells.append(""); ses.append("")
    rows += [(cells, "row"), (ses, "se")]
rows += [([""] * 4, "rule"),
         (["Season fixed effects", "Yes", "Yes", "Yes"], "row"),
         (["Observations / teams", "1,812 / 367", "1,812 / 367", "1,812 / 367"], "row"),
         (["R²", *[f"{r[1]:.3f}" for r in res]], "row"),
         (["Adjusted R²", *[f"{r[2]:.3f}" for r in res]], "row"),
         (["RESET p: squares & cubes", "0.036", "0.067", "0.168"], "row"),
         (["RESET p: squares only", "0.011", "0.022", "0.068"], "row"),
         (["RESET p: cubes only", "0.011", "0.023", "0.069"], "row")]
table("Table2_H2_style_and_volatility.png", "Table 2. Playing style and team volatility (H2)",
      ["", "(1) Linear", "(2) U-shape", "(3) Log U-shape"], rows,
      ["Pooled OLS on team-seasons, 2021-22 to 2025-26. Standard errors clustered by team in parentheses. *** p < 0.01, ** p < 0.05, * p < 0.10.",
       "Column (3) is the preferred specification, the only one in which Ramsey's RESET does not reject. Its coefficients × 100 ≈ % change in volatility."],
      [2.7, 1.45, 1.45, 1.45], subheader=["Dependent variable:", "Volatility", "Volatility", "ln(Volatility)"])

# ------------------------------------------------------------------ Table 3: H3
rows = [(["Exp. margin", "−0.073***", "", "", "−0.027"], "row"), (["", "(0.015)", "", "", "(0.057)"], "se"),
        (["Game volatility", "0.024", "", "", ""], "row"), (["", "(0.054)", "", "", ""], "se"),
        (["Z_Const = margin / σ", "", "−0.769***", "", ""], "row"), (["", "", "(0.149)", "", ""], "se"),
        (["Z_Team = margin / game volatility", "", "", "−0.727***", ""], "row"), (["", "", "", "(0.147)", ""], "se"),
        (["Variance eq.: ln game volatility", "", "", "", "−0.444"], "row"), (["", "", "", "", "(0.904)"], "se"),
        (["Constant", "−0.724", "−0.025", "−0.043", "−0.004"], "row"), (["", "(0.890)", "(0.118)", "(0.118)", "(0.046)"], "se"),
        ([""] * 5, "rule"),
        (["Round & season dummies", "Yes", "No", "No", "No"], "row"),
        (["Log-likelihood", "−177.96", "−183.40", "−184.12", "−183.07"], "row"),
        (["McFadden R²", "0.117", "0.090", "0.087", ""], "row"),
        (["AIC", "381.9", "370.8", "372.2", "372.1"], "row"),
        (["LR test, heteroskedasticity (p)", "", "", "", "0.65"], "row")]
table("Table3_H3_upset_models.png", "Table 3. Do volatile matchups produce more upsets? (H3)",
      ["", "(1)\nProbit", "(2)\nProbit", "(3)\nProbit", "(4) Hetero-\nskedastic probit"], rows,
      ["Dependent variable: Upset = 1 if the lower-rated team won. N = 335 NCAA tournament games, 2022–2026. QML (sandwich) standard errors in parentheses. *** p < 0.01.",
       "If ratings are correctly scaled, theory implies a slope of −1 on Z and a constant of 0. Column (2): slope −0.77 (p = 0.12 vs. −1)."],
      [3.0, 1.15, 1.15, 1.15, 1.6])

# ------------------------------------------------------------------ Table 4: H4
x = pd.read_excel("output/ncaam_research.xlsx", sheet_name=None)
s = x["Model_Scores"]; a = s[s.Season.astype(str) == "ALL"].set_index("Model")
order = [("M0 Strength, constant volatility", "M0  Strength, constant volatility"),
         ("M1 Strength + team volatility", "M1  Strength + team volatility"),
         ("M2 Strength + style-predicted volatility", "M2  Strength + style volatility"),
         ("M3 Strength, tournament-widened volatility (LOSO)", "M3  Strength, widened σ (LOSO)"),
         ("B1 TeamRankings rating, constant volatility", "B1  TeamRankings rating")]
cols = ["Game_LogLoss", "Game_Brier", "Game_Accuracy", "Adv_Brier", "P_Actual_Champion"]
best = {c: (a[c].max() if c in ("Game_Accuracy", "P_Actual_Champion") else a[c].min()) for c in cols}
rows = []
for key, lab in order:
    cells = [lab]
    for c in cols:
        v = a.at[key, c]; t = f"{v:.1%}" if c in ("Game_Accuracy", "P_Actual_Champion") else f"{v:.4f}"
        cells.append(t)
    kind = "bold" if key.startswith("B1") else "row"
    rows.append((cells, kind))
rows += [([""] * 6, "rule"), (["Coin flip (reference)", "0.6931", "0.2500", "50.0%", "", "1.5%"], "row")]
table("Table4_H4_forecast_scores.png", "Table 4. Out-of-sample tournament forecasts, 2022–2026 (H4)",
      ["Model", "Game\nlog loss", "Game\nBrier", "Accuracy", "Advancement\nBrier", "P(actual\nchampion)"], rows,
      ["335 tournament games and 2,040 team-by-round advancement forecasts per model; 20,000 simulated brackets per season. Lower log loss and Brier are better.",
       "Bold = best model overall. P(champion) = average probability given to the eventual champion. M3 estimates its widening factor from the other four tournaments only."],
      [3.1, 1.05, 1.0, 1.0, 1.3, 1.15])

# ------------------------------------------------------------------ Table 5: robustness
r = pd.read_csv("output/robustness/robustness_summary.csv", index_col=0)
C = ["baseline", "cap25", "shrink5", "shrink30"]
def row(lab, key, f, pk=None):
    return ([lab] + [f.format(float(r.at[key, c])).replace("-", "−") + (star(float(r.at[pk, c])) if pk else "") for c in C], "row")
rows = [row("H1  half-season correlation", "H1 half-season r", "{:.3f}"), ([""] * 5, "rule")]
for v, lab in [("ThreePA_Rate", "3PA rate"), ("Opp_ThreePA_Rate", "Opp. 3PA rate"), ("FT_Rate", "FT rate"),
               ("TR_TOV_Poss_Season", "Turnovers per poss."), ("TR_OppTOV_Poss_Season", "Opp. turnovers per poss."), ("Pace", "Pace")]:
    rows.append(row(f"H2  {lab}", f"H2 {v}", "{:.4f}", f"H2 {v} p"))
rows += [([""] * 5, "rule"), row("H3  game volatility (p-value)", "H3 Game_Volatility p", "{:.2f}"),
         row("H3  slope on Z_Const (theory −1)", "H3 Z_Const slope", "{:.3f}"), ([""] * 5, "rule")]
for m, lab in [("M0", "M0 constant σ"), ("M1", "M1 team volatility"), ("M2", "M2 style volatility"), ("M3", "M3 widened σ"), ("B1", "B1 TeamRankings")]:
    rows.append(row(f"H4  log loss, {lab}", f"H4 LogLoss {m}", "{:.4f}"))
table("Table5_robustness.png", "Table 5. Robustness checks",
      ["Statistic", "Baseline", "Margins\ncapped at 25", "Shrinkage\nn/(n+5)", "Shrinkage\nn/(n+30)"], rows,
      ["Each column re-runs the full pipeline (ratings, volatility, 20,000 simulated brackets per season) with one setting changed. H2: preferred log specification, team-clustered SEs. *** p < 0.01, ** p < 0.05.",
       "Shrinkage changes volatility only, so rating-based rows are unchanged by design. With capped margins the Z_Const slope matches the theoretical −1."],
      [3.1, 1.25, 1.35, 1.35, 1.35])

# ------------------------------------------------------------------ Table A1: diagnostics
cols = [c for _, c in H2]
Xv = d[cols].to_numpy()
vif = []
for i, c in enumerate(cols):
    o = np.delete(Xv, i, axis=1); Xo = np.column_stack([np.ones(len(d)), o] + FE)
    bb = np.linalg.lstsq(Xo, Xv[:, i], rcond=None)[0]; r2 = 1 - ((Xv[:, i] - Xo @ bb).var() / Xv[:, i].var())
    vif.append(1 / (1 - r2))
rows = [([lab, f"{v:.2f}"], "row") for (lab, _), v in zip(H2, vif)]
rows += [(["", ""], "rule"),
         (["Ramsey RESET, squares & cubes (p)", "0.168"], "row"), (["Ramsey RESET, squares only (p)", "0.068"], "row"),
         (["Ramsey RESET, cubes only (p)", "0.069"], "row"),
         (["Normality of residuals, Doornik–Hansen χ²(2)", "38.4 (p < 0.001)"], "row")]
table("TableA1_H2_diagnostics.png", "Table A1. Diagnostics for the preferred H2 model",
      ["Test", "Value"], [(["Variance inflation factors", ""], "section")] + rows,
      ["All VIFs are far below the conventional threshold of 10. Residuals depart from normality through lighter-than-normal tails, consistent with shrinkage of the volatility estimates; with N = 1,812 and team-clustered errors, inference relies on asymptotic normality."],
      [3.8, 1.8])

# ------------------------------------------------------------------ Figure A1: residual histogram
fig, ax = plt.subplots(figsize=(6.4, 3.9), dpi=220)
e = lnV_resid; sd = e.std()
ax.hist(e, bins=30, density=True, color=BLUE, alpha=0.85, edgecolor=BG, linewidth=0.8)
g = np.linspace(e.min() - 0.05, e.max() + 0.05, 300)
ax.plot(g, np.exp(-g ** 2 / (2 * sd ** 2)) / (sd * math.sqrt(2 * math.pi)), color=INK, lw=1.4, label="Normal, same SD")
ax.set_xlabel("Residual, ln(volatility)"); ax.set_ylabel("Density")
for sp in ("top", "right"): ax.spines[sp].set_visible(False)
ax.grid(axis="y", color=GRID, lw=0.6); ax.set_axisbelow(True); ax.legend(frameon=False, fontsize=9)
ax.set_title("Figure A1. Residuals of the preferred H2 model\ncompared with a normal distribution", loc="left", fontsize=11, fontweight="bold")
fig.tight_layout(); fig.savefig(OUT / "FigureA1_H2_residuals.png"); plt.close(fig); print("wrote FigureA1")

# ------------------------------------------------------------------ Figure 1: calibration
cal = x["Calibration"]
fig, ax = plt.subplots(figsize=(6.2, 5.3), dpi=220)
ax.plot([0, 1], [0, 1], ls=(0, (4, 3)), lw=1, color="#9b9a90", zorder=1)
ax.text(0.80, 0.74, "perfect calibration", rotation=41, color=MUTED, fontsize=8.5, ha="center")
for key, lab, col, mk in [("M0 Strength, constant volatility", "M0: strength, constant volatility", BLUE, "o"),
                          ("M1 Strength + team volatility", "M1: strength + team volatility", ORANGE, "s")]:
    dd = cal[cal.Model == key]
    ax.plot(dd.Mean_Predicted, dd.Observed_Rate, color=col, lw=1.6, zorder=2)
    ax.scatter(dd.Mean_Predicted, dd.Observed_Rate, s=np.clip(dd.Forecasts, 20, 260) * 0.5, color=col, marker=mk,
               edgecolor=BG, linewidth=1.4, zorder=3, label=lab)
ax.set_xlim(0, 1); ax.set_ylim(0, 1)
ax.set_xlabel("Predicted probability of reaching the round"); ax.set_ylabel("Observed share that reached it")
ax.grid(color=GRID, lw=0.6); ax.set_axisbelow(True)
for sp in ("top", "right"): ax.spines[sp].set_visible(False)
ax.legend(frameon=False, loc="upper left", fontsize=9)
ax.set_title("Figure 1. Bracket probabilities are well calibrated,\nwith or without team volatility", loc="left", fontsize=11, fontweight="bold")
fig.text(0.02, 0.01, "2,040 advancement forecasts per model (68 teams × 6 rounds × 5 tournaments), binned by predicted probability. Marker size = forecasts per bin.",
         fontsize=7.4, color=MUTED, fontstyle="italic")
fig.tight_layout(rect=(0, 0.035, 1, 1)); fig.savefig(OUT / "Figure1_calibration.png"); plt.close(fig); print("wrote Figure1")

# ------------------------------------------------------------------ Figure 2: the ratings artifact
fig, axes = plt.subplots(1, 2, figsize=(9.6, 4.3), dpi=220, sharey=True)
for ax, (path, lab, slope, const) in zip(axes, [("output/gretl/research_tourney_games.csv", "Uncapped ratings (main)", -0.769, -0.025),
                                                ("output/robustness/cap25/gretl/research_tourney_games.csv", "Margins capped at 25", -1.036, 0.000)]):
    t = pd.read_csv(path)
    bins = [0, 0.25, 0.5, 0.75, 1.0, 1.4, 3.5]
    t["bin"] = pd.cut(t.Z_Const, bins, include_lowest=True)
    gb = t.groupby("bin", observed=True).agg(z=("Z_Const", "mean"), up=("Upset", "mean"), n=("Upset", "size"))
    se = np.sqrt(gb.up * (1 - gb.up) / gb.n)
    zz = np.linspace(0, 3.2, 200)
    ax.plot(zz, Phi(-zz), color=INK, lw=1.3, ls=(0, (4, 3)), label="Theory: Φ(−Z)")
    ax.plot(zz, Phi(const + slope * zz), color=BLUE, lw=2, label=f"Fitted probit, slope {slope:.2f}".replace("-", "−"))
    ax.errorbar(gb.z, gb.up, yerr=1.0 * se, fmt="o", color=ORANGE, ms=6, mec=BG, mew=1.2, ecolor="#c3c2b7", elinewidth=1, capsize=0,
                label="Observed upset rate (±1 SE)")
    ax.set_title(lab, loc="left", fontsize=10.5, fontweight="bold")
    ax.set_xlabel("Z = expected margin ÷ game SD"); ax.set_xlim(0, 3.2); ax.set_ylim(0, 0.6)
    ax.grid(color=GRID, lw=0.6); ax.set_axisbelow(True)
    for sp in ("top", "right"): ax.spines[sp].set_visible(False)
axes[0].set_ylabel("Probability the underdog wins"); axes[0].legend(frameon=False, fontsize=8.5, loc="upper right")
fig.suptitle("Figure 2. With blowouts capped, tournament upsets occur at the rate theory predicts", x=0.01, ha="left", fontsize=11.5, fontweight="bold")
fig.text(0.01, 0.01, "335 NCAA tournament games, 2022–2026, grouped by Z. Uncapped ratings overstate favorites (slope −0.77); capped ratings match theory (slope −1.04, constant 0.00).",
         fontsize=7.6, color=MUTED, fontstyle="italic")
fig.tight_layout(rect=(0, 0.04, 1, 0.965)); fig.savefig(OUT / "Figure2_ratings_artifact.png"); plt.close(fig); print("wrote Figure2")
