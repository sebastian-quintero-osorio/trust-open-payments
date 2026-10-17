package application

import (
	"context"
	"encoding/json"
	"errors"
	"github.com/jackc/pgx/v5"
	"github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/domain"
	"github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/httpapi"
	op "github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/openpayments"
	"github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/persistence"
	"github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/workflow"
	"time"
)

// Bootstrap resolves configured wallets and upserts discovery without resetting grants.
func (s *Service) Bootstrap(ctx context.Context) error {
	wallets := map[string]string{}
	for _, w := range s.Config.PoolWallets {
		wallets[w] = "collection"
	}
	if s.Config.SimPayerWallet != "" {
		wallets[s.Config.SimPayerWallet] = "sim_payer"
	}
	for address, role := range wallets {
		w, e := s.OP.Resolve(ctx, address)
		if e != nil {
			return errors.New("configured pool wallet could not be resolved")
		}
		tx, e := s.DB.Pool.Begin(ctx)
		if e != nil {
			return e
		}
		e = s.bootstrapWallet(ctx, tx, w, role)
		if e != nil {
			_ = tx.Rollback(ctx)
			return e
		}
		if e = tx.Commit(ctx); e != nil {
			return e
		}
	}
	return nil
}
func (s *Service) bootstrapWallet(ctx context.Context, tx pgx.Tx, w op.Wallet, role string) error {
	if _, e := tx.Exec(ctx, "SELECT pg_advisory_xact_lock(hashtextextended($1,0))", "wallet:"+w.ID); e != nil {
		return e
	}
	var id, existingRole string
	e := tx.QueryRow(ctx, "SELECT resource_id::text,role FROM pool_wallets WHERE wallet_address=$1", w.ID).Scan(&id, &existingRole)
	if e == nil && existingRole != role {
		return errors.New("wallet roles conflict")
	}
	if errors.Is(e, pgx.ErrNoRows) {
		id = newID()
		v := map[string]any{"id": id, "wallet_address": w.ID, "role": role, "asset_code": w.AssetCode, "asset_scale": w.AssetScale, "status": "unauthorized", "collection_account_id": nil, "authorization_url": nil, "authorization_expires_at": nil}
		if e = insert(ctx, tx, "pool_wallet", v); e != nil {
			return e
		}
	} else if e != nil {
		return e
	}
	discovery, e := json.Marshal(w)
	if e != nil {
		return e
	}
	tag, e := tx.Exec(ctx, `INSERT INTO pool_wallets(resource_id,wallet_address,role,asset_code,asset_scale,discovery) VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT(wallet_address) DO UPDATE SET discovery=EXCLUDED.discovery WHERE pool_wallets.asset_code=EXCLUDED.asset_code AND pool_wallets.asset_scale=EXCLUDED.asset_scale`, id, w.ID, role, w.AssetCode, w.AssetScale, discovery)
	if e == nil && tag.RowsAffected() != 1 {
		return errors.New("configured wallet asset changed")
	}
	return e
}
func (s *Service) poolWallet(ctx context.Context, db workflow.DB, id string) (op.Wallet, error) {
	var b []byte
	e := db.QueryRow(ctx, "SELECT discovery FROM pool_wallets WHERE resource_id=$1", id).Scan(&b)
	if e != nil {
		return op.Wallet{}, e
	}
	var w op.Wallet
	e = json.Unmarshal(b, &w)
	return w, e
}
func (s *Service) authorizePool(ctx context.Context, tx pgx.Tx, r httpapi.Request) (persistence.Response, error) {
	id := r.HTTP.PathValue("pool_wallet_id")
	v, e := read(ctx, tx, id, "pool_wallet", true)
	if errors.Is(e, pgx.ErrNoRows) {
		return problem(404, "not_found")
	}
	if e != nil {
		return persistence.Response{}, e
	}
	if status(v) == "authorized" || status(v) == "assigned" {
		return problem(409, "already_authorized")
	}
	var input struct {
		ReturnURL string `json:"return_url"`
	}
	if e = json.Unmarshal(r.Body, &input); e != nil {
		return problem(400, "validation_error")
	}
	if input.ReturnURL != "" && !s.Config.ReturnURLAllowed(input.ReturnURL) {
		return problem(422, "return_url_not_allowed")
	}
	w, e := s.poolWallet(ctx, tx, id)
	if e != nil {
		return persistence.Response{}, e
	}
	attempt, e := s.beginConsent(ctx, tx, id, w, input.ReturnURL, nil, "", Flow{})
	if e != nil {
		return upstream(e)
	}
	v["status"] = "awaiting_authorization"
	v["authorization_url"] = attempt.URL
	v["authorization_expires_at"] = attempt.Expires.UTC().Format(time.RFC3339Nano)
	if e = save(ctx, tx, id, v); e != nil {
		return persistence.Response{}, e
	}
	return jsonResponse(201, v)
}

