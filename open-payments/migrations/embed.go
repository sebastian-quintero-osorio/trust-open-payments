// Package migrations provides immutable, ordered PostgreSQL migrations.
package migrations

import "embed"

// Files is embedded so the deployed binary needs no working-directory assets.
//
//go:embed *.sql
var Files embed.FS
