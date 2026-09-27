#!/bin/bash
# Runs once, on first initialisation of the pgdata volume (official postgres image behaviour).
# One role per service (docs/04 §8). Temporal's DBs are created by temporal-sql-tool (needs CREATEDB).
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v temporal_pw="$B2C_PG_TEMPORAL_PASSWORD" -v langfuse_pw="$B2C_PG_LANGFUSE_PASSWORD" <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector;
CREATE ROLE temporal LOGIN CREATEDB PASSWORD :'temporal_pw';
CREATE ROLE langfuse LOGIN PASSWORD :'langfuse_pw';
CREATE DATABASE langfuse OWNER langfuse;
SQL
