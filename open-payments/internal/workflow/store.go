package workflow

import (
	"context"
	"crypto/sha256"
	"encoding/binary"
	"errors"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	op "github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/openpayments"
	"github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/persistence"
)

// DB supports transactional and connection-bound state updates.
type DB interface {
	Exec(context.Context, string, ...any) (pgconn.CommandTag, error)
	QueryRow(context.Context, string, ...any) pgx.Row
}

// Session contains only encrypted, internal grant state.
type Session struct {
	Wallet         op.Wallet
	Grant          op.Grant
	TokenExpiresAt time.Time
	CreatedAt      time.Time
	ClientNonce    string
	ServerNonce    string
	Rotating       bool
	Blocked        bool
}

// Store binds credential encryption to a PostgreSQL repository.
type Store struct {
	DB    *persistence.Store
	Vault *Vault
}

// SaveSession atomically replaces token and management URL in one ciphertext.
func (s *Store) SaveSession(ctx context.Context, db DB, id string, state Session) error {
	b, e := s.Vault.Seal("grant:"+id, state)
	if e != nil {
		return e
	}
	_, e = db.Exec(ctx, "INSERT INTO grant_sessions(id,data_cipher) VALUES($1,$2) ON CONFLICT(id) DO UPDATE SET data_cipher=EXCLUDED.data_cipher,updated_at=now()", id, b)
	return e
}

// LoadSession decrypts state only inside the integration boundary.
func (s *Store) LoadSession(ctx context.Context, db DB, id string) (Session, error) {
	var b []byte
	if e := db.QueryRow(ctx, "SELECT data_cipher FROM grant_sessions WHERE id=$1", id).Scan(&b); e != nil {
		return Session{}, e
	}
	var state Session
	e := s.Vault.Open("grant:"+id, b, &state)
	return state, e
}

// ErrBusy means a different process owns the per-grant network critical section.
var ErrBusy = errors.New("grant is in use")

func lockID(id string) int64 {
	h := sha256.Sum256([]byte("grant:" + id))
	return int64(binary.BigEndian.Uint64(h[:8]) & 0x7fffffffffffffff)
}

// WithSession serializes calls and rotation per grant across service instances.
// The lock is connection-bound rather than transaction-bound, allowing durable
// checkpoints before non-idempotent upstream calls without releasing the lock.
func (s *Store) WithSession(ctx context.Context, id string, fn func(context.Context, *Session, DB) error) error {
	conn, e := s.DB.Pool.Acquire(ctx)
	if e != nil {
		return e
	}
	defer conn.Release()
	var locked bool
	if e = conn.QueryRow(ctx, "SELECT pg_try_advisory_lock($1)", lockID(id)).Scan(&locked); e != nil {
		return e
	}
	if !locked {
		return ErrBusy
	}
	defer func() {
		cleanup, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		if _, e := conn.Exec(cleanup, "SELECT pg_advisory_unlock($1)", lockID(id)); e != nil {
			_ = conn.Conn().Close(cleanup)
		}
	}()
	state, e := s.LoadSession(ctx, conn, id)
	if e != nil {
		return e
	}
	return fn(ctx, &state, conn)
}

// EnsureToken rotates expired tokens including those already expired upstream.
// A durable 'rotating' marker prevents repeating rotation after a lost response.
func (s *Store) EnsureToken(ctx context.Context, db DB, id string, state *Session, client *op.Client) error {
	if state.Blocked || state.Rotating || state.Grant.AccessToken == nil {
		return &op.Error{Status: 503, Code: "grant_revoked"}
	}
	if time.Now().Before(state.TokenExpiresAt.Add(-15 * time.Second)) {
		return nil
	}
	state.Rotating = true
	if e := s.SaveSession(ctx, db, id, *state); e != nil {
		return e
	}
	token, e := client.RotateToken(ctx, *state.Grant.AccessToken)
	if e != nil {
		state.Blocked = true
		_ = s.SaveSession(ctx, db, id, *state)
		return e
	}
	state.Grant.AccessToken = &token
	if e = op.CheckTokenOrigin(state.Grant, state.Wallet.AuthServer); e != nil {
		state.Blocked = true
		_ = s.SaveSession(ctx, db, id, *state)
		return e
	}
	state.TokenExpiresAt = time.Now().Add(time.Duration(token.ExpiresIn) * time.Second)
	state.Rotating = false
	return s.SaveSession(ctx, db, id, *state)
}

// Attempt identifies a one-use callback. Nonces and continuation credentials live
// only in its associated encrypted session.
type Attempt struct {
	ID, ResourceID, SessionID, ReturnURL string
	ExpiresAt                            time.Time
	UsedAt                               *time.Time
}

