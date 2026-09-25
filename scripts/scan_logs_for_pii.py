#!/usr/bin/env python3
"""Scan log files (or text) for PII-looking values. Exit 1 if any are found.

Usage: python scripts/scan_logs_for_pii.py "api/logs/*.log"
"""
import glob
import re
import sys

PII_PATTERNS = {
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
    "card_number": re.compile(r"\b(?:\d[ -]?){15}\d\b"),
    "account_number": re.compile(r"(?<![\d*])\d{9,12}(?!\d)"),
    "phone": re.compile(r"\(?\b\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b"),
}

# Timestamps at the start of a log line are numeric but not PII.
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(,\d+)?")


def scan_text(text):
    findings = []
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = TIMESTAMP.sub("", line)
        for name, pattern in PII_PATTERNS.items():
            for match in pattern.finditer(stripped):
                findings.append((lineno, name, match.group(0)))
    return findings


def main(patterns):
    total = 0
    for pattern in patterns:
        for path in glob.glob(pattern):
            with open(path, encoding="utf-8", errors="replace") as fh:
                findings = scan_text(fh.read())
            for lineno, kind, value in findings:
                print(f"{path}:{lineno}: {kind}: {value}")
            total += len(findings)
    print(f"{total} potential PII value(s) found")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["api/logs/*.log"]))
