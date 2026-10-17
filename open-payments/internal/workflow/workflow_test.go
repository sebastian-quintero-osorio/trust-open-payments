package workflow

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/x509"
	"encoding/pem"
	"fmt"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/config"
	op "github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/openpayments"
	"github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/persistence"
	"github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/testutil"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"
)

func vault(t *testing.T) *Vault {
	t.Helper()
	_, key, e := ed25519.GenerateKey(rand.Reader)
	if e != nil {
		t.Fatal(e)
	}
	der, e := x509.MarshalPKCS8PrivateKey(key)
	if e != nil {
		t.Fatal(e)
	}
	v, e := NewVault(config.Secret(pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: der})))
	if e != nil {
		t.Fatal(e)
	}
	return v
}
func TestVaultPreservesSecretsAndRejectsTampering(t *testing.T) {
	v := vault(t)
	s := Session{ClientNonce: "nonce", Grant: op.Grant{AccessToken: &op.Token{Value: "SECRET-TOKEN", Manage: "https://auth.example/SECRET-MANAGE"}}}
	b, e := v.Seal("grant:1", s)
	if e != nil {
		t.Fatal(e)
	}
	if bytes.Contains(b, []byte("SECRET")) {
		t.Fatal("plaintext secret stored")
	}
	var out Session
	if e = v.Open("grant:1", b, &out); e != nil || string(out.Grant.AccessToken.Value) != "SECRET-TOKEN" {
		t.Fatal("credential lost", e)
	}
	if e = v.Open("grant:2", b, &out); e == nil {
		t.Fatal("row substitution accepted")
	}
	b[len(b)-1] ^= 1
	if e = v.Open("grant:1", b, &out); e == nil {
		t.Fatal("tampering accepted")
	}
}
func TestTerminalStatesNeverTransition(t *testing.T) {
	for _, kind := range []string{"payment", "transfer", "sim_payment"} {
		for _, terminal := range []string{"completed", "failed", "expired", "rejected"} {
			for _, next := range []string{"processing", "completed", "awaiting_authorization"} {
				if CanTransition(kind, terminal, next) {
					t.Fatal("terminal state mutable", kind, terminal, next)
				}
			}
		}
	}
	if !CanTransition("payment", "awaiting_authorization", "processing") {
		t.Fatal("valid transition rejected")
	}
}
func store(t *testing.T) *Store {
	t.Helper()
	dsn := testutil.DatabaseURL(t)
	db, e := persistence.Open(context.Background(), dsn)
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(db.Pool.Close)
	if e = db.Migrate(context.Background()); e != nil {
		t.Fatal(e)
	}
	return &Store{DB: db, Vault: vault(t)}
}
func TestDurableQueueAndGrantSerialization(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	id := uuid.NewString()
	tx, e := s.DB.Pool.Begin(ctx)
	if e != nil {
		t.Fatal(e)
	}
	defer func() { _ = tx.Rollback(ctx) }()
	if e = persistence.InsertResource(ctx, tx, persistence.Resource{ID: id, Kind: "payment", Status: "processing", Payload: []byte(`{}`)}); e != nil {
		t.Fatal(e)
	}
	grant := uuid.NewString()
	if e = s.SaveSession(ctx, tx, grant, Session{CreatedAt: time.Now()}); e != nil {
		t.Fatal(e)
	}
	if _, e = s.Enqueue(ctx, tx, id, grant, "start", Session{ClientNonce: "saved-stage"}, time.Now()); e != nil {
		t.Fatal(e)
	}
	if e = tx.Commit(ctx); e != nil {
		t.Fatal(e)
	}
	j, e := s.Claim(ctx, uuid.NewString())
	if e != nil {
		t.Fatal(e)
	}
	var state Session
	if e = s.Vault.Open("job:"+j.ID, j.Data, &state); e != nil || state.ClientNonce != "saved-stage" {
		t.Fatal("durable stage lost", e)
	}
	if e = s.Finish(ctx, Job{ID: j.ID, Owner: uuid.NewString()}); e == nil {
		t.Fatal("stale owner acknowledged job")
	}
	if e = s.Resume(ctx, j, "poll", time.Now()); e != nil {
		t.Fatal(e)
	}
	j, e = s.Claim(ctx, uuid.NewString())
	if e != nil {
		t.Fatal(e)
	}
	if e = s.Finish(ctx, j); e != nil {
		t.Fatal(e)
	}
	if e = s.WithSession(ctx, grant, func(ctx context.Context, state *Session, db DB) error {
		return s.WithSession(ctx, grant, func(context.Context, *Session, DB) error { return nil })
	}); e != ErrBusy {
		t.Fatal("grant not serialized", e)
	}
	tx, e = s.DB.Pool.Begin(ctx)
	if e != nil {
		t.Fatal(e)
	}
	if e = InsertAttempt(ctx, tx, Attempt{ID: uuid.NewString(), ResourceID: id, SessionID: grant, ExpiresAt: time.Now()}); e != nil {
		t.Fatal(e)
	}
	if e = tx.Commit(ctx); e != nil {
		t.Fatal(e)
	}
}
func TestJobRecoveryAfterLeaseExpiry(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	id := uuid.NewString()
	tx, e := s.DB.Pool.Begin(ctx)
	if e != nil {
		t.Fatal(e)
	}
	if e = persistence.InsertResource(ctx, tx, persistence.Resource{ID: id, Kind: "transfer", Status: "processing", Payload: []byte(`{}`)}); e != nil {
		t.Fatal(e)
	}
	if _, e = s.Enqueue(ctx, tx, id, "", "start", "outgoing-request-already-sent", time.Now()); e != nil {
		t.Fatal(e)
	}
	if e = tx.Commit(ctx); e != nil {
		t.Fatal(e)
	}
	j, e := s.Claim(ctx, uuid.NewString())
	if e != nil {
		t.Fatal(e)
	}
	if _, e = s.DB.Pool.Exec(ctx, "UPDATE jobs SET locked_until=now()-interval '1 second' WHERE id=$1", j.ID); e != nil {
		t.Fatal(e)
	}
	next, e := s.Claim(ctx, uuid.NewString())
	if e != nil || next.ID != j.ID || next.Owner == j.Owner {
		t.Fatal("lease recovery failed", next, e)
	}
	if e = s.Finish(ctx, j); e == nil {
		t.Fatal("previous owner finished recovered job")
	}
	if e = s.Finish(ctx, next); e != nil {
		t.Fatal(e)
	}
	if _, e = s.Claim(ctx, uuid.NewString()); e != pgx.ErrNoRows {
		t.Fatal("finished job reclaimed", e)
	}
}

