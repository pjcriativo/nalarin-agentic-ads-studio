#!/usr/bin/env bash
# Run the backend test suite against a DISPOSABLE PostgreSQL (tmpfs container).
# Never touches the dev or production database.
#   scripts/test-backend.sh                 # whole suite
#   scripts/test-backend.sh tests/unit -q   # extra pytest args
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

docker compose -f docker-compose.test.yml up -d --wait
trap 'docker compose -f docker-compose.test.yml down -v >/dev/null 2>&1' EXIT

export TEST_DATABASE_URL="postgresql://ads_studio_test:ads_studio_test@127.0.0.1:55432/ads_studio_test"
cd backend
python -m pytest "${@:-tests}" --cov=app --cov-report=term-missing:skip-covered
