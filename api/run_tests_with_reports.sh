#!/usr/bin/env bash
# Runs the API test suite and writes HTML coverage + test-result reports to reports/.
# Usage: ./run_tests_with_reports.sh [app ...]   (defaults to: transactions)
set -uo pipefail
cd "$(dirname "$0")"

APPS=("${@:-transactions}")
REPORT_DIR="${TEST_REPORT_DIR:-reports}"
mkdir -p "$REPORT_DIR"

export TEST_REPORT_DIR="$REPORT_DIR"
python -m coverage run --source="$(IFS=,; echo "${APPS[*]}"),virtual_bank.logging" \
    manage.py test "${APPS[@]}" 2> "$REPORT_DIR/test-logs.jsonl"
TEST_EXIT=$?

python -m coverage report -m --omit='*/migrations/*,*/tests/*' | tee "$REPORT_DIR/coverage.txt"
python -m coverage html -d "$REPORT_DIR/coverage_html" --omit='*/migrations/*,*/tests/*'
python -m junit2htmlreport "$REPORT_DIR/junit.xml" "$REPORT_DIR/test_results.html"

echo
echo "PII scan of captured logs (SSN / 10-digit account / 16-digit card / email):"
PII_HITS=$(grep -cE '\b[0-9]{3}-[0-9]{2}-[0-9]{4}\b|\b[0-9]{10}\b|\b[0-9]{16}\b|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}' "$REPORT_DIR/test-logs.jsonl" || true)
echo "  hits: ${PII_HITS:-0}"

echo "Reports: $REPORT_DIR/coverage_html/index.html, $REPORT_DIR/test_results.html"
exit $TEST_EXIT
