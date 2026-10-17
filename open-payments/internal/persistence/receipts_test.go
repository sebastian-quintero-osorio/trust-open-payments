package persistence

import (
	"context"
	"crypto/sha256"
	"net/http"
	"strings"
	"testing"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/testutil"
	"github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/migrations"
)

func TestRejectPoolThatCanStarveFinancialWorkers(t *testing.T) {
	_, err := Open(context.Background(), "postgres://user@localhost:1/test?pool_max_conns=1")
	if err == nil || !strings.Contains(err.Error(), "pool_max_conns must be at least 12") {
		t.Fatal("unsafe pool accepted", err)
	}
}

func TestPoolReservesCapacityForWorkersAndMaintenance(t *testing.T) {
	s := testStore(t)
	if s.Pool.Config().MaxConns < MinimumPoolConnections {
		t.Fatal("insufficient worker connection capacity")
	}
}

func TestUpgradePreservesPreviouslyCachedFinancialResponses(t *testing.T) {
	ctx := context.Background()
	s, err := Open(ctx, testutil.DatabaseURL(t))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(s.Pool.Close)
	// Reconstruct the schema as deployed before the durable-receipt migration.
	if _, err = s.Pool.Exec(ctx, "CREATE TABLE schema_migrations(name text PRIMARY KEY, checksum bytea NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"001_base.sql", "002_workflows.sql", "003_trust.sql", "004_reauthorized_collections.sql"} {
		body, err := migrations.Files.ReadFile(name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err = s.Pool.Exec(ctx, string(body)); err != nil {
			t.Fatal(err)
		}
		hash := sha256.Sum256(body)
		if _, err = s.Pool.Exec(ctx, "INSERT INTO schema_migrations(name,checksum) VALUES($1,$2)", name, hash[:]); err != nil {
			t.Fatal(err)
		}
	}
	key, hash := uuid.NewString(), sha256.Sum256([]byte(`{}`))
	if _, err = s.Pool.Exec(ctx, `INSERT INTO idempotency_records(scope,key,request_hash,status,response_body,response_headers,expires_at)
 VALUES('POST /v1/payments',$1,$2,201,$3,$4,now()-interval '1 hour')`, key, hash[:], []byte(`{"id":"original-payment"}`), []byte(`{"Content-Type":["application/json"]}`)); err != nil {
		t.Fatal(err)
	}
	if err = s.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	if err = s.Cleanup(ctx); err != nil {
		t.Fatal(err)
	}
	response, err := s.Execute(ctx, "POST /v1/payments", key, hash, func(context.Context, pgx.Tx) (Response, error) {
		t.Fatal("upgrade allowed a second financial operation")
		return Response{}, nil
	})
	if err != nil || !response.Replayed || response.Status != 201 || string(response.Body) != `{"id":"original-payment"}` || response.Headers.Get("Content-Type") != "application/json" {
		t.Fatal("cached receipt not preserved", response, err)
	}
}

func TestDefinitiveErrorsStillExpireWithoutCreatingDurableReceipts(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()
	key, hash := uuid.NewString(), sha256.Sum256([]byte(`{}`))
	for _, status := range []int{422, 201} {
		r, err := s.Execute(ctx, "POST /v1/payments", key, hash, func(context.Context, pgx.Tx) (Response, error) {
			return Response{Status: status, Body: []byte(`{}`), Headers: http.Header{}}, nil
		})
		if err != nil || r.Replayed || r.Status != status {
			t.Fatal("definitive error prevented a later new intent", r, err)
		}
		if _, err = s.Pool.Exec(ctx, "UPDATE idempotency_records SET expires_at=now()-interval '1 second' WHERE key=$1", key); err != nil {
			t.Fatal(err)
		}
	}
}
