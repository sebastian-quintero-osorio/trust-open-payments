package application

import (
	"context"
	"encoding/json"
	"errors"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/workflow"
	"html/template"
	"net/http"
	"net/url"
	"time"
)

func (s *Service) callback(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("attempt_id")
	if _, e := uuid.Parse(id); e != nil {
		http.Error(w, "Invalid callback", http.StatusBadRequest)
		return
	}
	q := r.URL.Query()
	for _, key := range []string{"hash", "interact_ref", "result"} {
		if len(q[key]) > 1 {
			http.Error(w, "Invalid callback", http.StatusBadRequest)
			return
		}
	}
	tx, e := s.DB.Pool.Begin(r.Context())
	if e != nil {
		http.Error(w, "Service unavailable", http.StatusServiceUnavailable)
		return
	}
	defer func() { _ = tx.Rollback(r.Context()) }()
	claim, e := s.Work.VerifyAndConsume(r.Context(), tx, id, q.Get("interact_ref"), q.Get("hash"), q.Get("result"))
	if e != nil {
		http.Error(w, "Invalid callback", http.StatusBadRequest)
		return
	}
	var kind string
	if e = tx.QueryRow(r.Context(), "SELECT kind FROM resources WHERE id=$1", claim.Attempt.ResourceID).Scan(&kind); e != nil {
		http.Error(w, "Service unavailable", http.StatusServiceUnavailable)
		return
	}
	value, e := read(r.Context(), tx, claim.Attempt.ResourceID, kind, false)
	if e != nil {
		http.Error(w, "Service unavailable", http.StatusServiceUnavailable)
		return
	}
	hint := status(value)
	if !claim.Replay {
		result := q.Get("result")
		if result != "" || claim.Expired {
			target := "failed"
			if claim.Expired || result == "invalid_interaction" {
				target = "expired"
			} else if result == "grant_rejected" || result == "request_denied" {
				target = "rejected"
			}
			if kind == "pool_wallet" {
				target = "unauthorized"
			}
			if e = s.changeState(r.Context(), tx, claim.Attempt.ResourceID, kind, value, target, "upstream_error"); e != nil {
				http.Error(w, "Service unavailable", http.StatusServiceUnavailable)
				return
			}
			if _, e = s.Work.Enqueue(r.Context(), tx, claim.Attempt.ResourceID, claim.Attempt.SessionID, "cancel", Control{}, time.Now()); e != nil {
				http.Error(w, "Service unavailable", http.StatusServiceUnavailable)
				return
			}
			hint = target
		} else {
			session, e := s.Work.LoadSession(r.Context(), tx, claim.Attempt.SessionID)
			if e != nil {
				http.Error(w, "Service unavailable", http.StatusServiceUnavailable)
				return
			}
			at := session.CreatedAt.Add(time.Duration(session.Grant.Continue.Wait) * time.Second)
			if _, e = s.Work.Enqueue(r.Context(), tx, claim.Attempt.ResourceID, claim.Attempt.SessionID, "continue", Control{Ref: q.Get("interact_ref")}, at); e != nil {
				http.Error(w, "Service unavailable", http.StatusServiceUnavailable)
				return
			}
			hint = "processing"
			if kind == "pool_wallet" {
				hint = "authorized"
			}
		}
	}
	if e = tx.Commit(r.Context()); e != nil {
		http.Error(w, "Service unavailable", http.StatusServiceUnavailable)
		return
	}
	if claim.Attempt.ReturnURL != "" && s.Config.ReturnURLAllowed(claim.Attempt.ReturnURL) {
		u, e := url.Parse(claim.Attempt.ReturnURL)
		if e != nil {
			http.Error(w, "Invalid return URL", http.StatusBadRequest)
			return
		}
		q := u.Query()
		q.Set("op_status", hint)
		q.Set("op_id", claim.Attempt.ResourceID)
		q.Set("op_resource", kind)
		u.RawQuery = q.Encode()
		http.Redirect(w, r, u.String(), http.StatusFound)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_ = callbackPage.Execute(w, hint)
}

var callbackPage = template.Must(template.New("callback").Parse(`<!doctype html><html lang="es"><meta charset="utf-8"><title>Trust</title><h1>Autorización Trust</h1><p>Estado: {{.}}</p><p>Puedes cerrar esta ventana.</p></html>`))

func (s *Service) changeState(ctx context.Context, tx pgx.Tx, id, kind string, v map[string]any, target, code string) error {
	from := status(v)
	v["status"] = target
	if kind == "payment" {
		v["updated_at"] = now()
		v["authorization_url"] = nil
		if target == "failed" {
			v["failure_code"] = code
		}
	}
	if kind == "pool_wallet" {
		v["authorization_url"] = nil
		v["authorization_expires_at"] = nil
	}
	if target == "completed" && kind == "transfer" {
		v["completed_at"] = now()
	}
	b, e := json.Marshal(v)
	if e != nil {
		return e
	}
	changed, e := workflow.Transition(ctx, tx, id, kind, from, target, b)
	if e != nil {
		return e
	}
	if !changed {
		return errors.New("resource state changed concurrently")
	}
	return nil
}

// ExpireConsents consumes only unused, current attempts after the grace period.
func (s *Service) ExpireConsents(ctx context.Context) error {
	rows, e := s.DB.Pool.Query(ctx, "SELECT id::text FROM grant_attempts WHERE used_at IS NULL AND expires_at+interval '30 seconds'<now() ORDER BY expires_at LIMIT 100")
	if e != nil {
		return e
	}
	ids := []string{}
	for rows.Next() {
		var id string
		if e = rows.Scan(&id); e != nil {
			rows.Close()
			return e
		}
		ids = append(ids, id)
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return e
	}
	for _, id := range ids {
		if e = s.expireConsent(ctx, id); e != nil {
			return e
		}
	}
	return nil
}
func (s *Service) expireConsent(ctx context.Context, id string) error {
	tx, e := s.DB.Pool.Begin(ctx)
	if e != nil {
		return e
	}
	defer func() { _ = tx.Rollback(ctx) }()
	claim, e := s.Work.VerifyAndConsume(ctx, tx, id, "", "", "")
	if e != nil {
		return e
	}
	if claim.Replay {
		if _, e = workflow.ConsumeAttempt(ctx, tx, id); e != nil {
			return e
		}
		return tx.Commit(ctx)
	}
	var kind string
	if e = tx.QueryRow(ctx, "SELECT kind FROM resources WHERE id=$1", claim.Attempt.ResourceID).Scan(&kind); e != nil {
		return e
	}
	v, e := read(ctx, tx, claim.Attempt.ResourceID, kind, false)
	if e != nil {
		return e
	}
	target := "expired"
	if kind == "pool_wallet" {
		target = "unauthorized"
	}
	if e = s.changeState(ctx, tx, claim.Attempt.ResourceID, kind, v, target, ""); e != nil {
		return e
	}
	if _, e = s.Work.Enqueue(ctx, tx, claim.Attempt.ResourceID, claim.Attempt.SessionID, "cancel", Control{}, time.Now()); e != nil {
		return e
	}
	return tx.Commit(ctx)
}
