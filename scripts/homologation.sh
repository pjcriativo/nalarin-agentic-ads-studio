#!/usr/bin/env bash
# Etapa 02.7 homologation helper. Default actions are OFFLINE; real calls need the explicit `phase-b` command.
#   scripts/homologation.sh plan        # list allowlisted operations (offline)
#   scripts/homologation.sh preflight   # which credentials are present (offline, values never printed)
#   scripts/homologation.sh up|down     # isolated stack: disposable PostgreSQL + API + worker + frontend
#   scripts/homologation.sh scan        # fail if any configured secret appears in the evidence or in git-tracked files
#   scripts/homologation.sh phase-b     # REAL calls: needs HOMOLOG_AUTHORIZATION=I_AUTHORIZE_REAL_CALLS_PHASE_B
# ONE aggregate ceiling (US$ 1.00, hard clamp) covers the provider harness AND the agent tests: they share the ledger file below.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
COMPOSE=(docker compose -f docker-compose.homologation.yml)
export HOMOLOG_LEDGER_FILE="${HOMOLOG_LEDGER_FILE:-$ROOT/backend/.homolog-ledger.json}"
EVIDENCE="docs/evidence/etapa-02-07"
case "${1:-plan}" in
  plan)      (cd backend && python -m app.homologation plan) ;;
  preflight) (cd backend && python -m app.homologation preflight) ;;
  up)        "${COMPOSE[@]}" up -d --build --wait postgres backend && "${COMPOSE[@]}" up -d agent-worker frontend ;;
  down)      "${COMPOSE[@]}" down -v ;;
  scan)      # evidence + docs: values AND token-shaped strings; code: configured secret VALUES only (test fixtures use fake keys)
             (cd backend && python -m app.homologation scan "$ROOT/$EVIDENCE" "$ROOT/docs" && python -m app.homologation --values-only scan "$ROOT/backend/app" "$ROOT/backend/tests" "$ROOT/backend/tests_real" "$ROOT/scripts") ;;
  phase-b)
    : "${HOMOLOG_AUTHORIZATION:?Phase B is not authorized: HOMOLOG_AUTHORIZATION is not set}"
    mkdir -p "$EVIDENCE"
    (cd backend && python -m app.homologation run --execute --out "../$EVIDENCE/provider-report.json") || true
    # agents end-to-end on a DISPOSABLE database (name must end in _test, enforced by tests/conftest.py)
    "${COMPOSE[@]}" up -d --wait postgres
    trap '"${COMPOSE[@]}" down -v >/dev/null 2>&1' EXIT
    export TEST_DATABASE_URL="postgresql://ads_homolog:ads_homolog_pw@127.0.0.1:55433/ads_homolog_test"
    # pytest output goes through the redactor before it touches the disk or the terminal
    (cd backend && python -m pytest tests_real -p tests.conftest -m real -q --no-cov -rs --tb=line 2>&1 \
        | python -m app.homologation redact | tee "../$EVIDENCE/agents-report.txt") || true
    (cd backend && python -m app.homologation scan "$ROOT/$EVIDENCE") ;;
  *) echo "unknown command" >&2; exit 64 ;;
esac
