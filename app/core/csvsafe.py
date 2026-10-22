"""CSV export hardening.

A spreadsheet treats a cell that starts with = + - @ (or a tab / carriage return) as a formula, so a student
whose name is typed as `=HYPERLINK(...)` would run code on the accountant's computer when an export is opened.
`safe_cell` neutralises such cells with a leading apostrophe; real numbers (+923001234567, -5) are left alone.
"""
from __future__ import annotations

import csv
import re
from typing import Any, Iterable

_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")
_NUMBER = re.compile(r"^[+-]?\d[\d.,]*$")


def safe_cell(value: Any) -> Any:
    """Return `value` made safe for a spreadsheet; anything that is not text is returned unchanged."""
    if isinstance(value, str) and value and value[0] in _TRIGGERS:
        if value[0] in "+-" and _NUMBER.match(value):
            return value
        return "'" + value
    return value


class _SafeWriter:
    def __init__(self, fileobj, **kwargs: Any) -> None:
        self._w = csv.writer(fileobj, **kwargs)

    def writerow(self, row: Iterable[Any]):
        return self._w.writerow([safe_cell(c) for c in row])

    def writerows(self, rows: Iterable[Iterable[Any]]) -> None:
        for row in rows:
            self.writerow(row)


def safe_writer(fileobj, **kwargs: Any) -> _SafeWriter:
    """`csv.writer` that neutralises formula cells. Same arguments as `csv.writer`."""
    return _SafeWriter(fileobj, **kwargs)
