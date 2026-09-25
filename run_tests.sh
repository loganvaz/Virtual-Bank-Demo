#!/bin/bash
# Runs the API test suite in parallel with coverage.
#   ./run_tests.sh                 # all api tests
#   ./run_tests.sh users           # one app
# Requires: pip install -r requirements-dev.txt
set -euo pipefail
cd "$(dirname "$0")/api"
export COVERAGE_RCFILE="$(pwd)/../.coveragerc"
coverage erase
coverage run manage.py test --parallel "${PARALLEL:-auto}" "$@"
coverage combine -q
coverage report
coverage html -d htmlcov -q
echo "HTML report: api/htmlcov/index.html"
