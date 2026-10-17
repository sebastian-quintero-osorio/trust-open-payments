package httpapi

import (
	"context"
	"encoding/json"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/config"
	"github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/persistence"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync/atomic"
	"testing"
)

func TestHTTPIdempotencyPostgres(t *testing.T) {
	dsn := os.Getenv("TEST_DATABASE_URL")
	if dsn == "" {
		if os.Getenv("REQUIRE_INTEGRATION") == "1" {
			t.Fatal("TEST_DATABASE_URL required")
		}
		t.Skip("PostgreSQL integration requires TEST_DATABASE_URL")
	}
	ctx := context.Background()
	store, e := persistence.Open(ctx, dsn)
	if e != nil {
		t.Fatal(e)
	}
	defer store.Pool.Close()
	if e = store.Migrate(ctx); e != nil {
		t.Fatal(e)
	}
	s, e := New(config.Config{InternalAPIKey: "private-test-key"}, store)
	if e != nil {
		t.Fatal(e)
	}
	var calls atomic.Int32
	s.Operations["createCollectionAccount"] = func(ctx context.Context, tx pgx.Tx, r Request) (persistence.Response, error) {
		calls.Add(1)
		var in struct {
			ExternalRef string `json:"external_ref"`
		}
		if e := json.Unmarshal(r.Body, &in); e != nil {
			return persistence.Response{}, e
		}
		b, e := json.Marshal(map[string]string{"external_ref": in.ExternalRef})
		return persistence.Response{Status: 201, Body: b, Headers: http.Header{"Content-Type": []string{"application/json"}}}, e
	}
	handler := s.Handler()
	key := uuid.NewString()
	send := func(body, key string) *httptest.ResponseRecorder {
		r := httptest.NewRequest("POST", "/v1/collection-accounts", strings.NewReader(body))
		r.Header.Set("X-Internal-Api-Key", "private-test-key")
		r.Header.Set("Content-Type", "application/json")
		if key != "" {
			r.Header.Set("Idempotency-Key", key)
		}
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, r)
		return w
	}
	a := send(`{"external_ref":"merchant:1","display_name":"Shop","asset_code":"USD"}`, key)
	b := send(`{ "asset_code":"USD", "display_name":"Shop", "external_ref":"merchant:1" }`, key)
	if a.Code != 201 || b.Code != 201 || a.Body.String() != b.Body.String() || b.Header().Get("Idempotent-Replayed") != "true" || calls.Load() != 1 {
		t.Fatal("HTTP replay failed", a.Code, b.Code, a.Body.String(), b.Body.String())
	}
	if send(`{"external_ref":"merchant:2","display_name":"Shop","asset_code":"USD"}`, key).Code != 409 {
		t.Fatal("body conflict accepted")
	}
	if send(`{}`, "").Code != 400 {
		t.Fatal("missing key accepted")
	}
	badKey := uuid.NewString()
	if send(`{}`, badKey).Code != 400 || send(`{}`, badKey).Header().Get("Idempotent-Replayed") != "true" {
		t.Fatal("definitive validation error not cached")
	}
	for _, raw := range []string{`{"display_name":"a","display_name":"b"}`, strings.Repeat(" ", 65537)} {
		if send(raw, uuid.NewString()).Code != 400 {
			t.Fatal("invalid body accepted")
		}
	}
	malformedKey := uuid.NewString()
	if send(`{"a":1,"a":2}`, malformedKey).Code != 400 || send(`{"a":1,"a":2}`, malformedKey).Header().Get("Idempotent-Replayed") != "true" {
		t.Fatal("malformed JSON response not replayed")
	}
	if calls.Load() != 1 {
		t.Fatal("invalid input reached business operation")
	}
}
