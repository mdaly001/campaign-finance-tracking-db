"""Tests for the cross-source entity money tools (core/mcp/entity_tools.py).

execute_read is patched with a dispatch that recognizes each query by its
distinctive SQL fragments, so no live DB is required. We verify:
- entity resolution hits BOTH the federal and state registries.
- totals reconcile: total == sum(by_year) == sum(by_source).
- the display list is separate from the full totals.
- year / min_amount params are bound.
"""

from unittest.mock import patch

import pytest

import core.mcp.entity_tools as E


def _sql(calls):
    return [" ".join(c.args[0].split()).upper() for c in calls]


class TestResolve:
    def test_hits_both_registries(self):
        with patch.object(E, "execute_read", return_value=[]) as m:
            E._resolve_entity("Upset The Setup")
        sqls = " ".join(_sql(m.call_args_list))
        assert "FEC.FEC_COMMITTEES" in sqls      # federal
        assert "FILERNAME_CD" in sqls            # state

    def test_returns_federal_and_state_keys(self):
        def disp(sql, params=None):
            s = " ".join(sql.split()).upper()
            if "FEC.FEC_COMMITTEES" in s:
                return [{"id": "C00772244", "name": "UPSET THE SETUP", "state": "DC"}]
            return []
        with patch.object(E, "execute_read", side_effect=disp):
            r = E._resolve_entity("Upset The Setup")
        assert r["federal"][0]["id"] == "C00772244"
        assert r["state"] == []


class TestContributions:
    def _disp(self, sql, params=None):
        s = " ".join(sql.split()).upper()
        if "FEC.FEC_COMMITTEES" in s:
            return [{"id": "C1", "name": "TEST PAC", "state": "DC"}]
        if "FILERNAME_CD" in s:
            return []
        # grand totals (sum + count, no GROUP BY year)
        if "COALESCE(SUM(CONTRIBUTION_AMOUNT),0) AS AMT" in s:
            return [{"amt": 100000 if "INDIVIDUAL" in s else 50000,
                    "n": 10 if "INDIVIDUAL" in s else 5}]
        # year totals
        if "EXTRACT(YEAR FROM CONTRIBUTION_DATE)::INT AS YR" in s:
            return [{"yr": 2024, "amt": 100000 if "INDIVIDUAL" in s else 50000}]
        # display list (grouped by name)
        if "GROUP BY 1" in s:
            return [{"name": "BIG DONOR", "amount": 120000, "n": 12}]
        return []

    def test_totals_reconcile(self):
        with patch.object(E, "execute_read", side_effect=self._disp):
            out = E.entity_contributions("Test PAC")
        assert out["total"] == 150000.0
        assert round(sum(out["by_year"].values()), 2) == 150000.0
        assert out["by_source"]["federal"] == 150000.0
        assert out["by_source"]["state"] == 0.0
        assert out["record_count"] == 15

    def test_min_amount_bound(self):
        with patch.object(E, "execute_read", side_effect=self._disp) as m:
            E.entity_contributions("Test PAC", min_amount=50000)
        # the HAVING threshold param must be present on the list query
        assert any("MIN_AMOUNT" in s for s in _sql(m.call_args_list))

    def test_year_bound(self):
        with patch.object(E, "execute_read", side_effect=self._disp) as m:
            E.entity_contributions("Test PAC", year=2024)
        assert any(p.get("year") == 2024
                  for c in m.call_args_list for p in [c.args[1] if len(c.args) > 1 else {}])


