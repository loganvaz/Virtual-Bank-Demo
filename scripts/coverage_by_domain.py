#!/usr/bin/env python3
"""
Roll up coverage.xml into the compliance control domains used for the OCC
evidence pack. Usage: coverage_by_domain.py api/reports/coverage.xml [--md]
"""

import sys
import xml.etree.ElementTree as ET

DOMAINS = {
    "TXN  transaction processing": ["transactions/views.py", "transactions/utils.py", "transactions/models.py"],
    "AUTHN authentication / identity": ["users/views.py", "users/utils.py", "users/models.py"],
    "VALID data validation": ["transactions/serializers.py", "transactions/paginations.py", "users/serializers.py", "users/forms.py"],
    "AUDIT audit logging": ["virtual_bank/logging/request_id.py", "virtual_bank/logging/__init__.py"],
    "PII   PII handling": ["virtual_bank/logging/pii.py"],
}


def main(path, markdown=False):
    root = ET.parse(path).getroot()
    per_file = {}
    for pkg in root.iter("package"):
      for cls in pkg.iter("class"):
        lines = cls.findall("lines/line")
        filename = cls.get("filename")
        if "/" not in filename:
            filename = f"{pkg.get('name').replace('.', '/')}/{filename}"
        per_file[filename] = (
            sum(1 for ln in lines if int(ln.get("hits")) > 0),
            len(lines),
        )

    rows = []
    for domain, files in DOMAINS.items():
        hit = total = 0
        for f in files:
            h, t = per_file.get(f, (0, 0))
            hit += h
            total += t
        rows.append((domain, hit, total, 100.0 * hit / total if total else 0.0))
    hit = sum(r[1] for r in rows)
    total = sum(r[2] for r in rows)
    rows.append(("TOTAL", hit, total, 100.0 * hit / total if total else 0.0))

    if markdown:
        print("| Control domain | Lines covered | Lines | Coverage |")
        print("|---|---:|---:|---:|")
        for d, h, t, pct in rows:
            print(f"| {d} | {h} | {t} | {pct:.1f}% |")
    else:
        for d, h, t, pct in rows:
            print(f"{d:<32} {h:>5}/{t:<5} {pct:6.1f}%")


if __name__ == "__main__":
    main(sys.argv[1], "--md" in sys.argv)
