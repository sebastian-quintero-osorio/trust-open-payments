package application

import (
	"context"
	"errors"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	op "github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/openpayments"
	"github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/workflow"
	"log/slog"
	"sync"
	"time"
)

// ErrReconciliationRequired means a payment may already have moved funds. Losing
// permission to inspect it must never turn it into a retryable financial failure.
var ErrReconciliationRequired = errors.New("financial reconciliation required")

func submitted(f Flow) bool { return f.Stage == "poll" || f.Stage == "outgoing_uncertain" }

// Start runs workers and expiry/outbox maintenance until cancellation. The returned
// wait function joins all goroutines before the database pool is closed.
func (s *Service) Start(ctx context.Context) func() {
	var wg sync.WaitGroup
	for range 4 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			owner := uuid.NewString()
			ticker := time.NewTicker(100 * time.Millisecond)
			defer ticker.Stop()
			for {
				select {
				case <-ctx.Done():
					return
				case <-ticker.C:
					if _, e := s.RunOnce(ctx, owner); e != nil && ctx.Err() == nil {
						slog.Error("workflow execution deferred", "reason", "dependency failure")
					}
				}
			}
		}()
	}
	wg.Add(1)
	go func() {
		defer wg.Done()
		ticker := time.NewTicker(time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				maintenance, cancel := context.WithTimeout(ctx, 20*time.Second)
				if e := s.ExpireConsents(maintenance); e != nil && ctx.Err() == nil {
					slog.Error("consent expiry deferred")
				}
				if e := s.DeliverWebhooks(maintenance); e != nil && ctx.Err() == nil {
					slog.Error("webhook delivery deferred")
				}
				if e := s.DB.Cleanup(maintenance); e != nil && ctx.Err() == nil {
					slog.Error("idempotency cleanup deferred")
				}
				cancel()
			}
		}
	}()
	return wg.Wait
}

