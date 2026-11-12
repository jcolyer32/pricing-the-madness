"""
Team-name matching — the part that makes multi-source data trustworthy.

Every site spells teams differently:
    KenPom "Connecticut" | TeamRankings "Connecticut" | ESPN "UConn"
    KenPom "Mississippi" | TeamRankings "Mississippi" | ESPN "Ole Miss"
    KenPom "Stephen F. Austin" | TeamRankings "SF Austin"

We use ESPN's team id as the one master key (TeamKey) and map every other
site's spelling to it. The mapping lives in data/team_crosswalk.csv:

    source, source_name, ESPN_ID, Team, match_type

match_type:
    alias  – from data/team_aliases.csv (hand-written, always trusted)
    exact  – names identical after normalising ("St." = "State", etc.)
    fuzzy  – close spelling match; ACCEPTED but listed in _Status for you to eyeball
    manual – you typed it into the crosswalk yourself (never overwritten)

Anything that can't be matched confidently is NOT guessed — it goes to the
_Unmatched sheet so you can add one line to team_aliases.csv.
"""
from __future__ import annotations

import difflib
import logging
import re
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

# word-level expansions applied before comparing names
WORD_MAP = {
    "st": "st", "state": "st", "saint": "st", "saint's": "st",
    "n": "north", "s": "south", "e": "east", "w": "west", "c": "central",
    "no": "north", "so": "south",
    "u": "university", "univ": "university",
    "mt": "mount", "ft": "fort",
    "intl": "international", "chr": "christian",
    "&": "and",
}
DROP_WORDS = {"the", "university", "of", "at"}


def normalize(name: str) -> str:
    s = str(name).lower().strip()
    s = s.replace("&", " and ").replace("-", " ").replace("/", " ")
    s = re.sub(r"[.'’()]", "", s)
    words = [WORD_MAP.get(w, w) for w in s.split()]
    words = [w for w in words if w not in DROP_WORDS]
    return " ".join(words)


class TeamMatcher:
    def __init__(self, root: Path, espn_teams: pd.DataFrame):
        self.xwalk_path = root / "data" / "team_crosswalk.csv"
        self.alias_path = root / "data" / "team_aliases.csv"
        self.espn = espn_teams.copy()
        self.espn["ESPN_ID"] = self.espn["ESPN_ID"].astype(str)
        self.id_to_team = dict(zip(self.espn["ESPN_ID"], self.espn["ESPN_Location"]))

        # lookup: normalized name -> set of ESPN ids (several spellings per team)
        self.norm_index: dict[str, set[str]] = {}
        for _, t in self.espn.iterrows():
            for col in ("ESPN_Location", "ESPN_Short", "ESPN_Name", "ESPN_Abbr"):
                if pd.notna(t.get(col)):
                    self.norm_index.setdefault(normalize(t[col]), set()).add(t["ESPN_ID"])

        self.xwalk = (pd.read_csv(self.xwalk_path, dtype=str) if self.xwalk_path.exists()
                      else pd.DataFrame(columns=["source", "source_name", "ESPN_ID", "Team", "match_type"]))
        self.aliases = self._load_aliases()
        self.unmatched: list[dict] = []
        self.new_fuzzy: list[dict] = []

    # ------------------------------------------------------------------
    def _load_aliases(self) -> dict[tuple[str, str], str]:
        """(source or '*', normalized source name) -> ESPN_ID"""
        if not self.alias_path.exists():
            return {}
        a = pd.read_csv(self.alias_path, dtype=str, comment="#").fillna("")
        out, missing = {}, []
        for _, r in a.iterrows():
            target = r["espn_team"].strip()
            if target.isdigit():
                espn_id = target
            else:
                ids = self.norm_index.get(normalize(target), set())
                espn_id = next(iter(ids)) if len(ids) == 1 else None
            if espn_id is None:
                missing.append(target)
                continue
            out[(r["source"].strip() or "*", normalize(r["source_name"]))] = espn_id
        if missing:
            log.debug("team_aliases.csv: %d targets not in ESPN list (unused): %s",
                      len(missing), missing)
        return out

    def _match_one(self, source: str, name: str) -> tuple[str | None, str]:
        n = normalize(name)
        for key in ((source, n), ("*", n)):
            if key in self.aliases:
                return self.aliases[key], "alias"
        ids = self.norm_index.get(n, set())
        if len(ids) == 1:
            return next(iter(ids)), "exact"
        # fuzzy: best must be very close AND clearly better than runner-up
        scores = sorted(((difflib.SequenceMatcher(None, n, k).ratio(), k)
                         for k in self.norm_index), reverse=True)[:2]
        if scores and scores[0][0] >= 0.88 and len(self.norm_index[scores[0][1]]) == 1:
            if len(scores) < 2 or scores[0][0] - scores[1][0] >= 0.06:
                return next(iter(self.norm_index[scores[0][1]])), "fuzzy"
        return None, "unmatched"

    # ------------------------------------------------------------------
    def attach(self, df: pd.DataFrame, source: str, name_col: str = "Team") -> pd.DataFrame:
        """Add TeamKey (ESPN id) + canonical Team name to a source table."""
        sub = self.xwalk[(self.xwalk["source"] == source) & self.xwalk["ESPN_ID"].notna()]
        known = dict(zip(sub["source_name"], sub["ESPN_ID"]))
        keys = []
        for name in df[name_col].astype(str):
            if name in known:
                keys.append(known[name])
                continue
            espn_id, how = self._match_one(source, name)
            if espn_id is None:
                self.unmatched.append({"source": source, "source_name": name})
            else:
                row = {"source": source, "source_name": name, "ESPN_ID": espn_id,
                       "Team": self.id_to_team.get(espn_id), "match_type": how}
                self.xwalk = pd.concat([self.xwalk, pd.DataFrame([row])], ignore_index=True)
                known[name] = espn_id
                if how == "fuzzy":
                    self.new_fuzzy.append(row)
            keys.append(espn_id)

        out = df.copy()
        out = out.rename(columns={name_col: f"{source}_Name"})
        out.insert(0, "TeamKey", keys)
        out.insert(1, "Team", [self.id_to_team.get(k) if k else None for k in keys])

        # two different source rows mapped to the same team = a bad match. Flag it.
        dupes = out[out["TeamKey"].notna() & out.duplicated("TeamKey", keep=False)]
        if not dupes.empty:
            raise ValueError(
                f"{source}: several names mapped to the same team: "
                f"{dupes[[f'{source}_Name', 'Team']].values.tolist()} — fix data/team_crosswalk.csv")
        return out

    def save(self) -> None:
        self.xwalk.drop_duplicates(["source", "source_name"], keep="first") \
                  .sort_values(["source", "source_name"]) \
                  .to_csv(self.xwalk_path, index=False)

    def unmatched_df(self) -> pd.DataFrame:
        return pd.DataFrame(self.unmatched).drop_duplicates() if self.unmatched \
            else pd.DataFrame(columns=["source", "source_name"])
