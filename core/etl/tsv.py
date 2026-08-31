"""TSVReader: tab-delimited file parser with type coercion support.

Streaming design: ``stream_lines``/``stream_bytes``/``stream_file`` parse a
source line-by-line and ``yield`` one coerced row dict at a time, so memory
usage is O(one row) regardless of input size. The classic list-returning
methods (``read_string``, ``read_file``, ``read_bytes``) are thin wrappers
over the same generator for backward compatibility.
"""

import io
from typing import Any, Iterable, Iterator


class TSVReader:
    """Reads tab-delimited TSV into dicts, one row at a time.

    Handles:
    - Header detection from first non-empty line
    - Empty/whitespace-only fields -> None
    - NUL (0x00) stripping (SOS export corruption artifact)
    - Ragged lines: short rows padded with None, excess fields merged
      into the last column (never drops data)
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

    # -- streaming API (memory-safe) ---------------------------------------- #

    def stream_lines(self, line_iterator: Iterable[str]) -> Iterator[dict[str, Any]]:
        """Yield coerced row dicts from an iterable of raw text lines.

        Memory is O(1) per row: each line is parsed, coerced, yielded, and
        discarded before the next is touched. Header/ragged/coercion
        semantics are identical to ``read_string``.

        Args:
            line_iterator: Any iterable yielding raw lines (file handles,
                TextIOWrappers, generators, or plain lists all work).

        Returns:
            Generator of dicts, one per data row (header row excluded when
            ``has_header`` is True).
        """
        iterator = iter(line_iterator)
        fieldnames: list[str] | None = None
        n_cols = 0

        for line in iterator:
            # NUL bytes are pure data corruption (Postgres rejects them);
            # stripping per line is equivalent to stripping the whole blob.
            if "\x00" in line:
                line = line.replace("\x00", "")
            # Terminators are never data: drop the newline, then one
            # carriage return if it terminated a CRLF ending. A bare \r
            # inside a field (no \n after it) is kept — exactly the
            # historical replace("\r\n","\n") + split("\n") semantics.
            if line.endswith("\n"):
                line = line[:-1]
            if line.endswith("\r"):
                line = line[:-1]
            # Blank/whitespace-only physical lines carry no data (the old
            # path dropped them before header detection too).
            if not line.strip():
                continue

            fields = line.split("\t")

            if fieldnames is None:
                if self.has_header:
                    fieldnames = fields
                    n_cols = len(fieldnames)
                    continue
                # No header: generate f0..fN from the first data row's width,
                # then let the same line fall through as data (same as old).
                n_cols = len(fields)
                fieldnames = [f"f{i}" for i in range(n_cols)]
                if n_cols == 0:
                    continue

            # Ragged-row handling identical to the historical behaviour:
            # excess fields merge into the final column, short rows pad.
            if len(fields) > n_cols:
                fields = fields[: n_cols - 1] + ["\t".join(fields[n_cols - 1 :])]
            elif len(fields) < n_cols:
                fields = fields + [None] * (n_cols - len(fields))

            row: dict[str, Any] = dict(zip(fieldnames, fields))
            if self.empty_to_none:
                for key, value in row.items():
                    if value is not None and value.strip() == "":
                        row[key] = None
            yield self._coerce_types(row)

    def stream_bytes(self, raw: bytes) -> Iterator[dict[str, Any]]:
        """Stream-parse TSV bytes into an iterator of row dicts.

        Decoding is incremental (TextIOWrapper over the in-memory buffer),
        so peak memory is the bytes you passed in plus a single row — the
        full decoded string is never materialized.

        Args:
            raw: Tab-delimited bytes, typically from an HTTP download.
        """
        # newline="\n" keeps LF as the ONLY line boundary and translates
        # nothing: embedded bare \r (and VT/FS/GS/RS) inside free-text
        # fields stay inside their field, exactly like the historical
        # replace("\r\n", "\n") + split("\n") pipeline.
        wrapper = io.TextIOWrapper(
            io.BytesIO(raw), encoding="utf-8", errors="replace", newline="\n"
        )
        return self.stream_lines(self._rstrip_cr(wrapper))

    def stream_file(self, path: str) -> Iterator[dict[str, Any]]:
        """Stream-parse a TSV file from disk one row at a time (O(1) memory)."""
        with open(path, encoding="utf-8", errors="replace", newline="") as fh:
            yield from self.stream_lines(self._rstrip_cr(fh))

    # -- legacy list API (backward compatible wrappers) ---------------------- #

    def read_string(self, tsv_string: str) -> list[dict[str, Any]]:
        """Parse a TSV string into a list of dicts (legacy list API)."""
        return list(self.stream_lines(tsv_string.split("\n")))

    def read_file(self, path: str) -> list[dict[str, Any]]:
        """Read and parse a TSV file from disk into a list (legacy list API)."""
        return list(self.stream_file(path))

    def read_bytes(self, raw: bytes) -> list[dict[str, Any]]:
        """Parse TSV bytes into a list of dicts (legacy list API)."""
        return list(self.stream_bytes(raw))

    # -- internal helpers --------------------------------------------------- #

    @staticmethod
    def _rstrip_cr(lines: Iterable[str]) -> Iterator[str]:
        """Drop one trailing carriage return per physical line (CRLF endings)."""
        for line in lines:
            if line.endswith("\r"):
                line = line[:-1]
            yield line

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
