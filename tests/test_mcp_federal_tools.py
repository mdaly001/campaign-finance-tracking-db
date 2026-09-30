"""Tests for the federal (FEC) MCP tools in core/mcp/federal_tools.py.

Covers:
- Each of the 5 fec_* tools returns the correct shape from canned rows.
- Every tool queries the RESOLVED view (fec.fec_ie_targeted_resolved), which is
  built on the de-duplicated view — the core dedup + name-resolution guarantee.
  No tool touches the raw fec.fec_ie_targeted table.
- Parameter handling: cycle filter, date window, side mapping, invalid side.
- The MCP server registers all federal tools.

execute_read is patched in the federal_tools namespace (the tools import it
directly), so no live database is required.
"""

from unittest.mock import patch

import pytest

import core.mcp.federal_tools as F
from core.mcp.server import TOOLS, _create_server

# The tools must read the resolved view (dedup + candidate-name resolution),
# which is itself built on fec.fec_ie_targeted_dedup.
RESOLVED_VIEW = "FEC.FEC_IE_TARGETED_RESOLVED"


def _captured_sql(calls):
    return " ".join(" ".join(c.args[0].split()).upper() for c in calls)


# --------------------------------------------------------------------------- #
#  1. fec_find_candidate
# --------------------------------------------------------------------------- #
class TestFindCandidate:
    def test_shapes_result(self):
        rows = [{
            "candidate_id": "H6CA22190", "candidate_name": "VILLEGAS, RANDY",
            "office": "H", "office_state": "CA", "office_district": "22",
            "party": "DEM", "ie_records": 223,
        }]
        with patch.object(F, "execute_read", return_value=rows) as m:
            out = F.fec_find_candidate("Villegas", state="CA", district="22")
        assert out[0]["candidate_id"] == "H6CA22190"
        assert out[0]["party"] == "DEM"
        # state + district filters were bound
        assert m.call_args.args[1]["state"] == "CA"
        assert m.call_args.args[1]["district"] == "22"

    def test_uses_resolved_view(self):
        with patch.object(F, "execute_read", return_value=[]) as m:
            F.fec_find_candidate("Nobody")
        assert RESOLVED_VIEW in _captured_sql(m.call_args_list)

    def test_cycle_filter_bound(self):
        with patch.object(F, "execute_read", return_value=[]) as m:
            F.fec_find_candidate("X", cycle=2026)
        assert m.call_args.args[1]["cycle"] == 2026

    def test_fuzzy_params_bound(self):
        # The fuzzy path binds the raw query (:q) and the threshold (:min_sim)
        # alongside the ILIKE pattern (:name), and returns a match score.
        with patch.object(F, "execute_read", return_value=[]) as m:
            F.fec_find_candidate("Villegaz", min_similarity=0.25)
        p = m.call_args.args[1]
        assert p["q"] == "Villegaz"
        assert p["name"] == "%Villegaz%"
        assert p["min_sim"] == 0.25
        sql = _captured_sql(m.call_args_list)
        assert "SIMILARITY" in sql  # trigram fuzzy is actually used


# --------------------------------------------------------------------------- #
#  2. fec_ie_summary
# --------------------------------------------------------------------------- #
class TestIeSummary:
    def test_totals_and_net(self):
        with patch.object(F, "execute_read", return_value=[
            {"for_total": 3533042.63, "against_total": 3306101.93,
             "for_count": 187, "against_count": 38}
        ]):
            out = F.fec_ie_summary("H6CA22190", cycle=2026)
        assert out["for_total"] == 3533042.63
        assert out["against_total"] == 3306101.93
        assert out["net"] == pytest.approx(226940.70, rel=1e-6)
        assert out["for_count"] == 187

    def test_date_window_params(self):
        with patch.object(F, "execute_read", return_value=[
            {"for_total": 51065.38, "against_total": 0.0,
             "for_count": 28, "against_count": 0}
        ]) as m:
            F.fec_ie_summary("H6CA22190", cycle=2026,
                             start_date="2026-06-01", end_date="2026-07-01")
        p = m.call_args.args[1]
        assert p["start"] == "2026-06-01"
        assert p["end"] == "2026-07-01"
        assert RESOLVED_VIEW in _captured_sql(m.call_args_list)

    def test_uses_resolved_view(self):
        with patch.object(F, "execute_read", return_value=[
            {"for_total": 0, "against_total": 0, "for_count": 0, "against_count": 0}
        ]) as m:
            F.fec_ie_summary("X")
        assert RESOLVED_VIEW in _captured_sql(m.call_args_list)


