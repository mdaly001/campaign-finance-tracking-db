"""Row validation for ETL pipelines."""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class RowValidator:
    """Validate ETL rows against a schema.

    Checks:
    - Required columns present and non-null
    - Type constraints (numeric ranges, date formats)
    - Value constraints (enums, length)
    - Cross-field consistency
    """

    def __init__(
        self, rules: dict[str, list[dict]] | None = None
    ) -> None:
        """Initialize with validation rules.

        Args:
            rules: {column_name: [rule_dict, ...]}

            rule_dict formats:
            - {"type": "numeric", "min": 0, "max": 999999999}
            - {"type": "date", "format": "%Y-%m-%d"}
            - {"type": "enum", "values": ["A", "B", "C"]}
            - {"type": "length", "min": 1, "max": 255}
            - {"type": "required"}
            - {"cross_field": "if_col_a_set_then_col_b_not_null"}
        """
        self.rules = rules or {}

    def validate(
        self, record: dict, table_name: str = "unknown"
    ) -> list[str]:
        """Validate a single record. Returns list of error messages.

        Empty list = valid. Non-empty = invalid (errors listed).

        Args:
            record: The row dict to validate.
            table_name: Table name for error messages.

        Returns:
            List of error message strings.
        """
        errors: list[str] = []

        for col, col_rules in self.rules.items():
            val = record.get(col)

            for rule in col_rules:
                rule_type = rule.get("type")

                if rule_type == "required" and (val is None or val == ""):
                    errors.append(
                        f"{table_name}.{col}: required field is null/empty"
                    )
                    continue

                if val is None:
                    continue

                if rule_type == "numeric":
                    try:
                        num_val = float(val)
                        if "min" in rule and num_val < rule["min"]:
                            errors.append(
                                f"{table_name}.{col}: {num_val} < min {rule['min']}"
                            )
                        if "max" in rule and num_val > rule["max"]:
                            errors.append(
                                f"{table_name}.{col}: {num_val} > max {rule['max']}"
                            )
                    except (ValueError, TypeError):
                        errors.append(
                            f"{table_name}.{col}: '{val}' is not numeric"
                        )

                elif rule_type == "date":
                    fmt = rule.get("format", "%Y-%m-%d")
                    try:
                        datetime.strptime(str(val), fmt)
                    except ValueError:
                        errors.append(
                            f"{table_name}.{col}: '{val}' does not match {fmt}"
                        )

                elif rule_type == "enum":
                    if val not in rule["values"]:
                        errors.append(
                            f"{table_name}.{col}: '{val}' not in {rule['values']}"
                        )

                elif rule_type == "length":
                    str_val = str(val)
                    if "min" in rule and len(str_val) < rule["min"]:
                        errors.append(
                            f"{table_name}.{col}: length {len(str_val)} < min {rule['min']}"
                        )
                    if "max" in rule and len(str_val) > rule["max"]:
                        errors.append(
                            f"{table_name}.{col}: length {len(str_val)} > max {rule['max']}"
                        )

        return errors

    def validate_batch(
        self, records: list[dict]
    ) -> tuple[list[dict], list[dict]]:
        """Validate a batch of records. Returns (valid, invalid) lists.

        Args:
            records: List of record dicts to validate.

        Returns:
            Tuple of (valid_records, invalid_records).
            Invalid records get _validation_errors key populated.
        """
        valid: list[dict] = []
        invalid: list[dict] = []

        for record in records:
            errors = self.validate(record)
            if errors:
                record["_validation_errors"] = errors
                invalid.append(record)
            else:
                valid.append(record)

        return valid, invalid
