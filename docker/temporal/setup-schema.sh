#!/bin/sh
# One-shot schema setup, adapted from temporalio/samples-server compose/scripts/setup-postgres.sh
# (VERIFY V-05). create/setup-schema tolerate "already exists" so re-runs are safe; update-schema
# must succeed and fails loudly if the schema is really broken.
set -eu
: "${POSTGRES_SEEDS:?}" "${POSTGRES_USER:?}" "${SQL_PASSWORD:?}"
T="temporal-sql-tool --plugin postgres12 --ep ${POSTGRES_SEEDS} -u ${POSTGRES_USER} -p ${DB_PORT:-5432}"
S=/etc/temporal/schema/postgresql/v12

for i in $(seq 1 30); do nc -z -w 2 "$POSTGRES_SEEDS" "${DB_PORT:-5432}" && break; sleep 2; done

for db in temporal temporal_visibility; do
  $T --db "$db" create || echo "database $db already exists"
  $T --db "$db" setup-schema -v 0.0 || echo "schema of $db already initialised"
done
$T --db temporal update-schema -d "$S/temporal/versioned"
$T --db temporal_visibility update-schema -d "$S/visibility/versioned"
echo "temporal schema ready"