class TestExpenditures:
    def _disp(self, sql, params=None):
        s = " ".join(sql.split()).upper()
        if "FEC.FEC_COMMITTEES" in s:
            return [{"id": "C1", "name": "TEST PAC", "state": "DC"}]
        if "FILERNAME_CD" in s:
            return []
        # 2026 deduped view grand
        if "FEC_IE_TARGETED_RESOLVED" in s and "COALESCE(SUM(EXPENDITURE_AMOUNT),0) AS AMT" in s:
            return [{"amt": 200000, "n": 6}]
        # 2024 deduped grand (sum over the DISTINCT-ON subquery)
        if "FEC_INDEPENDENT_EXPENDITURES" in s and "COALESCE(SUM(AMOUNT),0) AS AMT" in s:
            return [{"amt": 450000, "n": 109}]
        # 2024 by-year (deduped)
        if "FEC_INDEPENDENT_EXPENDITURES" in s and "EXTRACT(YEAR FROM ED)" in s:
            return [{"yr": 2024, "amt": 450000}]
        # 2026 by-year
        if "FEC_IE_TARGETED_RESOLVED" in s and "EXTRACT(YEAR FROM EXPENDITURE_DATE)" in s:
            return [{"yr": 2026, "amt": 200000}]
        # display list (grouped by target, side)
        if "GROUP BY 1, 2" in s:
            return [{"target": "RIKER, BRANDON", "side": "for", "amount": 200000, "n": 6}]
        return []

    def test_totals_reconcile(self):
        with patch.object(E, "execute_read", side_effect=self._disp):
            out = E.entity_expenditures("Test PAC")
        assert out["total"] == 650000.0
        assert round(sum(out["by_year"].values()), 2) == 650000.0
        assert out["by_source"]["federal"] == 650000.0
        assert out["record_count"] == 115

    def test_uses_deduped_2024(self):
        # the 2024 source must be collapsed by transaction_id (DISTINCT ON)
        with patch.object(E, "execute_read", side_effect=self._disp) as m:
            E.entity_expenditures("Test PAC")
        assert any("DISTINCT ON (COMMITTEE_ID, TRANSACTION_ID)" in s
                  for s in _sql(m.call_args_list))

    def test_no_match_returns_zero(self):
        def disp(sql, params=None):
            return []  # no federal, no state
        with patch.object(E, "execute_read", side_effect=disp):
            out = E.entity_expenditures("Nonexistent Org")
        assert out["total"] == 0.0
        assert out["expenditures"] == []


class TestReturnedContributions:
    def _disp(self, sql, params=None):
        s = " ".join(sql.split()).upper()
        if "FEC.FEC_COMMITTEES" in s:
            return [{"id": "C1", "name": "TEST PAC", "state": "DC"}]
        if "FILERNAME_CD" in s:
            return [{"id": "S1", "name": "TEST PAC"}]
        # Federal refunds: negative contributions negated to a positive
        # refund magnitude. Every federal query carries CONTRIBUTION_AMOUNT < 0.
        if "CONTRIBUTION_AMOUNT < 0" in s:
            if "COALESCE(SUM(AMT),0) AS AMT" in s:
                return [{"amt": 8000, "n": 4}]
            if "EXTRACT(YEAR FROM DT)::INT AS YR" in s:
                return [{"yr": 2024, "amt": 8000}]
            if "GROUP BY 1" in s:
                return [{"recipient": "DONOR A", "amount": 8000, "n": 4}]
        # State refunds: expn_all refund purposes (the only queries that
        # reference `purpose`). Positive amounts only.
        if "PURPOSE" in s:
            if "COALESCE(SUM(AMOUNT),0) AS AMT" in s:
                return [{"amt": 5000, "n": 3}]
            if "EXTRACT(YEAR FROM EXPENSE_DATE)::INT AS YR" in s:
                return [{"yr": 2023, "amt": 5000}]
            if "GROUP BY 1" in s:
                return [{"recipient": "DONOR B", "purpose": "Contribution Refund",
                         "amount": 5000, "n": 3}]
        return []

    def test_totals_reconcile(self):
        with patch.object(E, "execute_read", side_effect=self._disp):
            out = E.returned_contributions("Test PAC")
        assert out["total"] == 13000.0
        assert round(sum(out["by_year"].values()), 2) == 13000.0
        assert out["by_source"]["federal"] == 8000.0
        assert out["by_source"]["state"] == 5000.0
        assert out["record_count"] == 7

    def test_federal_uses_negative_contributions(self):
        with patch.object(E, "execute_read", side_effect=self._disp) as m:
            E.returned_contributions("Test PAC")
        assert any("CONTRIBUTION_AMOUNT < 0" in s for s in _sql(m.call_args_list))

    def test_state_excludes_negative_reversals(self):
        # the state side must filter amount > 0 so amendment reversals (whose
        # payee is the committee itself) don't turn the total negative.
        with patch.object(E, "execute_read", side_effect=self._disp) as m:
            E.returned_contributions("Test PAC")
        assert any("AMOUNT > 0" in s for s in _sql(m.call_args_list))

    def test_min_amount_bound(self):
        with patch.object(E, "execute_read", side_effect=self._disp) as m:
            E.returned_contributions("Test PAC", min_amount=5000)
        assert any("MIN_AMOUNT" in s for s in _sql(m.call_args_list))

    def test_no_match_returns_zero(self):
        with patch.object(E, "execute_read",
                         side_effect=lambda sql, params=None: []):
            out = E.returned_contributions("Nonexistent Org")
        assert out["total"] == 0.0
        assert out["returns"] == []
