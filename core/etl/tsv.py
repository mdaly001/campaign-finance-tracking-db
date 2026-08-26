"""TSVReader: tab-delimited file parser with type coercion support."""

import csv
from typing import Any


class TSVReader:
    """Reads tab-delimited TSV into list of dicts.

    Handles:
    - Header detection from first line
    - Empty strings -> None
    - Type coercion hints (optional)
    """

    def __init__(
        self,
        has_header: bool = True,
        empty_to_none: bool = True,
        coercion_hints: dict[str, type] | None = None,
    ):
        """
        Args:
            has_header: If True, treat the first row as column headers.
            empty_to_none: If True, replace empty strings with None.
            coercion_hints: Optional dict mapping column names to desired
                Python types (int, float, str, bool, date).
        """
        self.has_header = has_header
        self.empty_to_none = empty_to_none
        self.coercion_hints = coercion_hints or {}

    def read_string(self, tsv_string: str) -> list[dict[str, Any]]:
        """Parse a TSV string into a list of dicts.

        Args:
            tsv_string: Tab-delimited text content.

        Returns:
            List of dicts, each representing a row.
        """
        # The real SOS export occasionally contains NUL (0x00) bytes inside
        # free-text fields. Postgres rejects NULs in every string type, so
        # strip them at parse time (they are pure data corruption).
        tsv_string = tsv_string.replace("\x00", "")

        # Lines are CRLF-terminated, but free-text fields ALSO contain
        # embedded control characters (bare \r, \x0b VT, \x1c-\x1e FS/GS/RS)
        # that str.splitlines() would treat as line boundaries, splitting
        # one logical row into a truncated row plus a garbage fragment row.
        # Normalize CRLF -> LF and split on LF ONLY so embedded control
        # chars stay inside their field.
        tsv_string = tsv_string.replace("\r\n", "\n")

        lines = [line for line in tsv_string.split("\n") if line.strip()]
        if not lines:
            return []

        # Determine fieldnames
        if self.has_header:
            fieldnames = lines[0].split("\t")
            data_lines = lines[1:]
        else:
            # Auto-generate f0, f1, ... based on column count
            first_fields = lines[0].split("\t")
            fieldnames = [f"f{i}" for i in range(len(first_fields))]
            data_lines = lines

        # The real SOS export contains ragged rows: some data lines carry
        # MORE fields than the header (unescaped tabs inside free-text
        # values) and some carry fewer. csv.DictReader stashes extras in a
        # list under the None key, which breaks downstream string handling
        # — so map fields manually: merge excess fields into the last
        # column (preserving the data) and pad short rows with None.
        n_cols = len(fieldnames)
        rows: list[dict[str, str | None]] = []
        for ln in data_lines:
            fields = ln.split("\t")
            if len(fields) > n_cols:
                fields = fields[: n_cols - 1] + ["\t".join(fields[n_cols - 1 :])]
            elif len(fields) < n_cols:
                fields = fields + [None] * (n_cols - len(fields))
            rows.append(dict(zip(fieldnames, fields)))
        return self._coerce_rows(rows)

    def read_file(self, path: str) -> list[dict[str, Any]]:
        """Read and parse a TSV file from disk.

        Args:
            path: Filesystem path to the TSV file.

        Returns:
            List of dicts, each representing a row.
        """
        with open(path, encoding="utf-8", errors="replace") as fh:
            return self.read_string(fh.read())

    def read_bytes(self, raw: bytes) -> list[dict[str, Any]]:
        """Parse TSV bytes into a list of dicts.

        Args:
            raw: Tab-delimited bytes, typically from an HTTP download.

        Returns:
            List of dicts, each representing a row.
        """
        return self.read_string(raw.decode("utf-8", errors="replace"))

    # -- internal helpers -------------------------------------------------- #

    def _coerce_rows(self, rows: list[dict[str, str]]) -> list[dict[str, Any]]:
        """Apply empty-to-None and type coercion to raw row dicts."""
        coerced: list[dict[str, Any]] = []
        for row in rows:
            new_row: dict[str, Any] = {}
            for key, value in row.items():
                if value is None or value.strip() == "":
                    new_row[key] = None if self.empty_to_none else value
                else:
                    new_row[key] = value
            coerced.append(self._coerce_types(new_row))
        return coerced

    def _coerce_types(self, row: dict[str, Any]) -> dict[str, Any]:
        """Apply type coercion hints to a single row."""
        for col, target_type in self.coercion_hints.items():
            val = row.get(col)
            if val is None:
                continue
            try:
                if target_type is bool:
                    row[col] = val.strip().lower() in ("true", "1", "yes")
                elif target_type is float:
                    row[col] = float(val)
                elif target_type is int:
                    row[col] = int(float(val))  # handles "1.0" -> 1
                else:
                    row[col] = target_type(val)
            except (ValueError, TypeError, OverflowError):
                # Leave value as-is on coercion failure
                pass
        return row
