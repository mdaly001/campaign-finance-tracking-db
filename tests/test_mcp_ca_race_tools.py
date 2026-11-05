"""Tests for ``ca_race_overview`` (core/mcp/tools.py).

Two layers:

* Mocked unit tests — patch ``execute_read`` with canned rows and verify the
  name-variant merge, the for/against split, the chamber roster, and the
  ``unmatched`` bleed. Hermetic and fast (no live DB).
* Live tests (``CFDB_LIVE_DB=1``) — assert the exact Reyes SD-29 2024 ground
  truth and the Lopez AD-68 2026 structural properties against real
  CAL-ACCESS data.

Run the live layer against the isolated testing stack:

    CFDB_LIVE_DB=1 python -m pytest tests/test_mcp_ca_race_tools.py -q
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

import core.mcp.tools as T
from core.mcp.server import TOOLS, _create_server


def _captured_sql(calls) -> str:
    return " ".join(" ".join(c.args[0].split()).upper() for c in calls)


# Canned rows mirroring the Reyes SD-29 2024 shape, including the
# cand_naml / cand_namf split (some rows put the whole name in cand_naml,
# others put last in cand_naml and first in cand_namf).
REYES_ROWS = [
    # Newman (SEN)
    {"cand_naml": "Newman", "cand_namf": "Josh", "office_cd": "SEN",
     "sup_opp_cd": "S", "amount": 56993.92, "expn_date": "2024-05-01",
     "iec_naml": "CAPT PAC", "iec_namf": None},
    # Reyes variants (SEN) — four spellings of the same person
    {"cand_naml": "Eloise Reyes", "cand_namf": "", "office_cd": "SEN",
     "sup_opp_cd": "S", "amount": 39171.03, "expn_date": "2024-03-01",
     "iec_naml": "COOP AMER PHYS", "iec_namf": None},
    {"cand_naml": "Reyes", "cand_namf": "Eloise", "office_cd": "SEN",
     "sup_opp_cd": "S", "amount": 4612.00, "expn_date": "2024-04-01",
     "iec_naml": "COURAGE", "iec_namf": None},
    {"cand_naml": "Eloise Gomez Reyes", "cand_namf": "", "office_cd": "SEN",
     "sup_opp_cd": "S", "amount": 165.87, "expn_date": "2024-06-01",
     "iec_naml": "CFT COPE", "iec_namf": None},
    {"cand_naml": "Eloise Reyes for Senate 2024", "cand_namf": "", "office_cd": "SEN",
     "sup_opp_cd": "S", "amount": 157.18, "expn_date": "2024-07-01",
     "iec_naml": "CTA", "iec_namf": None},
    # Rivas (ASM) — the other chamber, must land in `unmatched`
    {"cand_naml": "Robert Rivas", "cand_namf": "", "office_cd": "ASM",
     "sup_opp_cd": "S", "amount": 737.67, "expn_date": "2024-05-01",
     "iec_naml": "RIVAS IEC", "iec_namf": None},
    {"cand_naml": "Rivas", "cand_namf": "Robert", "office_cd": "ASM",
     "sup_opp_cd": "S", "amount": 611.98, "expn_date": "2024-06-01",
     "iec_naml": "RIVAS IEC2", "iec_namf": None},
]


# --------------------------------------------------------------------------- #
#  _candidate_tokens
# --------------------------------------------------------------------------- #
class TestCandidateTokens:
    def test_strips_for_office_year(self):
        assert T._candidate_tokens("Eloise Reyes for Senate 2024") == frozenset({"ELOISE", "REYES"})

    def test_basic(self):
        assert T._candidate_tokens("Eloise Reyes") == frozenset({"ELOISE", "REYES"})

    def test_empty(self):
        assert T._candidate_tokens("") == frozenset()
        assert T._candidate_tokens(None) == frozenset()


# --------------------------------------------------------------------------- #
#  _merge_candidate_variants
# --------------------------------------------------------------------------- #
class TestMergeVariants:
    def test_reyes_variants_merge_to_one_group(self):
        m = T._merge_candidate_variants(
            ["Eloise Reyes", "Reyes Eloise", "Eloise Gomez Reyes", "Eloise Reyes for Senate 2024"]
        )
        assert len(set(m.values())) == 1

    def test_distinct_people_do_not_merge(self):
        m = T._merge_candidate_variants(["John Smith", "Jane Smith"])
        assert m["John Smith"] != m["Jane Smith"]

    def test_single_token_cannot_swallow_a_full_name(self):
        m = T._merge_candidate_variants(["Reyes", "Eloise Reyes"])
        # a bare one-token name must NOT collapse into a two-token name
        assert m["Reyes"] != m["Eloise Reyes"]


# --------------------------------------------------------------------------- #
#  ca_race_overview (mocked)
# --------------------------------------------------------------------------- #
class TestCaRaceOverviewMocked:
    def test_reyes_merge_for_against_and_roster(self):
        with patch.object(T, "execute_read", return_value=REYES_ROWS):
            out = T.ca_race_overview("S", "29", 2024)
        # roster = the two Senate candidates (Reyes merged, Newman); Rivas (ASM) excluded
        assert len(out["race_roster"]) == 2
        reyes = next(c for c in out["by_candidate"] if "REYES" in c["candidate"].upper())
        newman = next(c for c in out["by_candidate"] if "NEWMAN" in c["candidate"].upper())
        assert reyes["for_total"] == 44106.08
        assert reyes["for_count"] == 4
        assert reyes["against_total"] == 0.0
        assert newman["for_total"] == 56993.92
        # Rivas (ASM) surfaces in `unmatched`, never in by_candidate
        assert out["unmatched"]["total"] == pytest.approx(1349.65)
        assert any("RIVAS" in u["candidate"].upper() for u in out["unmatched"]["by_candidate"])
        assert not any("RIVAS" in c["candidate"].upper() for c in out["by_candidate"])

    def test_top_spenders_ranking(self):
        with patch.object(T, "execute_read", return_value=REYES_ROWS):
            out = T.ca_race_overview("S", "29", 2024)
        reyes_for = [t for t in out["top_spenders"]
                     if "REYES" in t["candidate"].upper() and t["side"] == "for"]
        assert reyes_for[0]["iec"] == "COOP AMER PHYS"
        assert reyes_for[0]["total"] == 39171.03

    def test_queries_dedup_view_ind_form_d(self):
        with patch.object(T, "execute_read", return_value=[]) as m:
            T.ca_race_overview("A", "68", 2026)
        sql = _captured_sql(m.call_args_list)
        assert "EXPN_CD_DEDUPED" in sql
        assert "EXPN_CODE = 'IND'" in sql
        assert "FORM_TYPE = 'D'" in sql
        assert "FILING_FILER" in sql
        assert "FILERNAME_CD" in sql

    def test_district_and_cycle_bound(self):
        with patch.object(T, "execute_read", return_value=[]) as m:
            T.ca_race_overview("S", "29", 2024)
        p = m.call_args.args[1]
        assert p["district"] == "29"
        assert p["cyc_start"] == "2024-01-01"
        assert p["cyc_end"] == "2024-12-31"

    def test_invalid_chamber_raises(self):
        with patch.object(T, "execute_read", return_value=[]):
            with pytest.raises(ValueError):
                T.ca_race_overview("X", "29", 2024)

    def test_empty_bucket_is_safe(self):
        with patch.object(T, "execute_read", return_value=[]):
            out = T.ca_race_overview("S", "1", 2024)
        assert out["race_roster"] == []
        assert out["by_candidate"] == []
        assert out["unmatched"]["total"] == 0.0


# --------------------------------------------------------------------------- #
#  registration
# --------------------------------------------------------------------------- #
def test_registered_in_tools_list():
    assert "ca_race_overview" in TOOLS


def test_registered_on_server():
    srv = _create_server()
    names = {t.name for t in srv._tool_manager.list_tools()}
    assert "ca_race_overview" in names


# --------------------------------------------------------------------------- #
#  live tests (exact ground truth)
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(
    os.environ.get("CFDB_LIVE_DB") != "1",
    reason="set CFDB_LIVE_DB=1 to run against a real CFDB database",
)
class TestCaRaceOverviewLive:
    def test_reyes_sd29_2024_exact(self):
        out = T.ca_race_overview("S", "29", 2024)
        reyes = next(
            c for c in out["by_candidate"]
            if "REYES" in c["candidate"].upper() and "GOMEZ" not in c["candidate"].upper()
        )
        assert reyes["for_total"] == 44106.08
        assert reyes["for_count"] == 47
        assert reyes["against_total"] == 0.0
        newman = next(c for c in out["by_candidate"] if "NEWMAN" in c["candidate"].upper())
        assert newman["for_total"] == 56993.92
        assert newman["for_count"] == 3
        # the Assembly (AD-29) Rivas lines are chamber bleed -> unmatched
        assert out["unmatched"]["total"] == pytest.approx(1506.83)
        assert any("RIVAS" in u["candidate"].upper() for u in out["unmatched"]["by_candidate"])

    def test_lopez_ad68_2026_structural(self):
        out = T.ca_race_overview("A", "68", 2026)
        lopez = next(c for c in out["by_candidate"] if "JESSIE LOPEZ" in c["candidate"].upper())
        assert lopez["for_total"] > 0
        # her own IEC surfaces among the top for-spenders
        assert any(
            "LOPEZ FOR ASSEMBLY" in t["iec"].upper()
            for t in out["top_spenders"] if t["side"] == "for"
        )
