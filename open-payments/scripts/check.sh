#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${TEST_DATABASE_URL:?Set TEST_DATABASE_URL to an isolated PostgreSQL test database}"
export REQUIRE_INTEGRATION=1
export TRUST_RECEIVER_PYTHON="${TRUST_RECEIVER_PYTHON:-$(command -v python3)}"
formatting=$(gofmt -l .)
if [[ -n "$formatting" ]]; then
  printf 'Unformatted Go files:\n%s\n' "$formatting" >&2
  exit 1
fi
# Regeneration must reproduce committed wire models byte for byte.
model_before=$(sha256sum internal/domain/models.gen.go)
go generate ./...
model_after=$(sha256sum internal/domain/models.gen.go)
[[ "$model_before" == "$model_after" ]]
go mod verify
go vet ./...
staticcheck ./...
go test -race -count=1 ./...
gosec ./...
govulncheck ./...
python3 scripts/validate-openapi.py
python3 -m unittest discover -s tests -p 'test_*.py'
python3 scripts/scan-secrets.py
