package workflow

import (
	"context"
	"encoding/json"
	"errors"

	"github.com/jackc/pgx/v5"
)

var transitions = map[string]map[string][]string{
	"payment":  {"awaiting_authorization": {"processing", "rejected", "expired", "failed"}, "processing": {"completed", "failed"}},
	"transfer": {"processing": {"completed", "failed"}}, "sim_payment": {"processing": {"completed", "failed"}},
	"pool_wallet":        {"unauthorized": {"awaiting_authorization"}, "awaiting_authorization": {"authorized", "unauthorized", "revoked"}, "authorized": {"assigned", "revoked"}, "assigned": {"revoked"}, "revoked": {"awaiting_authorization"}},
	"collection_account": {"active": {"revoked"}},
}

// CanTransition is the single state-machine policy used by HTTP and workers.
func CanTransition(kind, from, to string) bool {
	for _, allowed := range transitions[kind][from] {
		if allowed == to {
			return true
		}
	}
	return false
}

// Transition protects terminal states with an atomic compare-and-set update.
func Transition(ctx context.Context, tx pgx.Tx, id, kind, from, to string, payload json.RawMessage) (bool, error) {
	if !CanTransition(kind, from, to) {
		return false, errors.New("invalid resource state transition")
	}
	tag, e := tx.Exec(ctx, "UPDATE resources SET status=$1,payload=$2,updated_at=now() WHERE id=$3 AND kind=$4 AND status=$5", to, []byte(payload), id, kind, from)
	return e == nil && tag.RowsAffected() == 1, e
}
