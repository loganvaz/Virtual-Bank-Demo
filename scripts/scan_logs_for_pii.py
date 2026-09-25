#!/usr/bin/env python3
"""
Scan log files (and optionally source files for log/print statements) for
PII-looking values. Exits non-zero on any hit so it can gate CI.

Usage: scan_logs_for_pii.py <path-or-glob> [...]
"""

import glob
import re
import sys

PATTERNS = {
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "card/account (13-19 digits)": re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)"),
    "account (10-12 digits)": re.compile(r"(?<![\w.])\d{10,12}(?![\w.])"),
    "phone": re.compile(r"(?<!\d)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\d)"),
}


def scan(paths):
    hits = 0
    for pattern in paths:
        for path in glob.glob(pattern, recursive=True):
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    for lineno, line in enumerate(fh, 1):
                        for name, rx in PATTERNS.items():
                            if rx.search(line):
                                hits += 1
                                print(f"{path}:{lineno}: possible {name}: {line.strip()[:120]}")
            except IsADirectoryError:
                continue
    return hits


if __name__ == "__main__":
    args = sys.argv[1:] or ["api/logs/*.log"]
    n = scan(args)
    print(f"{n} potential PII hit(s)")
    sys.exit(1 if n else 0)
