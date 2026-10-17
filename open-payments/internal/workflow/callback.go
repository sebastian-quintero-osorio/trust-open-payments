package workflow

import (
	"context"
	"errors"
	"github.com/jackc/pgx/v5"
	op "github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/openpayments"
	"time"
)

// ErrInvalidCallback never consumes a live attempt, so an invalid hash cannot
// prevent a later genuine wallet redirect from completing consent.
var ErrInvalidCallback = errors.New("invalid grant callback")

// CallbackClaim tells the caller whether work is already handled or has expired.
type CallbackClaim struct {
	Attempt Attempt
	Replay  bool
	Expired bool
}

// VerifyAndConsume verifies the nonce binding and consumes the attempt in the
// same transaction the caller uses to enqueue continuation or record rejection.
func (s *Store) VerifyAndConsume(ctx context.Context, tx pgx.Tx, id, ref, hash, result string) (CallbackClaim, error) {
	a, e := ReadAttempt(ctx, tx, id)
	if e != nil {
		return CallbackClaim{}, e
	}
	out := CallbackClaim{Attempt: a}
	var current, status string
	e = tx.QueryRow(ctx, `SELECT rs.session_id::text,r.status FROM resource_sessions rs JOIN resources r ON r.id=rs.resource_id WHERE r.id=$1 FOR UPDATE OF r`, a.ResourceID).Scan(&current, &status)
	if e != nil {
		return out, e
	}
	if a.UsedAt != nil || current != a.SessionID || status != "awaiting_authorization" {
		out.Replay = true
		return out, nil
	}
	out.Expired = time.Now().After(a.ExpiresAt.Add(30 * time.Second))
	if !out.Expired {
		if result == "" {
			state, e := s.LoadSession(ctx, tx, a.SessionID)
			if e != nil {
				return out, e
			}
			if !op.VerifyFinish(state.ClientNonce, state.ServerNonce, ref, state.Wallet.AuthServer, hash) {
				return out, ErrInvalidCallback
			}
		} else if ref != "" || hash != "" || len(result) > 100 {
			return out, ErrInvalidCallback
		}
	}
	used, e := ConsumeAttempt(ctx, tx, id)
	if e != nil {
		return out, e
	}
	out.Replay = !used
	return out, nil
}
