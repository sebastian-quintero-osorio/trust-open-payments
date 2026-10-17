package persistence

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"net/http"
	"os"
	"sync/atomic"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

func testStore(t *testing.T) *Store {
	t.Helper()
	dsn := os.Getenv("TEST_DATABASE_URL")
	if dsn == "" {
		if os.Getenv("REQUIRE_INTEGRATION") == "1" {
			t.Fatal("TEST_DATABASE_URL is required")
		}
		t.Skip("PostgreSQL integration requires TEST_DATABASE_URL")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	s, e := Open(ctx, dsn)
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(s.Pool.Close)
	if e = s.Migrate(ctx); e != nil {
		t.Fatal(e)
	}
	return s
}
func TestMigrationsRepeatSafely(t *testing.T) {
	s := testStore(t)
	if e := s.Migrate(context.Background()); e != nil {
		t.Fatal(e)
	}
}
func TestReplayConflictRollbackExpiryAndRestart(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()
	key := uuid.NewString()
	hash := sha256.Sum256([]byte(`{"a":1}`))
	id := uuid.NewString()
	calls := 0
	fn := func(ctx context.Context, tx pgx.Tx) (Response, error) {
		calls++
		e := InsertResource(ctx, tx, Resource{ID: id, Kind: "transfer", Status: "processing", Payload: json.RawMessage(`{"status":"processing"}`)})
		return Response{Status: 201, Body: []byte(`{"id":"` + id + `"}`), Headers: http.Header{"Content-Type": []string{"application/json"}}}, e
	}
	r, e := s.Execute(ctx, "POST /v1/transfers", key, hash, fn)
	if e != nil || r.Status != 201 || r.Replayed {
		t.Fatal(r, e)
	}
	s2, e := Open(ctx, os.Getenv("TEST_DATABASE_URL"))
	if e != nil {
		t.Fatal(e)
	}
	defer s2.Pool.Close()
	r2, e := s2.Execute(ctx, "POST /v1/transfers", key, hash, fn)
	if e != nil || !r2.Replayed || r2.Status != 201 || string(r.Body) != string(r2.Body) || calls != 1 {
		t.Fatal("replay failed", r2, e)
	}
	_, e = s.Execute(ctx, "POST /v1/transfers", key, sha256.Sum256([]byte(`{"a":2}`)), fn)
	if e != ErrConflict {
		t.Fatal("expected body conflict", e)
	}
	transientKey := uuid.NewString()
	transientID := uuid.NewString()
	var transientCalls int
	transient := func(ctx context.Context, tx pgx.Tx) (Response, error) {
		transientCalls++
		e := InsertResource(ctx, tx, Resource{ID: transientID, Kind: "payment", Status: "processing", Payload: json.RawMessage(`{}`)})
		return Response{Status: 503, Body: []byte(`{}`)}, e
	}
	for range 2 {
		if _, e = s.Execute(ctx, "POST /v1/payments", transientKey, hash, transient); e != nil {
			t.Fatal(e)
		}
	}
	if transientCalls != 2 {
		t.Fatal("transient response cached")
	}
	var payload []byte
	if e = s.Pool.QueryRow(ctx, "SELECT payload FROM resources WHERE kind=$1 AND id=$2", "payment", transientID).Scan(&payload); e != pgx.ErrNoRows {
		t.Fatal("transient mutation committed", e)
	}
	if _, e = s.Pool.Exec(ctx, "UPDATE idempotency_records SET expires_at=now()-interval '1 second' WHERE scope=$1 AND key=$2", "POST /v1/transfers", key); e != nil {
		t.Fatal(e)
	}
	r, e = s.Execute(ctx, "POST /v1/transfers", key, sha256.Sum256([]byte(`{"a":2}`)), func(context.Context, pgx.Tx) (Response, error) {
		return Response{Status: 422, Body: []byte(`{"error":{"code":"invalid_amount"}}`)}, nil
	})
	if e != ErrConflict {
		t.Fatal("financial intent lost after cache expiry", r, e)
	}
	t.Cleanup(func() {
		_, e := s.Pool.Exec(context.Background(), "DELETE FROM resources WHERE id=$1", id)
		if e != nil {
			t.Error(e)
		}
	})
}
func TestConcurrentRequestInProgress(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()
	scope := "POST /v1/transfers"
	key := uuid.NewString()
	hash := sha256.Sum256([]byte(`{}`))
	entered := make(chan struct{})
	release := make(chan struct{})
	done := make(chan error, 1)
	var calls atomic.Int32
	fn := func(context.Context, pgx.Tx) (Response, error) {
		calls.Add(1)
		close(entered)
		<-release
		return Response{Status: 201, Body: []byte(`{}`)}, nil
	}
	go func() { _, e := s.Execute(ctx, scope, key, hash, fn); done <- e }()
	select {
	case <-entered:
	case <-time.After(5 * time.Second):
		t.Fatal("first operation did not start")
	}
	_, e := s.Execute(ctx, scope, key, hash, fn)
	close(release)
	if e != ErrInProgress {
		t.Errorf("expected in progress, got %v", e)
	}
	if e := <-done; e != nil {
		t.Fatal(e)
	}
	if calls.Load() != 1 {
		t.Fatal("duplicate operation")
	}
}
func TestCacheablePolicy(t *testing.T) {
	for _, tc := range []struct {
		status int
		code   string
		want   bool
	}{{201, "", true}, {400, "validation_error", true}, {404, "not_found", true}, {409, "already_assigned", true}, {422, "invalid_amount", true}, {401, "unauthorized", false}, {409, "request_in_progress", false}, {409, "idempotency_conflict", false}, {502, "upstream_error", false}, {503, "upstream_error", false}} {
		b, e := json.Marshal(map[string]any{"error": map[string]string{"code": tc.code}})
		if e != nil {
			t.Fatal(e)
		}
		if Cacheable(Response{Status: tc.status, Body: b}) != tc.want {
			t.Fatal(tc)
		}
	}
}

func TestDefinitiveErrorsNeverCommitResources(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()
	id := uuid.NewString()
	key := uuid.NewString()
	hash := sha256.Sum256([]byte(`{}`))
	fn := func(ctx context.Context, tx pgx.Tx) (Response, error) {
		e := InsertResource(ctx, tx, Resource{ID: id, Kind: "payment", Status: "processing", Payload: json.RawMessage(`{}`)})
		return Response{Status: 400, Body: []byte(`{"error":{"code":"validation_error"}}`)}, e
	}
	if _, e := s.Execute(ctx, "POST /v1/payments", key, hash, fn); e != nil {
		t.Fatal(e)
	}
	r, e := s.Execute(ctx, "POST /v1/payments", key, hash, fn)
	if e != nil || !r.Replayed || r.Status != 400 {
		t.Fatal("definitive response not replayed", r, e)
	}
	var payload []byte
	if e := s.Pool.QueryRow(ctx, "SELECT payload FROM resources WHERE kind=$1 AND id=$2", "payment", id).Scan(&payload); e != pgx.ErrNoRows {
		t.Fatal("error response committed a resource", e)
	}
}