// RunOnce claims one durable stage. It is exported for deterministic integration tests.
func (s *Service) RunOnce(ctx context.Context, owner string) (bool, error) {
	step, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	job, e := s.Work.Claim(step, owner)
	if errors.Is(e, pgx.ErrNoRows) {
		return false, nil
	}
	if e != nil {
		return false, e
	}
	if job.Kind == "continue" || job.Kind == "cancel" {
		e = s.control(step, &job)
	} else {
		e = s.executeFlow(step, &job)
	}
	if e != nil {
		reason := "dependency_failure"
		if errors.Is(e, ErrReconciliationRequired) {
			reason = "reconciliation_required"
			job.Kind = "poll"
		}
		slog.Warn("financial workflow deferred", "resource_id", job.ResourceID, "job_id", job.ID, "stage_kind", job.Kind, "reason", reason)
		cleanup, stop := context.WithTimeout(context.WithoutCancel(ctx), 3*time.Second)
		defer stop()
		if re := s.Work.Resume(cleanup, job, job.Kind, time.Now().Add(s.Config.PollInterval)); re != nil {
			return true, re
		}
	}
	return true, e
}
func (s *Service) control(ctx context.Context, j *workflow.Job) error {
	var control Control
	if e := s.Work.Vault.Open("job:"+j.ID, j.Data, &control); e != nil {
		return e
	}
	return s.Work.WithSession(ctx, j.GrantID, func(ctx context.Context, session *workflow.Session, db workflow.DB) error {
		if j.Kind == "cancel" {
			session.Blocked = true
			if e := s.Work.SaveSession(ctx, db, j.GrantID, *session); e != nil {
				return e
			}
			e := s.OP.CancelGrant(ctx, session.Grant)
			var remote *op.Error
			if e != nil && !(errors.As(e, &remote) && (remote.Status == 401 || remote.Status == 403 || remote.Status == 404 || remote.Status == 410)) {
				return e
			}
			return s.Work.Finish(ctx, *j)
		}
		var kind string
		if e := s.DB.Pool.QueryRow(ctx, "SELECT kind FROM resources WHERE id=$1", j.ResourceID).Scan(&kind); e != nil {
			return e
		}
		v, e := read(ctx, s.DB.Pool, j.ResourceID, kind, false)
		if e != nil {
			return e
		}
		var current string
		if e = s.DB.Pool.QueryRow(ctx, "SELECT session_id::text FROM resource_sessions WHERE resource_id=$1", j.ResourceID).Scan(&current); e != nil {
			return e
		}
		if status(v) != "awaiting_authorization" || current != j.GrantID {
			return s.Work.Finish(ctx, *j)
		}
		if session.Grant.AccessToken == nil {
			if control.Calling {
				return s.failConsent(ctx, *j, kind, "upstream_error")
			}
			control.Calling = true
			if e = s.Work.Checkpoint(ctx, j, control); e != nil {
				return e
			}
			g, e := s.OP.ContinueGrant(ctx, session.Grant, control.Ref)
			if e != nil {
				var remote *op.Error
				if errors.As(e, &remote) && remote.Code == "too_fast" {
					control.Calling = false
					if e = s.Work.Checkpoint(ctx, j, control); e != nil {
						return e
					}
					return s.Work.Resume(ctx, *j, "continue", time.Now().Add(5*time.Second))
				}
				return s.failConsent(ctx, *j, kind, "upstream_error")
			}
			if g.Continue.URI == "" {
				g.Continue = session.Grant.Continue
			}
			if e = op.CheckTokenOrigin(g, session.Wallet.AuthServer); e != nil {
				return s.failConsent(ctx, *j, kind, "upstream_error")
			}
			session.Grant = g
			session.TokenExpiresAt = time.Now().Add(time.Duration(g.AccessToken.ExpiresIn) * time.Second)
			if e = s.Work.SaveSession(ctx, db, j.GrantID, *session); e != nil {
				return e
			}
		}
		tx, e := s.DB.Pool.Begin(ctx)
		if e != nil {
			return e
		}
		defer func() { _ = tx.Rollback(ctx) }()
		v, e = read(ctx, tx, j.ResourceID, kind, true)
		if e != nil {
			return e
		}
		if e = tx.QueryRow(ctx, "SELECT session_id::text FROM resource_sessions WHERE resource_id=$1", j.ResourceID).Scan(&current); e != nil {
			return e
		}
		if status(v) != "awaiting_authorization" || current != j.GrantID {
			return s.Work.Finish(ctx, *j)
		}
		target := "processing"
		if kind == "pool_wallet" {
			target = "authorized"
		}
		if e = s.changeState(ctx, tx, j.ResourceID, kind, v, target, ""); e != nil {
			return e
		}
		if kind == "payment" {
			flow, e := s.loadFlow(ctx, tx, j.ResourceID)
			if e != nil {
				return e
			}
			if _, e = s.Work.Enqueue(ctx, tx, j.ResourceID, j.GrantID, "start", flow, time.Now()); e != nil {
				return e
			}
		}
		if e = tx.Commit(ctx); e != nil {
			return e
		}
		return s.Work.Finish(ctx, *j)
	})
}
func (s *Service) failConsent(ctx context.Context, j workflow.Job, kind, code string) error {
	tx, e := s.DB.Pool.Begin(ctx)
	if e != nil {
		return e
	}
	defer func() { _ = tx.Rollback(ctx) }()
	v, e := read(ctx, tx, j.ResourceID, kind, true)
	if e != nil {
		return e
	}
	if status(v) == "awaiting_authorization" {
		target := "failed"
		if kind == "pool_wallet" {
			target = "unauthorized"
		}
		if e = s.changeState(ctx, tx, j.ResourceID, kind, v, target, code); e != nil {
			return e
		}
		if _, e = s.Work.Enqueue(ctx, tx, j.ResourceID, j.GrantID, "cancel", Control{}, time.Now()); e != nil {
			return e
		}
	}
	if e = tx.Commit(ctx); e != nil {
		return e
	}
	return s.Work.Finish(ctx, j)
}
func (s *Service) executeFlow(ctx context.Context, j *workflow.Job) error {
	var f Flow
	if e := s.Work.Vault.Open("job:"+j.ID, j.Data, &f); e != nil {
		return e
	}
	return s.Work.WithSession(ctx, j.GrantID, func(ctx context.Context, session *workflow.Session, db workflow.DB) error {
		v, e := read(ctx, s.DB.Pool, j.ResourceID, f.Kind, false)
		if e != nil {
			return e
		}
		if status(v) != "processing" {
			return s.Work.Finish(ctx, *j)
		}
		if f.Kind != "payment" {
			var usable bool
			if e = s.DB.Pool.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM resources r JOIN resource_sessions rs ON rs.resource_id=r.id WHERE rs.session_id=$1 AND r.kind='pool_wallet' AND r.status IN ('authorized','assigned'))", j.GrantID).Scan(&usable); e != nil {
				return e
			}
			if !usable {
				if submitted(f) {
					return ErrReconciliationRequired
				}
				return s.failFlow(ctx, *j, &f, "grant_revoked", session)
			}
		}
		if e = s.Work.EnsureToken(ctx, db, j.GrantID, session, s.OP); e != nil {
			if submitted(f) {
				return ErrReconciliationRequired
			}
			return s.failFlow(ctx, *j, &f, failureCode(e), session)
		}
		switch f.Stage {
		case "incoming":
			if e = s.prepareIncoming(ctx, &f, 15*time.Minute); e != nil {
				return s.failFlow(ctx, *j, &f, receiverFailure(e), session)
			}
			if f.UseQuote {
				f.Stage = "quote"
			} else {
				f.Stage = "outgoing"
			}
		case "quote":
			if e = s.prepareQuote(ctx, &f); e != nil {
				return s.failFlow(ctx, *j, &f, failureCode(e), session)
			}
			if f.Kind == "payment" {
				cmp, e := f.Quote.DebitAmount.Domain().Compare(f.DebitLimit.Domain())
				if e != nil || cmp > 0 {
					return s.failFlow(ctx, *j, &f, "quote_expired", session)
				}
			}
			if f.FixedSend {
				cmp, e := f.Quote.DebitAmount.Domain().Compare(f.Amount.Domain())
				if e != nil || cmp != 0 {
					return s.failFlow(ctx, *j, &f, "invalid_amount", session)
				}
			}
			f.Stage = "outgoing"
		case "outgoing":
			if f.UseQuote && !time.Now().Before(f.Quote.ExpiresAt) {
				f.Stage = "quote"
				break
			}
			f.Stage = "outgoing_uncertain"
			if e = s.Work.Checkpoint(ctx, j, f); e != nil {
				return e
			}
			quote := ""
			if f.UseQuote {
				quote = f.Quote.ID
			}
			var debit *op.Amount
			if f.FixedSend {
				debit = &f.Amount
			}
			metadata := map[string]any{"external_ref": f.ExternalRef, "op_operation_id": j.ResourceID}
			if f.Description != "" {
				metadata["description"] = f.Description
			}
			f.Outgoing, e = s.OP.CreateOutgoing(ctx, f.Source, session.Grant.AccessToken.Value, quote, f.Incoming.ID, debit, metadata)
			if e != nil {
				var remote *op.Error
				if errors.As(e, &remote) && remote.Uncertain {
					if e = s.Work.Checkpoint(ctx, j, f); e != nil {
						return e
					}
					return s.Work.Resume(ctx, *j, "poll", time.Now().Add(s.Config.PollInterval))
				}
				if !f.UseQuote && errors.As(e, &remote) && remote.Status == 400 && remote.Code != "InvalidAmount" && remote.Code != "NonPositiveReceiveAmount" {
					f.UseQuote = true
					f.Stage = "quote"
					break
				}
				return s.failFlow(ctx, *j, &f, failureCode(e), session)
			}
			f.Stage = "poll"
		case "outgoing_uncertain":
			out, found, e := s.OP.FindOutgoing(ctx, f.Source, session.Grant.AccessToken.Value, j.ResourceID)
			if e != nil {
				return e
			}
			if found {
				f.Outgoing = out
				f.Stage = "poll"
			}
		case "poll":
			return s.pollFlow(ctx, j, &f, session)
		default:
			return errors.New("unknown financial workflow stage")
		}
		if e = s.Work.Checkpoint(ctx, j, f); e != nil {
			return e
		}
		if e = s.updateProgress(ctx, j.ResourceID, f); e != nil {
			return e
		}
		kind := "start"
		delay := time.Duration(0)
		if f.Stage == "poll" || f.Stage == "outgoing_uncertain" {
			kind = "poll"
			delay = s.Config.PollInterval
		}
		return s.Work.Resume(ctx, *j, kind, time.Now().Add(delay))
	})
}
func (s *Service) pollFlow(ctx context.Context, j *workflow.Job, f *Flow, session *workflow.Session) error {
	out, e := s.OP.GetOutgoing(ctx, f.Outgoing.ID, session.Grant.AccessToken.Value)
	if e != nil {
		if failureCode(e) == "grant_revoked" {
			return ErrReconciliationRequired
		}
		return e
	}
	if out.ID == "" {
		out.ID = f.Outgoing.ID
	}
	f.Outgoing = out
	if e = s.Work.Checkpoint(ctx, j, *f); e != nil {
		return e
	}
	if out.Failed {
		code := "internal"
		switch out.Metadata["cancellationReason"] {
		case "Insufficient funds":
			code = "insufficient_balance"
		case "InvalidAmount":
			code = "invalid_amount"
		case "NonPositiveReceiveAmount":
			code = "amount_too_small"
		}
		return s.failFlow(ctx, *j, f, code, session)
	}
	if !time.Now().Before(f.IncomingTokenExpires.Add(-15 * time.Second)) {
		g, e := s.OP.NonInteractiveGrant(ctx, f.Receiver, "incoming-payment", incomingActions(f.FixedSend))
		if e != nil {
			return e
		}
		f.IncomingGrant = g
		f.IncomingTokenExpires = time.Now().Add(time.Duration(g.AccessToken.ExpiresIn) * time.Second)
		if e = s.Work.Checkpoint(ctx, j, *f); e != nil {
			return e
		}
	}
	incoming, e := s.OP.GetIncoming(ctx, f.Incoming.ID, f.IncomingGrant.AccessToken.Value)
	if e != nil {
		return e
	}
	if incoming.ID == "" {
		incoming.ID = f.Incoming.ID
	}
	if incoming.ExpiresAt.IsZero() {
		incoming.ExpiresAt = f.Incoming.ExpiresAt
	}
	f.Incoming = incoming
	settled := false
	if f.FixedSend {
		if out.DebitAmount.Domain().Validate(true) == nil && out.ReceiveAmount.Domain().Validate(true) == nil {
			debit, e := out.DebitAmount.Domain().Compare(f.Amount.Domain())
			if e != nil || debit != 0 {
				return ErrReconciliationRequired
			}
			sent, e := out.SentAmount.Domain().Compare(out.DebitAmount.Domain())
			received, re := incoming.ReceivedAmount.Domain().Compare(out.ReceiveAmount.Domain())
			settled = e == nil && sent == 0 || re == nil && received >= 0
		}
		if settled {
			completed, e := s.OP.CompleteIncoming(ctx, f.Incoming.ID, f.IncomingGrant.AccessToken.Value)
			if e != nil {
				return e
			}
			settled = completed.Completed
		}
	} else {
		cmp, e := incoming.ReceivedAmount.Domain().Compare(f.Amount.Domain())
		settled = incoming.Completed && e == nil && cmp >= 0
	}
	if settled {
		return s.completeFlow(ctx, *j, f)
	}
	if !incoming.ExpiresAt.IsZero() && time.Now().After(incoming.ExpiresAt) {
		return ErrReconciliationRequired
	}
	if e = s.updateProgress(ctx, j.ResourceID, *f); e != nil {
		return e
	}
	if e = s.Work.Checkpoint(ctx, j, *f); e != nil {
		return e
	}
	return s.Work.Resume(ctx, *j, "poll", time.Now().Add(s.Config.PollInterval))
}
func incomingActions(complete bool) []string {
	actions := []string{"create", "read"}
	if complete {
		actions = append(actions, "complete")
	}
	return actions
}
func failureCode(e error) string {
	var remote *op.Error
	if errors.As(e, &remote) {
		switch remote.Code {
		case "InvalidAmount", "invalid_amount":
			return "invalid_amount"
		case "NonPositiveReceiveAmount":
			return "amount_too_small"
		case "grant_revoked":
			return "grant_revoked"
		}
		if remote.Status == 401 || remote.Status == 403 {
			return "grant_revoked"
		}
		if remote.Status >= 500 {
			return "upstream_error"
		}
	}
	return "internal"
}
func receiverFailure(e error) string {
	code := failureCode(e)
	if code == "invalid_amount" || code == "amount_too_small" {
		return code
	}
	var remote *op.Error
	if errors.As(e, &remote) && remote.Status >= 400 && remote.Status < 500 {
		return "receiver_unreachable"
	}
	return "upstream_error"
}
func (s *Service) updateProgress(ctx context.Context, id string, f Flow) error {
	tx, e := s.DB.Pool.Begin(ctx)
	if e != nil {
		return e
	}
	defer func() { _ = tx.Rollback(ctx) }()
	v, e := read(ctx, tx, id, f.Kind, true)
	if e != nil {
		return e
	}
	if status(v) != "processing" {
		return nil
	}
	progress(v, f)
	if e = save(ctx, tx, id, v); e != nil {
		return e
	}
	return tx.Commit(ctx)
}
func progress(v map[string]any, f Flow) {
	if f.Incoming.ID != "" {
		v["incoming_payment_url"] = f.Incoming.ID
	}
	if f.Outgoing.ID != "" {
		v["outgoing_payment_url"] = f.Outgoing.ID
	}
	if f.Kind != "sim_payment" {
		if f.Outgoing.SentAmount.Domain().Validate(false) == nil {
			v["sent_amount"] = f.Outgoing.SentAmount.Domain()
		}
		if f.Kind == "transfer" && f.Outgoing.ReceiveAmount.Domain().Validate(true) == nil {
			v["receive_amount"] = f.Outgoing.ReceiveAmount.Domain()
		}
		if f.Kind == "payment" {
			v["updated_at"] = now()
			v["debit_amount"] = f.Quote.DebitAmount.Domain()
		}
	}
}
func (s *Service) failFlow(ctx context.Context, j workflow.Job, f *Flow, code string, session *workflow.Session) error {
	tx, e := s.DB.Pool.Begin(ctx)
	if e != nil {
		return e
	}
	defer func() { _ = tx.Rollback(ctx) }()
	v, e := read(ctx, tx, j.ResourceID, f.Kind, true)
	if e != nil {
		return e
	}
	if status(v) != "processing" {
		return s.Work.Finish(ctx, j)
	}
	progress(v, *f)
	if f.Kind == "payment" && code != "insufficient_balance" && code != "quote_expired" && code != "receiver_unreachable" && code != "upstream_error" {
		code = "internal"
	}
	if f.Kind == "transfer" {
		v["failure_code"] = code
		v["retryable"] = code == "insufficient_balance" || code == "receiver_unreachable" || code == "upstream_error" || code == "internal"
	}
	if e = s.changeState(ctx, tx, j.ResourceID, f.Kind, v, "failed", code); e != nil {
		return e
	}
	if f.Kind == "sim_payment" {
		if e = s.assignSequence(ctx, tx, j.ResourceID, v); e != nil {
			return e
		}
	}
	if code == "grant_revoked" || session.Blocked || session.Rotating {
		session.Blocked = true
		if e = s.Work.SaveSession(ctx, tx, j.GrantID, *session); e != nil {
			return e
		}
		if e = s.revokeGrantAccounts(ctx, tx, j.GrantID); e != nil {
			return e
		}
	}
	if e = tx.Commit(ctx); e != nil {
		return e
	}
	slog.Info("financial operation failed", "resource_id", j.ResourceID, "failure_code", code)
	return s.Work.Finish(ctx, j)
}
func (s *Service) completeFlow(ctx context.Context, j workflow.Job, f *Flow) error {
	if f.Kind == "sim_payment" {
		if e := s.queueWebhook(ctx, j.ResourceID, *f); e != nil {
			return e
		}
		return s.Work.Finish(ctx, j)
	}
	tx, e := s.DB.Pool.Begin(ctx)
	if e != nil {
		return e
	}
	defer func() { _ = tx.Rollback(ctx) }()
	v, e := read(ctx, tx, j.ResourceID, f.Kind, true)
	if e != nil {
		return e
	}
	if status(v) == "processing" {
		progress(v, *f)
		if e = s.changeState(ctx, tx, j.ResourceID, f.Kind, v, "completed", ""); e != nil {
			return e
		}
		if f.AccountID != "" {
			if e = s.updateLedger(ctx, tx, f.AccountID); e != nil {
				return e
			}
		}
	}
	if e = tx.Commit(ctx); e != nil {
		return e
	}
	return s.Work.Finish(ctx, j)
}
func (s *Service) revokeGrantAccounts(ctx context.Context, tx pgx.Tx, grantID string) error {
	rows, e := tx.Query(ctx, "SELECT resource_id::text FROM resource_sessions WHERE session_id=$1", grantID)
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
		pool, e := read(ctx, tx, id, "pool_wallet", true)
		if errors.Is(e, pgx.ErrNoRows) {
			continue
		}
		if e != nil {
			return e
		}
		pool["status"] = "revoked"
		if e = save(ctx, tx, id, pool); e != nil {
			return e
		}
		if accountID, ok := pool["collection_account_id"].(string); ok {
			v, e := read(ctx, tx, accountID, "collection_account", true)
			if e != nil {
				return e
			}
			v["status"] = "revoked"
			if _, e = tx.Exec(ctx, "UPDATE collection_wallets SET active=false WHERE account_id=$1", accountID); e != nil {
				return e
			}
			if e = save(ctx, tx, accountID, v); e != nil {
				return e
			}
		}
	}
	return nil
}
