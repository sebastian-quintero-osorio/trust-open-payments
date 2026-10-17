#!/usr/bin/env bash
set -euo pipefail
# Versions are fixed so local checks and CI execute the same analyzers.
go install honnef.co/go/tools/cmd/staticcheck@v0.8.2
go install github.com/securego/gosec/v2/cmd/gosec@v2.29.0
go install golang.org/x/vuln/cmd/govulncheck@v1.9.0
go install github.com/zricethezav/gitleaks/v8@v8.30.1