func TestCallbackVerificationAndConsumptionAreAtomic(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	resource := uuid.NewString()
	grant := uuid.NewString()
	attempt := uuid.NewString()
	tx, e := s.DB.Pool.Begin(ctx)
	if e != nil {
		t.Fatal(e)
	}
	if e = persistence.InsertResource(ctx, tx, persistence.Resource{ID: resource, Kind: "payment", Status: "awaiting_authorization", Payload: []byte(`{}`)}); e != nil {
		t.Fatal(e)
	}
	state := Session{ClientNonce: "client", ServerNonce: "server", Wallet: op.Wallet{AuthServer: "https://auth.example/tenant"}}
	if e = s.SaveSession(ctx, tx, grant, state); e != nil {
		t.Fatal(e)
	}
	if _, e = tx.Exec(ctx, "INSERT INTO resource_sessions(resource_id,session_id) VALUES($1,$2)", resource, grant); e != nil {
		t.Fatal(e)
	}
	if e = InsertAttempt(ctx, tx, Attempt{ID: attempt, ResourceID: resource, SessionID: grant, ExpiresAt: time.Now().Add(time.Minute)}); e != nil {
		t.Fatal(e)
	}
	if e = tx.Commit(ctx); e != nil {
		t.Fatal(e)
	}
	tx, e = s.DB.Pool.Begin(ctx)
	if e != nil {
		t.Fatal(e)
	}
	if _, e = s.VerifyAndConsume(ctx, tx, attempt, "ref", "wrong", ""); e != ErrInvalidCallback {
		t.Fatal("bad callback accepted", e)
	}
	if e = tx.Rollback(ctx); e != nil {
		t.Fatal(e)
	}
	tx, e = s.DB.Pool.Begin(ctx)
	if e != nil {
		t.Fatal(e)
	}
	claim, e := s.VerifyAndConsume(ctx, tx, attempt, "", "", "grant_rejected")
	if e != nil || claim.Replay {
		t.Fatal("valid rejection failed", e)
	}
	if e = tx.Commit(ctx); e != nil {
		t.Fatal(e)
	}
	tx, e = s.DB.Pool.Begin(ctx)
	if e != nil {
		t.Fatal(e)
	}
	claim, e = s.VerifyAndConsume(ctx, tx, attempt, "", "", "grant_rejected")
	if e != nil || !claim.Replay {
		t.Fatal("callback replay was consumed twice", e)
	}
	if e = tx.Rollback(ctx); e != nil {
		t.Fatal(e)
	}
}

