"""Live regression tests against a real CFDB database.

These assert the acceptance criteria from the GitHub issues against actual
CAL-ACCESS data, which the mocked unit tests cannot do. They are skipped
unless ``CFDB_LIVE_DB=1`` is set, so the default suite stays fast and
hermetic.

Run them against the isolated testing stack:

    CFDB_LIVE_DB=1 python -m pytest tests/test_issue_regressions_live.py -q

inside the ``cfdbtest-mcp`` container (which already points DATABASE_URL at
the copied cluster), or from the host with

    DATABASE_URL=postgresql://cfdb_reader:cfdb_reader@127.0.0.1:5433/cfdb \\
      CFDB_LIVE_DB=1 python -m pytest tests/test_issue_regressions_live.py -q
"""

from __future__ import annotations

import os

import pytest

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("CFDB_LIVE_DB") != "1",
        reason="set CFDB_LIVE_DB=1 to run against a real CFDB database",
    ),
    pytest.mark.live,
]


# --------------------------------------------------------------------------- #
#  Issue #11 — IEC expenditure tools returned $0
# --------------------------------------------------------------------------- #


class TestIssue11IndependentExpenditureCommittee:
    """total_expenditures must not zero out an independent-expenditure committee.

    Committee 1489398 (OC Action IEC, AD 68 / Jessie Lopez) filed four
    460s in cycle 2026 disclosing $439,840.62 of independent
    expenditures. Before the fix the tool returned 0 because the dated
    lines are Form D (excluded by the form filter) and the Form E lines it
    did select are NULL-dated (excluded by the cycle filter).
    """

    IEC_ID = "1489398"
    IEC_2026_TOTAL = 439840.62
    IEC_2026_TXNS = 10

    def test_iec_total_is_not_zero(self):
        from core.mcp.tools import total_expenditures

        result = total_expenditures(self.IEC_ID, 2026)
        assert result["total_amount"] != 0.0, (
            "issue #11 regression: IEC reported $0 despite disclosed spend"
        )

    def test_iec_total_matches_disclosed_amount(self):
        from core.mcp.tools import total_expenditures

        result = total_expenditures(self.IEC_ID, 2026)
        assert result["total_amount"] == pytest.approx(self.IEC_2026_TOTAL, abs=0.01)
        assert result["transaction_count"] == self.IEC_2026_TXNS

    def test_iec_total_is_attributed_to_form_d(self):
        from core.mcp.tools import total_expenditures

        result = total_expenditures(self.IEC_ID, 2026)
        assert "D" in (result.get("form_types") or [])
        # The reported total came from the beneficiary side; the vendor
        # side is undated and therefore contributes nothing here.
        assert result["form_d_total"] == pytest.approx(self.IEC_2026_TOTAL, abs=0.01)
        assert result["form_e_total"] == pytest.approx(0.0, abs=0.01)

    def test_iec_discloses_the_undated_vendor_side_gap(self):
        """The caller must be told the vendor-side money is not included."""
        from core.mcp.tools import total_expenditures

        result = total_expenditures(self.IEC_ID, 2026)
        notes = " ".join(result.get("notes") or []).lower()
        assert "not included" in notes or "undated" in notes or "no date" in notes


class TestIssue11RegularCommitteesUnchanged:
    """The fix must not move a regular committee's already-correct total.

    These committees have dated Form E lines in every 2026 filing, so the
    Form D fallback never fires. Values below were measured against the
    copied cluster with both the old and the new query and are identical.
    """

    CONTROLS = [
        ("1275358", 8, 82501800.00),
        ("1045082", 55, 105091872.35),
        ("1487425", 64, 26839994.77),
    ]

    @pytest.mark.parametrize("committee_id,txns,total", CONTROLS)
    def test_control_total_unchanged(self, committee_id, txns, total):
        from core.mcp.tools import total_expenditures

        result = total_expenditures(committee_id, 2026)
        assert result["transaction_count"] == txns
        assert result["total_amount"] == pytest.approx(total, abs=0.01)

    @pytest.mark.parametrize("committee_id,txns,total", CONTROLS)
    def test_control_has_no_form_d_fallback(self, committee_id, txns, total):
        """A committee counted on dated vendor lines must not gain Form D money."""
        from core.mcp.tools import total_expenditures

        result = total_expenditures(committee_id, 2026)
        assert result["form_d_total"] == pytest.approx(0.0, abs=0.01)
        assert result["form_e_total"] == pytest.approx(total, abs=0.01)


