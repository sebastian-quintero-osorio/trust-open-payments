// Package persistence implements transactional PostgreSQL repositories.
package persistence

import (
	"context"
	"crypto/sha256"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/migrations"
)

// Store owns a concurrency-safe PostgreSQL connection pool.
type Store struct{ Pool *pgxpool.Pool }

// Four workers each retain a grant-lock connection and may open a transaction.
// Reserve four additional connections for job acknowledgements and maintenance.
const MinimumPoolConnections int32 = 12

// Open validates connectivity before returning a usable store.
func Open(ctx context.Context, dsn string) (*Store, error) {
	cfg, e := pgxpool.ParseConfig(dsn)
	if e != nil {
		return nil, errors.New("database configuration rejected")
	}
	if cfg.MaxConns < MinimumPoolConnections {
		u, parseErr := url.Parse(dsn)
		if parseErr != nil || strings.Contains(dsn, "pool_max_conns=") || (u != nil && u.Query().Has("pool_max_conns")) {
			return nil, errors.New("pool_max_conns must be at least 12 for financial workers")
		}
		cfg.MaxConns = MinimumPoolConnections
	}
	p, e := pgxpool.NewWithConfig(ctx, cfg)
	if e != nil {
		return nil, errors.New("database configuration rejected")
	}
	if e = p.Ping(ctx); e != nil {
		p.Close()
		return nil, errors.New("database unavailable")
	}
	return &Store{Pool: p}, nil
}

// Migrate atomically applies migrations under a database-wide advisory lock.
func (s *Store) Migrate(ctx context.Context) (err error) {
	tx, e := s.Pool.Begin(ctx)
	if e != nil {
		return e
	}
	defer func() { _ = tx.Rollback(context.WithoutCancel(ctx)) }()
	if _, e = tx.Exec(ctx, "SELECT pg_advisory_xact_lock(714583921)"); e != nil {
		return e
	}
	if _, e = tx.Exec(ctx, `CREATE TABLE IF NOT EXISTS schema_migrations(name text PRIMARY KEY, checksum bytea NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())`); e != nil {
		return e
	}
	entries, e := migrations.Files.ReadDir(".")
	if e != nil {
		return e
	}
	for _, entry := range entries {
		if entry.IsDir() {
			continue
		}
		body, e := migrations.Files.ReadFile(entry.Name())
		if e != nil {
			return e
		}
		hash := sha256.Sum256(body)
		var saved []byte
		e = tx.QueryRow(ctx, "SELECT checksum FROM schema_migrations WHERE name=$1", entry.Name()).Scan(&saved)
		if e == nil {
			if string(saved) != string(hash[:]) {
				return errors.New("applied migration checksum mismatch")
			}
			continue
		}
		if !errors.Is(e, pgx.ErrNoRows) {
			return e
		}
		if _, e = tx.Exec(ctx, string(body)); e != nil {
			return e
		}
		if _, e = tx.Exec(ctx, "INSERT INTO schema_migrations(name,checksum) VALUES($1,$2)", entry.Name(), hash[:]); e != nil {
			return e
		}
	}
	return tx.Commit(ctx)
}

// Response is persisted with its original status and safe response headers.
type Response struct {
	Status   int
	Body     []byte
	Headers  http.Header
	Replayed bool
}

// ErrConflict means the same idempotency key was reused for a different body.
var ErrConflict = errors.New("idempotency conflict")

// ErrInProgress indicates another transaction owns this request.
var ErrInProgress = errors.New("request in progress")

// Operation performs local mutations using the same transaction as the response.
// External financial side effects must be queued durably, never executed here.
type Operation func(context.Context, pgx.Tx) (Response, error)

