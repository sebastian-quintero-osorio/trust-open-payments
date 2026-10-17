// Package application implements Trust's financial orchestration and API adapters.
package application

import (
	"context"
	"encoding/json"
	"errors"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/config"
	"github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/httpapi"
	op "github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/openpayments"
	"github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/persistence"
	"github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/workflow"
	"net/http"
	"time"
)

// Service composes HTTP operations with durable workflows and the real OP client.
type Service struct {
	Config      config.Config
	DB          *persistence.Store
	Work        *workflow.Store
	OP          *op.Client
	WebhookHTTP *http.Client
}

// New constructs production dependencies; the provided OP client permits HTTPS
// protocol fixtures without changing the production network policy.
func New(c config.Config, db *persistence.Store, client *op.Client) (*Service, error) {
	v, e := workflow.NewVault(c.PrivateKey)
	if e != nil {
		return nil, e
	}
	if client == nil {
		client, e = op.New(c, nil)
		if e != nil {
			return nil, e
		}
	}
	var existingID string
	var cipher []byte
	err := db.Pool.QueryRow(context.Background(), "SELECT id::text,data_cipher FROM grant_sessions LIMIT 1").Scan(&existingID, &cipher)
	if err == nil {
		var state workflow.Session
		if e = v.Open("grant:"+existingID, cipher, &state); e != nil {
			return nil, errors.New("stored grants cannot be decrypted with configured key")
		}
	} else if !errors.Is(err, pgx.ErrNoRows) {
		return nil, err
	}
	return &Service{Config: c, DB: db, Work: &workflow.Store{DB: db, Vault: v}, OP: client, WebhookHTTP: op.SafeHTTPClient()}, nil
}

// Bind registers every contract operation before the HTTP server starts.
func (s *Service) Bind(api *httpapi.Server) {
	for name, handler := range map[string]httpapi.Operation{"resolveWalletAddress": s.resolve, "listPoolWallets": s.listPool, "authorizePoolWallet": s.authorizePool, "createCollectionAccount": s.createAccount, "listCollectionAccounts": s.listAccounts, "getCollectionAccount": s.getAccount, "revokeCollectionAccount": s.revokeAccount, "createPayment": s.createPayment, "getPayment": s.getPayment, "createTransfer": s.createTransfer, "getTransfer": s.getTransfer, "createSimQrPayment": s.createSim, "listSimQrPayments": s.listSim} {
		api.Operations[name] = handler
	}
	api.BrowserOperations = map[string]http.HandlerFunc{"interactFinish": s.callback, "qrPage": s.qrPage, "qrPagePay": s.qrPay}
}
func jsonResponse(status int, value any) (persistence.Response, error) {
	b, e := json.Marshal(value)
	return persistence.Response{Status: status, Body: b, Headers: http.Header{"Content-Type": []string{"application/json"}}}, e
}
func problem(status int, code string) (persistence.Response, error) {
	return httpapi.ErrorResponse(status, code, code, status >= 500 || code == "request_in_progress"), nil
}
func upstream(e error) (persistence.Response, error) {
	var remote *op.Error
	if errors.As(e, &remote) {
		switch remote.Code {
		case "invalid_url", "wallet_not_found":
			return problem(422, remote.Code)
		}
	}
	return problem(502, "upstream_error")
}
func read(ctx context.Context, db workflow.DB, id, kind string, lock bool) (map[string]any, error) {
	query := "SELECT payload FROM resources WHERE id=$1 AND kind=$2"
	if lock {
		query += " FOR UPDATE"
	}
	var b []byte
	if e := db.QueryRow(ctx, query, id, kind).Scan(&b); e != nil {
		return nil, e
	}
	var out map[string]any
	e := json.Unmarshal(b, &out)
	return out, e
}
func save(ctx context.Context, db workflow.DB, id string, value map[string]any) error {
	b, e := json.Marshal(value)
	if e != nil {
		return e
	}
	_, e = db.Exec(ctx, "UPDATE resources SET status=$1,payload=$2,updated_at=now() WHERE id=$3", value["status"], b, id)
	return e
}
func insert(ctx context.Context, tx pgx.Tx, kind string, value map[string]any) error {
	b, e := json.Marshal(value)
	if e != nil {
		return e
	}
	ref, _ := value["external_ref"].(string)
	return persistence.InsertResource(ctx, tx, persistence.Resource{ID: value["id"].(string), Kind: kind, ExternalRef: ref, Status: value["status"].(string), Payload: b})
}
func now() string                        { return time.Now().UTC().Format(time.RFC3339Nano) }
func status(value map[string]any) string { s, _ := value["status"].(string); return s }
func newID() string                      { return uuid.NewString() }
func (s *Service) resolve(ctx context.Context, _ pgx.Tx, r httpapi.Request) (persistence.Response, error) {
	w, e := s.OP.Resolve(ctx, r.HTTP.URL.Query().Get("url"))
	if e != nil {
		return upstream(e)
	}
	return jsonResponse(200, map[string]any{"url": w.ID, "public_name": w.PublicName, "asset_code": w.AssetCode, "asset_scale": w.AssetScale})
}
func (s *Service) get(ctx context.Context, kind, id string) (persistence.Response, error) {
	v, e := read(ctx, s.DB.Pool, id, kind, false)
	if errors.Is(e, pgx.ErrNoRows) {
		return problem(404, "not_found")
	}
	if e != nil {
		return persistence.Response{}, e
	}
	return jsonResponse(200, v)
}
func (s *Service) getPayment(ctx context.Context, _ pgx.Tx, r httpapi.Request) (persistence.Response, error) {
	return s.get(ctx, "payment", r.HTTP.PathValue("payment_id"))
}
func (s *Service) getTransfer(ctx context.Context, _ pgx.Tx, r httpapi.Request) (persistence.Response, error) {
	return s.get(ctx, "transfer", r.HTTP.PathValue("transfer_id"))
}
func (s *Service) getAccount(ctx context.Context, _ pgx.Tx, r httpapi.Request) (persistence.Response, error) {
	return s.get(ctx, "collection_account", r.HTTP.PathValue("collection_account_id"))
}
func (s *Service) list(ctx context.Context, kind, ref string) (persistence.Response, error) {
	rows, e := s.DB.Pool.Query(ctx, "SELECT payload FROM resources WHERE kind=$1 AND ($2='' OR external_ref=$2) ORDER BY created_at DESC,id", kind, ref)
	if e != nil {
		return persistence.Response{}, e
	}
	defer rows.Close()
	items := []json.RawMessage{}
	for rows.Next() {
		var b []byte
		if e = rows.Scan(&b); e != nil {
			return persistence.Response{}, e
		}
		items = append(items, json.RawMessage(b))
	}
	if e = rows.Err(); e != nil {
		return persistence.Response{}, e
	}
	return jsonResponse(200, map[string]any{"items": items})
}
func (s *Service) listPool(ctx context.Context, _ pgx.Tx, _ httpapi.Request) (persistence.Response, error) {
	return s.list(ctx, "pool_wallet", "")
}
func (s *Service) listAccounts(ctx context.Context, _ pgx.Tx, r httpapi.Request) (persistence.Response, error) {
	return s.list(ctx, "collection_account", r.HTTP.URL.Query().Get("external_ref"))
}
