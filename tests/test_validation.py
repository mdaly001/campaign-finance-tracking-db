"""Tests for core/etl/validation.py — RowValidator class."""

import pytest

from core.etl.validation import RowValidator


class TestRowValidatorRequired:
    """Test required field validation."""

    def test_required_field_present(self):
        """A record with all required fields should have no errors."""
        rules = {
            "name": [{"type": "required"}],
            "amount": [{"type": "required"}],
        }
        validator = RowValidator(rules)

        errors = validator.validate(
            {"name": "Alice", "amount": "100"},
            table_name="test",
        )
        assert errors == []

    def test_required_field_missing(self):
        """A record with a null required field should have an error."""
        rules = {
            "name": [{"type": "required"}],
        }
        validator = RowValidator(rules)

        errors = validator.validate(
            {"name": None, "amount": "100"},
            table_name="test",
        )
        assert len(errors) == 1
        assert "name" in errors[0]
        assert "required" in errors[0].lower()

    def test_required_field_empty_string(self):
        """An empty string counts as missing for required fields."""
        rules = {
            "name": [{"type": "required"}],
        }
        validator = RowValidator(rules)

        errors = validator.validate(
            {"name": "", "amount": "100"},
            table_name="test",
        )
        assert len(errors) == 1
        assert "name" in errors[0]


class TestRowValidatorNumeric:
    """Test numeric range validation."""

    def test_numeric_in_range(self):
        """A numeric value within min/max should pass."""
        rules = {
            "amount": [{"type": "numeric", "min": 0, "max": 1000}],
        }
        validator = RowValidator(rules)

        errors = validator.validate({"amount": "500"}, table_name="test")
        assert errors == []

    def test_numeric_below_min(self):
        """A numeric value below min should fail."""
        rules = {
            "amount": [{"type": "numeric", "min": 0, "max": 1000}],
        }
        validator = RowValidator(rules)

        errors = validator.validate({"amount": "-10"}, table_name="test")
        assert len(errors) == 1
        assert "min" in errors[0].lower()

    def test_numeric_above_max(self):
        """A numeric value above max should fail."""
        rules = {
            "amount": [{"type": "numeric", "min": 0, "max": 1000}],
        }
        validator = RowValidator(rules)

        errors = validator.validate({"amount": "5000"}, table_name="test")
        assert len(errors) == 1
        assert "max" in errors[0].lower()

    def test_numeric_non_numeric_value(self):
        """A non-numeric string should fail with an error."""
        rules = {
            "amount": [{"type": "numeric"}],
        }
        validator = RowValidator(rules)

        errors = validator.validate({"amount": "not_a_number"}, table_name="test")
        assert len(errors) == 1
        assert "not numeric" in errors[0].lower()

    def test_numeric_null_skipped(self):
        """Null values should not trigger numeric validation."""
        rules = {
            "amount": [{"type": "numeric", "min": 0}],
        }
        validator = RowValidator(rules)

        errors = validator.validate({"amount": None}, table_name="test")
        assert errors == []


class TestRowValidatorDate:
    """Test date format validation."""

    def test_valid_date(self):
        """A correctly formatted date should pass."""
        rules = {
            "filing_date": [{"type": "date", "format": "%Y-%m-%d"}],
        }
        validator = RowValidator(rules)

        errors = validator.validate(
            {"filing_date": "2024-01-15"},
            table_name="test",
        )
        assert errors == []

    def test_invalid_date_format(self):
        """A date with wrong format should fail."""
        rules = {
            "filing_date": [{"type": "date", "format": "%Y-%m-%d"}],
        }
        validator = RowValidator(rules)

        errors = validator.validate(
            {"filing_date": "15-01-2024"},
            table_name="test",
        )
        assert len(errors) == 1
        assert "does not match" in errors[0].lower()

    def test_null_date_skipped(self):
        """Null dates should not trigger date validation."""
        rules = {
            "filing_date": [{"type": "date"}],
        }
        validator = RowValidator(rules)

        errors = validator.validate({"filing_date": None}, table_name="test")
        assert errors == []


class TestRowValidatorEnum:
    """Test enum validation."""

    def test_valid_enum(self):
        """A value in the enum should pass."""
        rules = {
            "status": [{"type": "enum", "values": ["A", "B", "C"]}],
        }
        validator = RowValidator(rules)

        errors = validator.validate({"status": "B"}, table_name="test")
        assert errors == []

    def test_invalid_enum(self):
        """A value not in the enum should fail."""
        rules = {
            "status": [{"type": "enum", "values": ["A", "B", "C"]}],
        }
        validator = RowValidator(rules)

        errors = validator.validate({"status": "X"}, table_name="test")
        assert len(errors) == 1
        assert "not in" in errors[0].lower()