// Execute serializes a scope/key and commits resource mutations with the reply.
// A crash rolls back both, so replay cannot produce duplicate local operations.
func (s *Store) Execute(ctx context.Context, scope, key string, hash [32]byte, fn Operation) (Response, error) {
	tx, e := s.Pool.Begin(ctx)
	if e != nil {
		return Response{}, e
	}
	defer func() { _ = tx.Rollback(context.WithoutCancel(ctx)) }()
	lockHash := sha256.Sum256([]byte(scope + "\x00" + key))
	var locked bool
	// Mask the sign bit to keep the lock identifier inside the int64 range.
	lockID := int64(binary.BigEndian.Uint64(lockHash[:8]) & 0x7fffffffffffffff)
	if e = tx.QueryRow(ctx, "SELECT pg_try_advisory_xact_lock($1)", lockID).Scan(&locked); e != nil {
		return Response{}, e
	}
	if !locked {
		return Response{}, ErrInProgress
	}
	if _, e = tx.Exec(ctx, "DELETE FROM idempotency_records WHERE scope=$1 AND key=$2 AND expires_at<=now()", scope, key); e != nil {
		return Response{}, e
	}
	var r Response
	var savedHash, headers []byte
	e = tx.QueryRow(ctx, `SELECT request_hash,status,response_body,response_headers FROM financial_receipts WHERE scope=$1 AND key=$2
 UNION ALL SELECT request_hash,status,response_body,response_headers FROM idempotency_records WHERE scope=$1 AND key=$2
 LIMIT 1`, scope, key).Scan(&savedHash, &r.Status, &r.Body, &headers)
	if e == nil {
		if string(savedHash) != string(hash[:]) {
			return Response{}, ErrConflict
		}
		if e = json.Unmarshal(headers, &r.Headers); e != nil {
			return Response{}, e
		}
		r.Replayed = true
		return r, nil
	}
	if !errors.Is(e, pgx.ErrNoRows) {
		return Response{}, e
	}
	workTx, e := tx.Begin(ctx)
	if e != nil {
		return Response{}, e
	}
	r, e = fn(ctx, workTx)
	if e != nil {
		return Response{}, e
	}
	if r.Status >= 200 && r.Status < 300 {
		if e = workTx.Commit(ctx); e != nil {
			return Response{}, e
		}
	} else {
		// Definitive error replies are cached, but must never commit resource creation.
		if e = workTx.Rollback(ctx); e != nil {
			return Response{}, e
		}
	}
	if !Cacheable(r) {
		return r, nil
	} // rollback prevents mutations accompanying a transient error.
	safeHeaders := http.Header{}
	for _, h := range []string{"Content-Type", "Location"} {
		if v := r.Headers.Get(h); v != "" {
			safeHeaders.Set(h, v)
		}
	}
	headers, e = json.Marshal(safeHeaders)
	if e != nil {
		return Response{}, e
	}
	if _, e = tx.Exec(ctx, "INSERT INTO idempotency_records(scope,key,request_hash,status,response_body,response_headers) VALUES($1,$2,$3,$4,$5,$6)", scope, key, hash[:], r.Status, r.Body, headers); e != nil {
		return Response{}, e
	}
	if r.Status >= 200 && r.Status < 300 && financialScope(scope) {
		if _, e = tx.Exec(ctx, "INSERT INTO financial_receipts(scope,key,request_hash,status,response_body,response_headers) VALUES($1,$2,$3,$4,$5,$6)", scope, key, hash[:], r.Status, r.Body, headers); e != nil {
			return Response{}, e
		}
	}
	if e = tx.Commit(ctx); e != nil {
		return Response{}, fmt.Errorf("commit request: %w", e)
	}
	return r, nil
}

func financialScope(scope string) bool {
	return scope == "POST /v1/payments" || scope == "POST /v1/transfers" ||
		scope == "POST /v1/sim/puntored/qr-payments" ||
		strings.HasPrefix(scope, "POST /qr/") && strings.HasSuffix(scope, "/pay")
}

// Cacheable follows the contract's definitive response policy.
func Cacheable(r Response) bool {
	if r.Status >= 200 && r.Status < 300 {
		return true
	}
	switch r.Status {
	case 400, 404, 422:
		return true
	case 409:
		var body struct {
			Error struct {
				Code string `json:"code"`
			} `json:"error"`
		}
		if json.Unmarshal(r.Body, &body) != nil {
			return false
		}
		return body.Error.Code != "request_in_progress" && body.Error.Code != "idempotency_conflict" && body.Error.Code != ""
	}
	return false
}

// Cleanup removes expired replay data without affecting active operations.
func (s *Store) Cleanup(ctx context.Context) error {
	_, e := s.Pool.Exec(ctx, "DELETE FROM idempotency_records WHERE expires_at <= now()")
	return e
}

// Resource is the public representation plus lookup fields, never grant secrets.
type Resource struct {
	ID          string
	Kind        string
	ExternalRef string
	Status      string
	Payload     json.RawMessage
	CreatedAt   time.Time
}

// InsertResource inserts within the caller's idempotency transaction.
func InsertResource(ctx context.Context, tx pgx.Tx, r Resource) error {
	_, e := tx.Exec(ctx, "INSERT INTO resources(id,kind,external_ref,status,payload) VALUES($1,$2,$3,$4,$5)", r.ID, r.Kind, r.ExternalRef, r.Status, []byte(r.Payload))
	return e
}