# --------------------------------------------------------------------------- #
#  3. fec_top_spending
# --------------------------------------------------------------------------- #
class TestTopSpending:
    def test_against_maps_to_O(self):
        with patch.object(F, "execute_read", return_value=[]) as m:
            F.fec_top_spending("H6CA22190", side="against")
        assert m.call_args.args[1]["op"] == "O"

    def test_for_maps_to_S(self):
        with patch.object(F, "execute_read", return_value=[]) as m:
            F.fec_top_spending("H6CA22190", side="for")
        assert m.call_args.args[1]["op"] == "S"

    def test_invalid_side_raises(self):
        with pytest.raises(ValueError):
            F.fec_top_spending("H6CA22190", side="sideways")

    def test_shapes_rows(self):
        with patch.object(F, "execute_read", return_value=[
            {"spender_id": "C00908707", "spender_name": "BDA PAC",
             "ies": 6, "total": 1200000.0}
        ]):
            out = F.fec_top_spending("H6CA22190", side="against")
        assert out[0]["spender_name"] == "BDA PAC"
        assert out[0]["total"] == 1200000.0

    def test_uses_resolved_view(self):
        with patch.object(F, "execute_read", return_value=[]) as m:
            F.fec_top_spending("X")
        assert RESOLVED_VIEW in _captured_sql(m.call_args_list)


# --------------------------------------------------------------------------- #
#  4. fec_spender_breakdown
# --------------------------------------------------------------------------- #
class TestSpenderBreakdown:
    def _dispatch(self, sql, params=None):
        s = " ".join(sql.split()).upper()
        if "MAX(C.COMMITTEE_NAME)" in s:
            return [{"nm": "NEW DEMOCRAT MAJORITY"}]
        if "SUM(EXPENDITURE_AMOUNT),0) AS TOTAL, COUNT(*) AS N" in s:
            return [{"total": 757805.91, "n": 4}]
        if "GROUP BY PAYEE" in s:
            return [{"payee": "GPS IMPACT", "n": 4, "total": 757805.91}]
        if "GROUP BY PURPOSE" in s:
            return [
                {"purpose": "TELEVISION ADVERTISING BUY", "n": 3, "total": 740946.0},
                {"purpose": "TELEVISION ADVERTISING PRODUCTION", "n": 1, "total": 16859.91},
            ]
        if "GROUP BY RESOLVED_CANDIDATE_ID, SIDE" in s:
            return [{"candidate_id": "H6CA22190", "candidate_name": "VILLEGAS, RANDY",
                     "side": "against", "n": 4, "total": 757805.91}]
        return []

    def test_full_structure(self):
        with patch.object(F, "execute_read", side_effect=self._dispatch):
            out = F.fec_spender_breakdown("C00859660",
                                         candidate_id="H6CA22190", cycle=2026)
        assert out["spender_name"] == "NEW DEMOCRAT MAJORITY"
        assert out["total"] == 757805.91
        assert out["ie_count"] == 4
        assert out["by_payee"][0]["payee"] == "GPS IMPACT"
        assert out["by_purpose"][0]["total"] == 740946.0
        assert out["by_target"][0]["side"] == "against"

    def test_uses_resolved_view(self):
        with patch.object(F, "execute_read", side_effect=self._dispatch) as m:
            F.fec_spender_breakdown("C00859660")
        assert RESOLVED_VIEW in _captured_sql(m.call_args_list)


