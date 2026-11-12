"""
Offline tests: fake versions of each site's page are generated, then the real
parsers / matcher / validator / writer run against them. No internet needed.

    python -m pytest tests -q        (or just: python tests/test_pipeline.py)
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.sources import espn, kenpom, teamrankings   # noqa: E402
from pipeline.teams import TeamMatcher                    # noqa: E402
from pipeline import validate                             # noqa: E402

# (ESPN location, ESPN id, KenPom spelling, TeamRankings spelling)
TRICKY = [
    ("UConn", "41", "Connecticut", "Connecticut"),
    ("Ole Miss", "145", "Mississippi", "Mississippi"),
    ("Miami", "2390", "Miami FL", "Miami (FL)"),
    ("Miami (OH)", "193", "Miami OH", "Miami (OH)"),
    ("NC State", "152", "N.C. State", "NC State"),
    ("North Carolina", "153", "North Carolina", "N Carolina"),
    ("St. John's", "2599", "St. John's", "St Johns"),
    ("Saint Mary's", "2608", "Saint Mary's", "St Marys"),
    ("Michigan State", "127", "Michigan St.", "Michigan St"),
    ("Michigan", "130", "Michigan", "Michigan"),
    ("Stephen F. Austin", "2617", "Stephen F. Austin", "SF Austin"),
    ("UT Martin", "2630", "Tennessee Martin", "TN Martin"),
    ("Texas A&M", "245", "Texas A&M", "Texas A&M"),
    ("Texas A&M-Corpus Christi", "357", "Texas A&M Corpus Chris", "TX A&M-CC"),
    ("East Tennessee State", "2193", "East Tennessee St.", "E Tenn St"),
    ("Southern", "2582", "Southern", "Southern"),
    ("Southern Miss", "2572", "Southern Miss", "S Mississippi"),
]
FILLER = [(f"Filler U{i:03d}", str(9000 + i), f"Filler U{i:03d}", f"Filler U{i:03d}") for i in range(345)]
TEAMS = TRICKY + FILLER


def espn_teams_json():
    return json.dumps({"sports": [{"leagues": [{"teams": [
        {"team": {"id": i, "location": loc, "displayName": f"{loc} Mascots",
                  "shortDisplayName": loc, "name": "Mascots", "abbreviation": loc[:4].upper()}}
        for loc, i, _, _ in TEAMS]}]}]})


def kenpom_html(shift=False):
    rows = []
    for rk, (_, _, kp, _) in enumerate(TEAMS, 1):
        net = 35 - rk * 0.2
        cells = [str(rk), f'<a href="team.php?team={kp}">{kp}</a> <span class="seed">3</span>',
                 "B10", "20-5", f"{net:+.2f}", "115.2", "10", "95.1", "20", "68.3", "100",
                 "+.031", "50", "+5.20", "40", "108.1", "30", "102.9", "35", "-1.20", "200"]
        if shift:
            cells.insert(5, "99")          # simulate KenPom adding a column
        rows.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
    head = "<thead><tr><th>Rk</th><th>Team</th></tr></thead>"
    return f'<html><table id="ratings-table">{head}<tbody>{"".join(rows)}</tbody></table></html>'


def tr_stat_html(extra_col=False):
    hdr = ["Rank", "Team", "2027", "Last 3", "Last 1", "Home", "Away", "2026"]
    if extra_col:
        hdr.insert(3, "Neutral")
    body = []
    for rk, (_, _, _, tr) in enumerate(TEAMS, 1):
        vals = [rk, tr, "54.1%", "55.0%", "--", "56.2%", "51.0%", "52.3%"]
        if extra_col:
            vals.insert(3, "50%")
        body.append("<tr>" + "".join(f"<td>{v}</td>" for v in vals) + "</tr>")
    return ("<table class='tr-table'><thead><tr>" + "".join(f"<th>{h}</th>" for h in hdr)
            + "</tr></thead><tbody>" + "".join(body) + "</tbody></table>")


def tr_rank_html():
    hdr = ["Rank", "Team", "Rating", "Hi", "Low", "Last"]
    body = "".join(f"<tr><td>{rk}</td><td>{tr} (20-5)</td><td>{20 - rk * 0.1:.1f}</td>"
                   f"<td>1</td><td>9</td><td>{rk}</td></tr>" for rk, (_, _, _, tr) in enumerate(TEAMS, 1))
    return ("<table><thead><tr>" + "".join(f"<th>{h}</th>" for h in hdr)
            + f"</tr></thead><tbody>{body}</tbody></table>")


def scoreboard_json(day):
    return json.dumps({"events": [
        {"id": f"{day}01", "date": f"{day[:4]}-{day[4:6]}-{day[6:]}T23:00Z",
         "competitions": [{"neutralSite": False, "conferenceCompetition": True,
                           "status": {"type": {"name": "STATUS_FINAL", "completed": True}},
                           "venue": {"fullName": "Cameron"},
                           "competitors": [
                               {"homeAway": "home", "score": "80", "winner": True,
                                "team": {"id": "41", "location": "UConn"}, "curatedRank": {"current": 5}},
                               {"homeAway": "away", "score": "71", "winner": False,
                                "team": {"id": "145", "location": "Ole Miss"}, "curatedRank": {"current": 99}}],
                           "odds": [{"details": "CONN -6.5", "spread": -6.5, "overUnder": 148.5}]}]}]})


class FakeFetcher:
    """Stands in for pipeline.http.Fetcher: returns fixture text by URL."""
    def __init__(self, broken=()):
        self.broken = set(broken)

    def get(self, url, key=None):
        for b in self.broken:
            if b in url:
                raise RuntimeError(f"simulated outage for {b}")
        if "kenpom.com" in url:
            return kenpom_html()
        if "/stat/" in url:
            return tr_stat_html()
        if "/ranking/" in url:
            return tr_rank_html()
        if "/teams" in url:
            return espn_teams_json()
        if "scoreboard" in url:
            return scoreboard_json(url.split("dates=")[1][:8])
        raise ValueError(url)


# ---------------------------------------------------------------------- tests
def test_kenpom_parse():
    df = kenpom.parse(kenpom_html())
    assert len(df) == len(TEAMS)
    assert df.loc[0, "Team"] == "Connecticut"          # seed stripped
    assert df.loc[0, "ORtg"] == 115.2 and df.loc[0, "Luck"] == 0.031
    assert df.loc[0, "Wins"] == 20
    assert not validate.check("KenPom", df)


def test_kenpom_layout_change_is_caught():
    try:
        kenpom.parse(kenpom_html(shift=True))
    except ValueError as e:
        assert "layout" in str(e)
    else:
        raise AssertionError("layout change not detected")


def test_tr_stat_parse_and_layout_guard():
    df = teamrankings.parse_stat(tr_stat_html())
    assert list(df.columns) == teamrankings.STAT_COLS
    assert df.loc[0, "Season"] == 54.1 and pd.isna(df.loc[0, "Last1"])
    try:
        teamrankings.parse_stat(tr_stat_html(extra_col=True))
    except ValueError:
        pass
    else:
        raise AssertionError("TR layout change not detected")


def test_tr_ranking_record_split():
    df = teamrankings.parse_ranking(tr_rank_html())
    assert df.loc[0, "Team"] == "Connecticut" and df.loc[0, "Record"] == "20-5"


def test_team_matching():
    tmp = Path(tempfile.mkdtemp())
    (tmp / "data").mkdir()
    shutil.copy(ROOT / "data" / "team_aliases.csv", tmp / "data")
    m = TeamMatcher(tmp, espn.parse_teams(json.loads(espn_teams_json())))
    kp = m.attach(kenpom.parse(kenpom_html()), "kenpom")
    tr = m.attach(teamrankings.parse_stat(tr_stat_html()), "teamrankings")
    truth = {loc: i for loc, i, _, _ in TEAMS}
    for df in (kp, tr):
        wrong = df[df["TeamKey"].notna() & (df["TeamKey"] != df["Team"].map(truth))]
        assert wrong.empty, wrong
    um = m.unmatched_df()
    print("\nunmatched:", um.values.tolist(), "\nfuzzy:", [(r['source_name'], r['Team']) for r in m.new_fuzzy])
    # every unmatched name must be unmatched, never mis-matched (checked above)
    assert len(um) <= 3


def test_validation_catches_bad_ranges():
    df = kenpom.parse(kenpom_html())
    df.loc[0, "AdjT"] = 115.0
    assert any("AdjT" in p for p in validate.check("KenPom", df))


def test_end_to_end(monkeypatch=None):
    """Full run.py flow in a temp copy, with one source failing on the second run."""
    import importlib
    tmp = Path(tempfile.mkdtemp()) / "proj"
    shutil.copytree(ROOT, tmp, ignore=shutil.ignore_patterns("output", "logs", "cache", "last_good", ".venv", "excel", "_to_delete", "espn_days", ".pytest_cache",
                                                             "snapshots", "espn_games.csv",
                                                             "team_crosswalk.csv", "__pycache__"))
    cfg = (tmp / "config.yaml").read_text().replace("start_date: 2026-11-02", "start_date: 2026-11-02") \
        .replace("end_date:   2027-04-07", "end_date:   2027-04-07")
    (tmp / "config.yaml").write_text(cfg)
    sys.path.insert(0, str(tmp))
    run = importlib.import_module("run")
    run.ROOT = tmp
    import datetime as dt

    class FixedDate(dt.date):
        @classmethod
        def today(cls):
            return cls(2026, 11, 5)
    espn.date = FixedDate

    def go(broken=()):
        run.Fetcher = lambda *a, **k: FakeFetcher(broken)
        sys.argv = ["run.py"]
        return run.main()

    assert go() == 0
    x = pd.read_excel(tmp / "output" / "ncaam_data.xlsx", sheet_name=None)
    st = x["_Status"]
    print("\n", st[["Table", "Result", "Rows"]].to_string())
    assert {"Master", "KenPom", "TR_Stats", "ESPN_Games", "Crosswalk"} <= set(x)
    master = x["Master"]
    uc = master[master["Team"] == "UConn"].iloc[0]
    assert uc["KP_ORtg"] == 115.2 and uc["TR_eFG_Season"] == 54.1
    assert len(x["ESPN_Games"]) == 4          # Nov 2–5, one game each
    mv = x["Modelv26"]
    assert list(mv.columns) == ["Team", "Win %", "SOS", "OFF Efficiency", "DEF Efficiency", "Effective FG %",
                                "Floor %", "FG%", "REB", "TO", "3PM", "OFF/DEF Diff", "TeamKey"]
    r = mv[mv["Team"] == "UConn"].iloc[0]
    assert abs(r["Effective FG %"] - 0.541) < 1e-9 and r["FG%"] == 54.1 and r["OFF/DEF Diff"] == 0
    assert r["SOS"] == 19.9 and list(x)[1] == "Modelv26"

    # second run: KenPom "down" -> last good copy used, status says so, exit code 1
    assert go(broken=["kenpom.com"]) == 1
    st = pd.read_excel(tmp / "output" / "ncaam_data.xlsx", sheet_name="_Status")
    kp_row = st[st["Table"] == "KenPom"].iloc[0]
    assert kp_row["Result"].startswith("FAILED – using last good"), kp_row
    m2 = pd.read_excel(tmp / "output" / "ncaam_data.xlsx", sheet_name="Master")
    assert m2["KP_ORtg"].notna().sum() > 300      # model still has KenPom data
    assert len(pd.read_excel(tmp / "output" / "ncaam_data.xlsx", sheet_name="ESPN_Games")) == 4  # no dupes


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