// InsertAttempt persists the callback with the resource transaction.
func InsertAttempt(ctx context.Context, db DB, a Attempt) error {
	_, e := db.Exec(ctx, "INSERT INTO grant_attempts(id,resource_id,session_id,return_url,expires_at) VALUES($1,$2,$3,$4,$5)", a.ID, a.ResourceID, a.SessionID, a.ReturnURL, a.ExpiresAt)
	return e
}

// ReadAttempt locks a callback so verification and consumption can be atomic.
func ReadAttempt(ctx context.Context, tx pgx.Tx, id string) (Attempt, error) {
	var a Attempt
	e := tx.QueryRow(ctx, "SELECT id::text,resource_id::text,session_id::text,return_url,expires_at,used_at FROM grant_attempts WHERE id=$1 FOR UPDATE", id).Scan(&a.ID, &a.ResourceID, &a.SessionID, &a.ReturnURL, &a.ExpiresAt, &a.UsedAt)
	return a, e
}

// ConsumeAttempt only succeeds for the first verified callback or expiry worker.
func ConsumeAttempt(ctx context.Context, db DB, id string) (bool, error) {
	tag, e := db.Exec(ctx, "UPDATE grant_attempts SET used_at=now() WHERE id=$1 AND used_at IS NULL", id)
	return e == nil && tag.RowsAffected() == 1, e
}

// Job carries durable encrypted execution progress and a lease owner.
type Job struct {
	ID, ResourceID, GrantID, Kind, Owner string
	Data                                 []byte
}

// Enqueue adds a job in the same transaction that creates or changes its resource.
func (s *Store) Enqueue(ctx context.Context, db DB, resourceID, grantID, kind string, state any, at time.Time) (string, error) {
	id := uuid.NewString()
	b, e := s.Vault.Seal("job:"+id, state)
	if e != nil {
		return "", e
	}
	var grant any
	if grantID != "" {
		grant = grantID
	}
	_, e = db.Exec(ctx, "INSERT INTO jobs(id,resource_id,grant_id,kind,data_cipher,available_at) VALUES($1,$2,$3,$4,$5,$6)", id, resourceID, grant, kind, b, at)
	return id, e
}

// Claim leases work with SKIP LOCKED. Unfinished start jobs preserve FIFO for a
// grant; polling an existing outgoing never holds up the next start job.
func (s *Store) Claim(ctx context.Context, owner string) (Job, error) {
	var j Job
	e := s.DB.Pool.QueryRow(ctx, `UPDATE jobs SET status='running',owner=$1,locked_until=now()+interval '2 minutes'
 WHERE id=(SELECT j.id FROM jobs j WHERE ((j.status='queued' AND j.available_at<=now()) OR (j.status='running' AND j.locked_until<now()))
 AND NOT EXISTS(SELECT 1 FROM jobs earlier WHERE j.kind='start' AND earlier.kind='start' AND earlier.grant_id=j.grant_id AND earlier.seq<j.seq AND earlier.status<>'done')
 ORDER BY j.available_at,j.seq FOR UPDATE SKIP LOCKED LIMIT 1)
 RETURNING id::text,resource_id::text,COALESCE(grant_id::text,''),kind,data_cipher,owner::text`, owner).Scan(&j.ID, &j.ResourceID, &j.GrantID, &j.Kind, &j.Data, &j.Owner)
	return j, e
}

// Checkpoint commits progress before an upstream mutation while retaining ownership.
func (s *Store) Checkpoint(ctx context.Context, j *Job, state any) error {
	b, e := s.Vault.Seal("job:"+j.ID, state)
	if e != nil {
		return e
	}
	tag, e := s.DB.Pool.Exec(ctx, "UPDATE jobs SET data_cipher=$1 WHERE id=$2 AND owner=$3 AND status='running'", b, j.ID, j.Owner)
	if e != nil {
		return e
	}
	if tag.RowsAffected() != 1 {
		return errors.New("job lease lost")
	}
	j.Data = b
	return nil
}

// Resume schedules the next stage, allowing later starts to run once this is a poll.
func (s *Store) Resume(ctx context.Context, j Job, kind string, at time.Time) error {
	tag, e := s.DB.Pool.Exec(ctx, "UPDATE jobs SET status='queued',kind=$1,available_at=$2,owner=NULL,locked_until=NULL WHERE id=$3 AND owner=$4 AND status='running'", kind, at, j.ID, j.Owner)
	if e != nil {
		return e
	}
	if tag.RowsAffected() != 1 {
		return errors.New("job lease lost")
	}
	return nil
}

// Finish acknowledges work without allowing a stale worker to acknowledge a new lease.
func (s *Store) Finish(ctx context.Context, j Job) error {
	tag, e := s.DB.Pool.Exec(ctx, "UPDATE jobs SET status='done',owner=NULL,locked_until=NULL WHERE id=$1 AND owner=$2 AND status='running'", j.ID, j.Owner)
	if e != nil {
		return e
	}
	if tag.RowsAffected() != 1 {
		return errors.New("job lease lost")
	}
	return nil
}