# --------------------------------------------------------------------------- #
#  5. fec_race_overview
# --------------------------------------------------------------------------- #
class TestRaceOverview:
    def test_totals_and_candidates(self):
        rows = [
            {"candidate_id": "H6CA22190", "candidate_name": "VILLEGAS, RANDY",
             "for_total": 3532323.35, "against_total": 3306101.93,
             "for_count": 185, "against_count": 38},
            {"candidate_id": "H6CA22208", "candidate_name": "BAINS, JASMEET DR.",
             "for_total": 2822233.42, "against_total": 1270311.83,
             "for_count": 64, "against_count": 13},
        ]
        with patch.object(F, "execute_read", return_value=rows):
            out = F.fec_race_overview("CA", "22", cycle=2026)
        assert out["state"] == "CA"
        assert out["district"] == "22"
        assert out["total_for"] == pytest.approx(6354556.77, rel=1e-6)
        assert out["total_against"] == pytest.approx(4576413.76, rel=1e-6)
        assert len(out["by_candidate"]) == 2

    def test_uses_resolved_view(self):
        with patch.object(F, "execute_read", return_value=[]) as m:
            F.fec_race_overview("CA", "22")
        assert RESOLVED_VIEW in _captured_sql(m.call_args_list)


# --------------------------------------------------------------------------- #
#  6. fec_ie_by_org
# --------------------------------------------------------------------------- #
class TestIeByOrg:
    def test_shapes_and_purpose_string(self):
        rows = [
            {"spender_id": "C1", "org": "DMFI PAC", "candidate_id": "H1",
             "candidate": "A, CAND", "side": "against", "amount": 2000000.0,
             "ie_count": 5},
            {"spender_id": "C2", "org": "PROJECT 218", "candidate_id": "H2",
             "candidate": "B, CAND", "side": "for", "amount": 900000.0,
             "ie_count": 3},
        ]
        with patch.object(F, "execute_read", return_value=rows):
            out = F.fec_ie_by_org("CA", "48", cycle=2026, election_type="primary")
        assert out[0]["purpose"] == "in opposition to A, CAND"
        assert out[1]["purpose"] == "in support of B, CAND"
        assert out[0]["amount"] == 2000000.0
        assert out[0]["ie_count"] == 5

    def test_min_amount_and_phase_bound(self):
        with patch.object(F, "execute_read", return_value=[]) as m:
            F.fec_ie_by_org("CA", "48", cycle=2026,
                            election_type="primary", min_amount=50000)
        p = m.call_args.args[1]
        assert p["min_amount"] == 50000
        assert p["ephase"] == "P"  # 'primary' mapped to the P phase letter
        sql = _captured_sql(m.call_args_list)
        assert "HAVING" in sql  # threshold applied at the group level
        assert RESOLVED_VIEW in sql

    def test_uses_resolved_view(self):
        with patch.object(F, "execute_read", return_value=[]) as m:
            F.fec_ie_by_org("CA", "48")
        assert RESOLVED_VIEW in _captured_sql(m.call_args_list)


# --------------------------------------------------------------------------- #
#  election_type phase mapping
# --------------------------------------------------------------------------- #
class TestElectionTypePhase:
    @pytest.mark.parametrize("name,letter", [
        ("primary", "P"), ("general", "G"), ("special", "S"),
        ("runoff", "R"), ("convention", "C"), ("other", "O"),
        ("P2026", "P"),  # raw code with year still resolves to the letter
    ])
    def test_phase_map(self, name, letter):
        clause, params = F._election_type_clause(name)
        assert params["ephase"] == letter
        assert "left(" in clause.lower()

    def test_none_is_noop(self):
        assert F._election_type_clause(None) == ("", {})

    def test_invalid_phase_raises(self):
        with pytest.raises(ValueError):
            F._election_type_clause("bogus")

    def test_summary_binds_phase(self):
        with patch.object(F, "execute_read", return_value=[
            {"for_total": 0, "against_total": 0, "for_count": 0, "against_count": 0}
        ]) as m:
            F.fec_ie_summary("H1", election_type="general")
        assert m.call_args.args[1]["ephase"] == "G"


# --------------------------------------------------------------------------- #
#  Server registration
# --------------------------------------------------------------------------- #
def test_server_registers_federal_tools():
    fed = {"fec_find_candidate", "fec_ie_summary", "fec_top_spending",
           "fec_spender_breakdown", "fec_race_overview", "fec_ie_by_org"}
    assert fed.issubset(set(TOOLS))
    srv = _create_server()
    assert srv.name == "cfdb"
