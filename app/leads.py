"""Leads -> CSV (opens in Excel / Google Sheets). Header is written once; Excel-friendly UTF-8 BOM."""
from __future__ import annotations

import csv
import os
import threading
from pathlib import Path

FIELDS = ["id", "at", "name", "item", "contact", "username", "igsid", "lang", "source"]
_lock = threading.Lock()


def _safe(v) -> str:
    s = "" if v is None else str(v)
    # CSV/formula injection: a cell starting with = + - @ would run as a formula in Excel
    return "'" + s if s[:1] in ("=", "+", "-", "@") and not s[1:2].isdigit() else s


class CsvLeads:
    def __init__(self, path: str):
        self.path = Path(path)

    def __call__(self, lead: dict) -> None:
        with _lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            new = not self.path.exists() or os.path.getsize(self.path) == 0
            with self.path.open("a", newline="", encoding="utf-8-sig" if new else "utf-8") as f:
                w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
                if new:
                    w.writeheader()
                w.writerow({k: _safe(lead.get(k)) for k in FIELDS})
