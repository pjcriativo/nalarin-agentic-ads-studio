#!/usr/bin/env bash
# Etapa 02.7 homologation helper. Default actions are OFFLINE; real calls need the explicit `phase-b` command.
#   scripts/homologation.sh plan        # list allowlisted operations (offline)
#   scripts/homologation.sh preflight   # which credentials are present (offline, values never printed)
#   scripts/homologation.sh up|down     # isolated stack: disposable PostgreSQL + API + worker + frontend
#   scripts/homologation.sh phase-b     # REAL calls: needs HOMOLOG_AUTHORIZATION=I_AUTHORIZE_REAL_CALLS_PHASE_B
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
COMPOSE=(docker compose -f docker-compose.homologation.yml)
case "${1:-plan}" in
  plan)      (cd backend && python -m app.homologation plan) ;;
  preflight) (cd backend && python -m app.homologation preflight) ;;
  up)        "${COMPOSE[@]}" up -d --build --wait postgres backend && "${COMPOSE[@]}" up -d agent-worker frontend ;;
  down)      "${COMPOSE[@]}" down -v ;;
  phase-b)
    : "${HOMOLOG_AUTHORIZATION:?Phase B is not authorized: HOMOLOG_AUTHORIZATION is not set}"
    mkdir -p docs/evidence/etapa-02-07
    (cd backend && python -m app.homologation run --execute --out "../docs/evidence/etapa-02-07/provider-report.json") || true
    # agents end-to-end on a DISPOSABLE database (name must end in _test, enforced by tests/conftest.py)
    "${COMPOSE[@]}" up -d --wait postgres
    trap '"${COMPOSE[@]}" down -v >/dev/null 2>&1' EXIT
    export TEST_DATABASE_URL="postgresql://ads_homolog:ads_homolog_pw@127.0.0.1:55433/ads_homolog_test"
    (cd backend && python -m pytest tests_real -p tests.conftest -m real -q --no-cov -rs) ;;
  *) echo "unknown command" >&2; exit 64 ;;
esac
