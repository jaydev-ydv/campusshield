#!/usr/bin/env bash
# CampusShield — full migration test from an empty database.
#
#   ./scripts/test_migration.sh
#
# Drops and recreates TEST_DATABASE_URL's database, then checks:
#   * the migration applies to a genuinely empty database
#   * object counts match what the schema is supposed to create
#   * downgrade removes everything, leaving no residue
#   * upgrade -> downgrade -> upgrade is repeatable
#   * every structural privacy guarantee still holds (verify_schema_guarantees.sql)
#
# This drops a database. It refuses to run against anything not clearly a test
# database, but read the URL before you run it anyway.

set -euo pipefail

cd "$(dirname "$0")/.."

ALEMBIC=".venv/bin/alembic"
[ -x "$ALEMBIC" ] || ALEMBIC="alembic"

if [ -f .env ]; then
    # shellcheck disable=SC1091
    set -a; . ./.env; set +a
fi

TEST_URL="${TEST_DATABASE_URL:-postgresql+psycopg://localhost:5432/campusshield_migration_test}"
DB_NAME="${TEST_URL##*/}"
DB_NAME="${DB_NAME%%\?*}"

case "$DB_NAME" in
    *test*) ;;
    *) echo "REFUSING: '$DB_NAME' does not look like a test database." >&2; exit 1 ;;
esac

red()   { printf '\033[31m%s\033[0m\n' "$1"; }
green() { printf '\033[32m%s\033[0m\n' "$1"; }
step()  { printf '\n\033[1m== %s\033[0m\n' "$1"; }

fail() { red "FAILED: $1"; exit 1; }

expect() { # expect <label> <actual> <expected>
    if [ "$2" = "$3" ]; then
        green "  ok   $1: $2"
    else
        red   "  FAIL $1: got $2, expected $3"
        FAILURES=$((FAILURES + 1))
    fi
}

FAILURES=0
SCHEMA_LIST="'identity','core','evidence','ml','analytics','intervention','notify','audit'"

q() { psql -d "$DB_NAME" -tAX -c "$1"; }

step "1. Recreate an empty database"
dropdb --if-exists "$DB_NAME"
createdb "$DB_NAME"
green "  created $DB_NAME"

step "2. Apply migration"
DATABASE_URL="$TEST_URL" "$ALEMBIC" upgrade head || fail "upgrade"
green "  applied"

step "3. Object counts"
expect "schemas"           "$(q "SELECT count(*) FROM information_schema.schemata WHERE schema_name IN ($SCHEMA_LIST)")" 8
expect "tables"            "$(q "SELECT count(*) FROM pg_tables WHERE schemaname IN ($SCHEMA_LIST)")" 34
expect "views"             "$(q "SELECT count(*) FROM pg_views WHERE schemaname IN ($SCHEMA_LIST)")" 1
expect "enum types"        "$(q "SELECT count(*) FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace WHERE t.typtype='e' AND n.nspname='public'")" 24
expect "indexes"           "$(q "SELECT count(*) FROM pg_indexes WHERE schemaname IN ($SCHEMA_LIST)")" 135
expect "triggers"          "$(q "SELECT count(*) FROM pg_trigger tg JOIN pg_class c ON c.oid=tg.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE NOT tg.tgisinternal AND n.nspname IN ($SCHEMA_LIST)")" 19
expect "trigger functions" "$(q "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE p.prorettype='trigger'::regtype::oid AND n.nspname IN ($SCHEMA_LIST)")" 18
expect "policy rows"       "$(q "SELECT count(*) FROM core.system_policy")" 13
expect "extensions"        "$(q "SELECT count(*) FROM pg_extension WHERE extname <> 'plpgsql'")" 0
expect "campus locations"  "$(q "SELECT count(*) FROM core.campus_location")" 0

step "4. Schema guarantees"
if psql -d "$DB_NAME" -f scripts/verify_schema_guarantees.sql 2>&1 | grep -q "FAIL"; then
    psql -d "$DB_NAME" -f scripts/verify_schema_guarantees.sql 2>&1 | grep "FAIL"
    fail "one or more structural guarantees are broken"
fi
green "  all guarantee checks passed"

step "5. Downgrade to base"
DATABASE_URL="$TEST_URL" "$ALEMBIC" downgrade base || fail "downgrade"
expect "schemas left" "$(q "SELECT count(*) FROM information_schema.schemata WHERE schema_name IN ($SCHEMA_LIST)")" 0
expect "enums left"   "$(q "SELECT count(*) FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace WHERE t.typtype='e' AND n.nspname='public'")" 0

step "6. Re-apply (repeatability)"
DATABASE_URL="$TEST_URL" "$ALEMBIC" upgrade head || fail "re-upgrade"
expect "tables after re-upgrade" "$(q "SELECT count(*) FROM pg_tables WHERE schemaname IN ($SCHEMA_LIST)")" 34

step "7. Clean up"
dropdb --if-exists "$DB_NAME"
green "  dropped $DB_NAME"

echo
if [ "$FAILURES" -eq 0 ]; then
    green "ALL CHECKS PASSED"
else
    red "$FAILURES CHECK(S) FAILED"
    exit 1
fi
