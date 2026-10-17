// Package testutil isolates PostgreSQL integration fixtures by schema.
package testutil

import (
	"context"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"net/url"
	"os"
	"testing"
	"time"
)

// DatabaseURL creates an isolated schema and schedules removal after test cleanup.
func DatabaseURL(t *testing.T) string {
	t.Helper()
	dsn := os.Getenv("TEST_DATABASE_URL")
	if dsn == "" {
		if os.Getenv("REQUIRE_INTEGRATION") == "1" {
			t.Fatal("TEST_DATABASE_URL required")
		}
		t.Skip("PostgreSQL required")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	pool, e := pgxpool.New(ctx, dsn)
	if e != nil {
		t.Fatal("test database configuration rejected")
	}
	schema := "test_" + uuid.NewString()
	identifier := pgx.Identifier{schema}.Sanitize()
	if _, e = pool.Exec(ctx, "CREATE SCHEMA "+identifier); e != nil {
		pool.Close()
		t.Fatal("test schema could not be created")
	}
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if _, e := pool.Exec(ctx, "DROP SCHEMA "+identifier+" CASCADE"); e != nil {
			t.Error("test schema cleanup failed")
		}
		pool.Close()
	})
	u, e := url.Parse(dsn)
	if e != nil {
		t.Fatal("test database URL rejected")
	}
	q := u.Query()
	q.Set("search_path", schema)
	u.RawQuery = q.Encode()
	return u.String()
}
