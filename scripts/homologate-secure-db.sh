#!/usr/bin/env bash
# Homologate the API under the PRODUCTION database mode: a dedicated, unprivileged
# LOGIN role (RUNTIME_DATABASE_URL), APP_ENV=production, no SET ROLE / RESET ROLE.
# Runs the backend suite against the DISPOSABLE PostgreSQL only (never dev/prod).
#   scripts/homologate-secure-db.sh                 # whole suite
#   scripts/homologate-secure-db.sh tests/unit -q   # extra pytest args
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
docker compose -f docker-compose.test.yml up -d --wait
trap 'docker compose -f docker-compose.test.yml down -v >/dev/null 2>&1' EXIT
export TEST_DATABASE_URL="postgresql://ads_studio_test:ads_studio_test@127.0.0.1:55432/ads_studio_test"
export RUNTIME_DATABASE_URL="postgresql://ads_homolog_login:homolog-only-password-0123@127.0.0.1:55432/ads_studio_test"
export APP_ENV=production
cd backend
python -m pytest "${@:-tests}" -q