class TestRowValidatorLength:
    """Test length validation."""

    def test_valid_length(self):
        """A string within length bounds should pass."""
        rules = {
            "name": [{"type": "length", "min": 1, "max": 50}],
        }
        validator = RowValidator(rules)

        errors = validator.validate({"name": "Alice"}, table_name="test")
        assert errors == []

    def test_length_too_short(self):
        """A string shorter than min should fail."""
        rules = {
            "name": [{"type": "length", "min": 2, "max": 50}],
        }
        validator = RowValidator(rules)

        errors = validator.validate({"name": "A"}, table_name="test")
        assert len(errors) == 1
        assert "length" in errors[0].lower()

    def test_length_too_long(self):
        """A string longer than max should fail."""
        rules = {
            "name": [{"type": "length", "min": 1, "max": 3}],
        }
        validator = RowValidator(rules)

        errors = validator.validate({"name": "Alice"}, table_name="test")
        assert len(errors) == 1
        assert "length" in errors[0].lower()

    def test_null_length_skipped(self):
        """Null values should not trigger length validation."""
        rules = {
            "name": [{"type": "length", "min": 1}],
        }
        validator = RowValidator(rules)

        errors = validator.validate({"name": None}, table_name="test")
        assert errors == []


class TestRowValidatorBatch:
    """Test batch validation."""

    def test_batch_mixed_valid_invalid(self):
        """A batch with mixed valid/invalid records should separate them."""
        rules = {
            "amount": [{"type": "numeric", "min": 0}],
            "name": [{"type": "required"}],
        }
        validator = RowValidator(rules)

        records = [
            {"name": "Alice", "amount": "100"},       # valid
            {"name": "Bob", "amount": "-50"},           # invalid (negative)
            {"name": "Charlie", "amount": "200"},       # valid
            {"name": None, "amount": "300"},            # invalid (required missing)
        ]

        valid, invalid = validator.validate_batch(records)

        assert len(valid) == 2
        assert len(invalid) == 2

        # Invalid records should have _validation_errors
        for rec in invalid:
            assert "_validation_errors" in rec
            assert len(rec["_validation_errors"]) > 0

    def test_batch_all_valid(self):
        """A batch of all valid records should return empty invalid list."""
        rules = {
            "name": [{"type": "required"}],
        }
        validator = RowValidator(rules)

        records = [
            {"name": "Alice"},
            {"name": "Bob"},
        ]

        valid, invalid = validator.validate_batch(records)

        assert len(valid) == 2
        assert len(invalid) == 0

    def test_batch_all_invalid(self):
        """A batch of all invalid records should return empty valid list."""
        rules = {
            "name": [{"type": "required"}],
        }
        validator = RowValidator(rules)

        records = [
            {"name": None},
            {"name": ""},
        ]

        valid, invalid = validator.validate_batch(records)

        assert len(valid) == 0
        assert len(invalid) == 2


class TestRowValidatorNoRules:
    """Test validator with no rules (should pass everything)."""

    def test_no_rules_passes_all(self):
        """A validator with no rules should never produce errors."""
        validator = RowValidator()

        errors = validator.validate(
            {"anything": "goes"},
            table_name="test",
        )
        assert errors == []

    def test_no_rules_batch_passes_all(self):
        """Batch validation with no rules should pass all records."""
        validator = RowValidator()

        records = [{"a": i} for i in range(5)]
        valid, invalid = validator.validate_batch(records)

        assert len(valid) == 5
        assert len(invalid) == 0


class TestRowValidatorMultipleErrors:
    """Test that multiple errors on the same record are collected."""

    def test_multiple_rule_failures(self):
        """A record failing multiple rules should return all errors."""
        rules = {
            "amount": [
                {"type": "required"},
                {"type": "numeric"},
                {"type": "length", "min": 1, "max": 5},
            ],
        }
        validator = RowValidator(rules)

        errors = validator.validate({"amount": "123456"}, table_name="test")
        # Should have at least the length error (> 5)
        length_errors = [e for e in errors if "length" in e.lower()]
        assert len(length_errors) >= 1