type consent struct {
	URL     string
	Expires time.Time
}

func (s *Service) beginConsent(ctx context.Context, tx pgx.Tx, id string, w op.Wallet, returnURL string, limit *op.Amount, incoming string, flow Flow) (consent, error) {
	attemptID := newID()
	sessionID := newID()
	nonce, e := op.NewNonce()
	if e != nil {
		return consent{}, e
	}
	created := time.Now()
	grant, e := s.OP.InteractiveGrant(ctx, w, s.Config.PublicBaseURL+"/v1/interact/finish/"+attemptID, nonce, limit, incoming)
	if e != nil {
		return consent{}, e
	}
	if e = op.CheckTokenOrigin(grant, w.AuthServer); e != nil {
		return consent{}, e
	}
	session := workflow.Session{Wallet: w, Grant: grant, CreatedAt: created, ClientNonce: nonce, ServerNonce: grant.Interact.Finish}
	if e = s.Work.SaveSession(ctx, tx, sessionID, session); e != nil {
		return consent{}, e
	}
	var oldID string
	e = tx.QueryRow(ctx, "SELECT session_id::text FROM resource_sessions WHERE resource_id=$1", id).Scan(&oldID)
	if e == nil {
		if _, e = s.Work.Enqueue(ctx, tx, id, oldID, "cancel", Control{}, time.Now()); e != nil {
			return consent{}, e
		}
	} else if !errors.Is(e, pgx.ErrNoRows) {
		return consent{}, e
	}
	if _, e = tx.Exec(ctx, "INSERT INTO resource_sessions(resource_id,session_id) VALUES($1,$2) ON CONFLICT(resource_id) DO UPDATE SET session_id=EXCLUDED.session_id", id, sessionID); e != nil {
		return consent{}, e
	}
	expiry := created.Add(s.Config.InteractionTimeout)
	if e = workflow.InsertAttempt(ctx, tx, workflow.Attempt{ID: attemptID, ResourceID: id, SessionID: sessionID, ReturnURL: returnURL, ExpiresAt: expiry}); e != nil {
		return consent{}, e
	}
	flow.Source = w
	flow.GrantID = sessionID
	if e = s.saveFlow(ctx, tx, id, flow); e != nil {
		return consent{}, e
	}
	return consent{URL: grant.Interact.Redirect, Expires: expiry}, nil
}
func (s *Service) createAccount(ctx context.Context, tx pgx.Tx, r httpapi.Request) (persistence.Response, error) {
	var input domain.CreateCollectionAccountRequest
	if e := json.Unmarshal(r.Body, &input); e != nil {
		return problem(400, "validation_error")
	}
	if _, e := tx.Exec(ctx, "SELECT pg_advisory_xact_lock(hashtextextended($1,0))", "merchant:"+input.ExternalRef); e != nil {
		return persistence.Response{}, e
	}
	var exists bool
	if e := tx.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM resources WHERE kind='collection_account' AND external_ref=$1 AND status='active')", input.ExternalRef).Scan(&exists); e != nil {
		return persistence.Response{}, e
	}
	if exists {
		return problem(409, "already_assigned")
	}
	var poolID string
	e := tx.QueryRow(ctx, `SELECT p.resource_id::text FROM pool_wallets p JOIN resources r ON r.id=p.resource_id WHERE p.role='collection' AND p.asset_code=$1 AND r.status='authorized' AND NOT EXISTS(SELECT 1 FROM collection_wallets c WHERE c.pool_id=p.resource_id) ORDER BY r.created_at,r.id FOR UPDATE OF r SKIP LOCKED LIMIT 1`, input.AssetCode).Scan(&poolID)
	if errors.Is(e, pgx.ErrNoRows) {
		return problem(409, "pool_exhausted")
	}
	if e != nil {
		return persistence.Response{}, e
	}
	w, e := s.poolWallet(ctx, tx, poolID)
	if e != nil {
		return persistence.Response{}, e
	}
	id := newID()
	v := map[string]any{"id": id, "external_ref": input.ExternalRef, "display_name": input.DisplayName, "wallet_address": w.ID, "qr_payload": s.Config.PublicBaseURL + "/qr/" + id, "asset_code": w.AssetCode, "asset_scale": w.AssetScale, "status": "active", "created_at": now(), "ledger_balance": domain.Amount{Value: "0", AssetCode: w.AssetCode, AssetScale: w.AssetScale}}
	if e = insert(ctx, tx, "collection_account", v); e != nil {
		return persistence.Response{}, e
	}
	if _, e = tx.Exec(ctx, "INSERT INTO collection_wallets(account_id,pool_id) VALUES($1,$2)", id, poolID); e != nil {
		return persistence.Response{}, e
	}
	pool, e := read(ctx, tx, poolID, "pool_wallet", false)
	if e != nil {
		return persistence.Response{}, e
	}
	pool["status"] = "assigned"
	pool["collection_account_id"] = id
	if e = save(ctx, tx, poolID, pool); e != nil {
		return persistence.Response{}, e
	}
	return jsonResponse(201, v)
}
func (s *Service) revokeAccount(ctx context.Context, _ pgx.Tx, r httpapi.Request) (persistence.Response, error) {
	tx, e := s.DB.Pool.Begin(ctx)
	if e != nil {
		return persistence.Response{}, e
	}
	defer func() { _ = tx.Rollback(ctx) }()
	id := r.HTTP.PathValue("collection_account_id")
	v, e := read(ctx, tx, id, "collection_account", true)
	if errors.Is(e, pgx.ErrNoRows) {
		return problem(404, "not_found")
	}
	if e != nil {
		return persistence.Response{}, e
	}
	if status(v) == "active" {
		v["status"] = "revoked"
		if e = save(ctx, tx, id, v); e != nil {
			return persistence.Response{}, e
		}
		var poolID, grantID string
		if e = tx.QueryRow(ctx, "SELECT c.pool_id::text,rs.session_id::text FROM collection_wallets c JOIN resource_sessions rs ON rs.resource_id=c.pool_id WHERE c.account_id=$1", id).Scan(&poolID, &grantID); e != nil {
			return persistence.Response{}, e
		}
		pool, e := read(ctx, tx, poolID, "pool_wallet", true)
		if e != nil {
			return persistence.Response{}, e
		}
		pool["status"] = "revoked"
		if e = save(ctx, tx, poolID, pool); e != nil {
			return persistence.Response{}, e
		}
		if _, e = s.Work.Enqueue(ctx, tx, id, grantID, "cancel", Control{}, time.Now()); e != nil {
			return persistence.Response{}, e
		}
	}
	if e = tx.Commit(ctx); e != nil {
		return persistence.Response{}, e
	}
	return persistence.Response{Status: 204}, nil
}
