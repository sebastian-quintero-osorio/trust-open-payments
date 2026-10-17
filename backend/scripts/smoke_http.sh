#!/usr/bin/env bash
# Smoke E2E en modo http: arranca el mock de op-service (tools/mock_op_service) en un puerto libre, con webhook firmado
# hacia la API, y corre scripts/smoke_e2e.py con OP_CLIENT=http contra él. Todo lo arranca y lo apaga el script de
# Python (--with-mock); los argumentos extra pasan tal cual (--keep-db, --timeout 90, --db-name ...).
#
#   cd backend && docker compose up -d db && ./scripts/smoke_http.sh
#
# Sale con el código del smoke: 0 todo bien · 1 falló un paso · 2 no se pudo preparar el entorno.
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run python scripts/smoke_e2e.py --with-mock "$@"
