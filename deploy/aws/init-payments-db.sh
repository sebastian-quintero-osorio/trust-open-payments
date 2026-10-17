#!/bin/sh
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    --set=op_password="$OP_DB_PASSWORD" <<'SQL'
CREATE USER op_service WITH PASSWORD :'op_password';
CREATE DATABASE op_service OWNER op_service;
SQL
