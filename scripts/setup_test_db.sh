#!/bin/sh
# (Re)creates the database the automated tests use, with the same tables as the live one but no data.
# Tests never touch the demo data: tests/conftest.py refuses to run unless DB_NAME contains "test".
set -e
cd "$(dirname "$0")/.."
docker compose exec -T postgres psql -U postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS project_plan_agent_test" -c "CREATE DATABASE project_plan_agent_test" >/dev/null
docker compose exec -T postgres sh -c 'pg_dump -U postgres --schema-only project_plan_agent | psql -U postgres -v ON_ERROR_STOP=1 -q -d project_plan_agent_test' >/dev/null
echo "test database ready: project_plan_agent_test"
