"""Golden number tests: verify known totals within tolerance.

Uses samples/golden_numbers.json which contains expected values
for the sample test data loaded by test_integration.py.
"""

from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_golden_numbers():
    """Load golden_numbers.json from samples/."""
    path = PROJECT_ROOT / "samples" / "golden_numbers.json"
    with open(path) as f:
        data = json.load(f)
    return data["golden_numbers"]


class TestGoldenNumbers:
    """Verify golden numbers match sample test data expectations.

    These tests check that the golden_numbers.json file is well-formed
    and contains reasonable values. Actual numeric verification happens
    in test_integration.py with real SQL queries.
    """

    def test_golden_numbers_file_exists(self):
        """Golden numbers file should exist."""
        path = PROJECT_ROOT / "samples" / "golden_numbers.json"
        assert path.exists(), "samples/golden_numbers.json not found"

    def test_golden_numbers_file_valid_json(self):
        """Golden numbers file should be valid JSON."""
        numbers = _load_golden_numbers()
        assert isinstance(numbers, list), "golden_numbers should be a list"
        assert len(numbers) > 0, "golden_numbers should not be empty"

    def test_golden_numbers_have_required_fields(self):
        """Each golden number entry should have all required fields."""
        required_fields = [
            "cycle",
            "table",
            "metric",
            "expected_value",
            "tolerance",
            "verification_date",
            "verified_by",
            "source_url",
        ]
        for entry in _load_golden_numbers():
            for field in required_fields:
                assert field in entry, (
                    f"Missing required field '{field}' in golden number "
                    f"entry: {entry.get('metric', 'unknown')}"
                )

    def test_golden_numbers_values_reasonable(self):
        """Expected values and tolerances should be non-negative."""
        for entry in _load_golden_numbers():
            assert entry["expected_value"] >= 0, (
                f"expected_value should be non-negative for {entry['metric']}"
            )
            assert entry["tolerance"] >= 0, (
                f"tolerance should be non-negative for {entry['metric']}"
            )

    def test_golden_numbers_cycles_match(self):
        """All golden numbers should reference cycle 2024 (Phase 1)."""
        for entry in _load_golden_numbers():
            assert entry["cycle"] == 2024, (
                f"Phase 1 golden numbers should use cycle 2024, got {entry['cycle']}"
            )