class TestIssue11VendorToolsExcludeRestatements:
    """vendor_revenue must not count Form D / Form G restatements of Form E money."""

    def test_vendor_revenue_counts_only_vendor_schedules(self):
        from core.mcp.db import execute_read
        from core.mcp.tools import vendor_revenue

        vendor = "GENERATIVE STRATEGIES"
        rows = vendor_revenue(vendor, limit=5)
        assert rows, f"expected {vendor} to appear in vendor revenue"

        # Recompute the way the tool now must: E/F461P5 only.
        expected = execute_read(
            "SELECT COALESCE(SUM(e.amount), 0) AS total, COUNT(*) AS n "
            "FROM expn_cd_deduped e "
            "WHERE e.form_type IN ('E', 'F461P5') "
            "AND UPPER(COALESCE(e.payee_naml, '')) LIKE :pat",
            {"pat": f"%{vendor}%"},
        )
        got_total = sum(r["total"] for r in rows)
        assert got_total == pytest.approx(float(expected[0]["total"]), abs=0.01)

    def test_vendor_revenue_excludes_form_d_money(self):
        """A payee that also appears on Form D must not be credited for it.

        "Alliance for a Better California" is a payee on both Form D
        (beneficiary tracking) and the vendor schedules in this corpus:

            Form D    141 rows  $27,272,125.96
            Form E    112 rows  $15,367,346.28
            F461P5      1 row       $2,492.38
            F465P3      1 row      $581,782.84

        Counting every form reported this vendor receiving ~$43.2M; the
        vendor-schedule-only figure is $15,369,838.66. The Form D money is
        the same spend seen from the beneficiary side, so counting it here
        is a straight double-count.
        """
        from core.mcp.tools import vendor_revenue

        payee = "Alliance for a Better California"
        vendor_schedule_total = 15367346.28 + 2492.38

        rows = vendor_revenue(payee, limit=20)
        got = next((r["total"] for r in rows if r["vendor_name"] == payee), None)
        assert got is not None, f"{payee} should be reported by vendor_revenue"

        assert got == pytest.approx(vendor_schedule_total, abs=0.01), (
            "vendor_revenue is crediting non-vendor schedules "
            f"(got {got}, expected {vendor_schedule_total})"
        )


# --------------------------------------------------------------------------- #
#  Issue #7 — fuzzy_name_search crashed on NULL dates
# --------------------------------------------------------------------------- #


class TestIssue7NullDates:
    """fuzzy_name_search must not raise TypeError on rows with NULL dates."""

    def test_does_not_crash_on_a_name_with_undated_rows(self):
        from core.mcp.tools import fuzzy_name_search

        # "Jessie Lopez" matches rows whose Form E lines are NULL-dated,
        # which previously raised:
        #   TypeError: int() argument must be ... not 'NoneType'
        result = fuzzy_name_search("Jessie Lopez", limit=5)
        assert isinstance(result, dict)
        assert result.get("query") == "Jessie Lopez"

    def test_undated_money_is_reported_not_dropped(self):
        """NULL-dated rows must surface under totals_undated, not vanish."""
        from core.mcp.tools import fuzzy_name_search

        result = fuzzy_name_search("Jessie Lopez", limit=10)
        by_year = result.get("totals_by_year") or {}
        # No year key may be None / "None".
        assert "None" not in by_year
        for key in by_year:
            assert key.isdigit(), f"unexpected non-year key in totals_by_year: {key!r}"
        undated = result.get("totals_undated")
        if undated:
            assert undated["n"] > 0
            assert "totals_by_year" in " ".join(result.get("notes") or [])

    def test_year_totals_stay_numeric(self):
        from core.mcp.tools import fuzzy_name_search

        result = fuzzy_name_search("Jessie Lopez", limit=10)
        for year, slot in (result.get("totals_by_year") or {}).items():
            assert isinstance(int(year), int)
            for field in ("in", "out", "net", "n"):
                assert field in slot


class TestExcludeRefundsStillWorks:
    """Red-hat catch: the refund exclusion must apply to the whole predicate.

    When the Form D fallback was added, the outer WHERE became
    ``A OR (B) AND <refund>``. AND binds tighter than OR, so the refund
    clause silently stopped filtering Form E / F461P5 rows and
    ``exclude_refunds=True`` became a no-op. The disjunct must be
    parenthesised so the refund clause gates both branches.
    """

    # A filer that carries refund-matching dated Form E rows in 2026.
    FILER = "1488732"
    CYCLE = 2026
    DEFAULT_TXNS = 21
    DEFAULT_TOTAL = 7755450.00
    EXCLUDED_TXNS = 12
    EXCLUDED_TOTAL = 7186863.50

    def test_flag_actually_excludes_refund_rows(self):
        from core.mcp.tools import total_expenditures

        default = total_expenditures(self.FILER, self.CYCLE)
        excluded = total_expenditures(self.FILER, self.CYCLE, exclude_refunds=True)

        assert default["total_amount"] == pytest.approx(self.DEFAULT_TOTAL, abs=0.01)
        assert default["transaction_count"] == self.DEFAULT_TXNS

        # The flag must change the answer — it was silently doing nothing.
        assert excluded["transaction_count"] < default["transaction_count"], (
            "exclude_refunds=True removed no rows: the refund clause is not "
            "gating the vendor branch (operator precedence)"
        )
        assert excluded["total_amount"] < default["total_amount"]
        assert excluded["total_amount"] == pytest.approx(self.EXCLUDED_TOTAL, abs=0.01)
        assert excluded["transaction_count"] == self.EXCLUDED_TXNS

    def test_refund_money_is_the_difference(self):
        from core.mcp.tools import total_expenditures

        default = total_expenditures(self.FILER, self.CYCLE)
        excluded = total_expenditures(self.FILER, self.CYCLE, exclude_refunds=True)
        removed = default["total_amount"] - excluded["total_amount"]
        assert removed == pytest.approx(
            self.DEFAULT_TOTAL - self.EXCLUDED_TOTAL, abs=0.01
        )
        assert removed > 0
