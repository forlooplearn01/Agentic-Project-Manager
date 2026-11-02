#!/bin/sh
# Runs the automated tests against the separate test database (created on first use), never the demo data.
#   scripts/run_tests.sh                 all tests
#   scripts/run_tests.sh tests/db -q     anything pytest accepts
cd "$(dirname "$0")/.."
if ! docker compose exec -T postgres psql -U postgres -At -c "select 1 from pg_database where datname='project_plan_agent_test'" | grep -q 1; then
  sh scripts/setup_test_db.sh || exit 1
fi
if [ "$#" -eq 0 ]; then set -- tests/ -q; fi
exec docker compose exec -T -e DB_NAME=project_plan_agent_test api pytest "$@"