func TestExpiredTokenRotatesAtomicallyAndInterruptedRotationIsNotRepeated(t *testing.T) {
	s := store(t)
	ctx := context.Background()
	_, key, e := ed25519.GenerateKey(rand.Reader)
	if e != nil {
		t.Fatal(e)
	}
	der, e := x509.MarshalPKCS8PrivateKey(key)
	if e != nil {
		t.Fatal(e)
	}
	cfg := config.Config{ClientWallet: "https://client.example/wallet", KeyID: "key", PrivateKey: config.Secret(pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: der}))}
	s.Vault, e = NewVault(cfg.PrivateKey)
	if e != nil {
		t.Fatal(e)
	}
	var base string
	var calls atomic.Int32
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if r.Header.Get("Authorization") != "GNAP old" {
			t.Error("expired token not used for rotation")
		}
		_, _ = fmt.Fprintf(w, `{"access_token":{"value":"new","manage":%q,"expires_in":600}}`, base+"/new-opaque")
	}))
	defer srv.Close()
	base = srv.URL
	client, e := op.New(cfg, srv.Client())
	if e != nil {
		t.Fatal(e)
	}
	id := uuid.NewString()
	state := Session{Wallet: op.Wallet{AuthServer: base + "/tenant"}, TokenExpiresAt: time.Now().Add(-time.Minute), Grant: op.Grant{AccessToken: &op.Token{Value: "old", Manage: config.Secret(base + "/opaque")}}}
	if e = s.SaveSession(ctx, s.DB.Pool, id, state); e != nil {
		t.Fatal(e)
	}
	for range 2 {
		if e = s.WithSession(ctx, id, func(ctx context.Context, state *Session, db DB) error {
			return s.EnsureToken(ctx, db, id, state, client)
		}); e != nil {
			t.Fatal(e)
		}
	}
	saved, e := s.LoadSession(ctx, s.DB.Pool, id)
	if e != nil || string(saved.Grant.AccessToken.Value) != "new" || string(saved.Grant.AccessToken.Manage) != base+"/new-opaque" || saved.Rotating {
		t.Fatal("new credentials not atomic", e)
	}
	saved.Rotating = true
	if e = s.SaveSession(ctx, s.DB.Pool, id, saved); e != nil {
		t.Fatal(e)
	}
	if e = s.WithSession(ctx, id, func(ctx context.Context, state *Session, db DB) error {
		return s.EnsureToken(ctx, db, id, state, client)
	}); e == nil {
		t.Fatal("interrupted rotation retried")
	}
	if calls.Load() != 1 {
		t.Fatal("duplicate rotation", calls.Load())
	}
}
