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
	"math/big"
	"strconv"
	"time"
)

func (s *Service) createSim(ctx context.Context, tx pgx.Tx, r httpapi.Request) (persistence.Response, error) {
	var input domain.CreateSimQrPaymentRequest
	if e := json.Unmarshal(r.Body, &input); e != nil {
		return problem(400, "validation_error")
	}
	return s.startSim(ctx, tx, input)
}
func (s *Service) startSim(ctx context.Context, tx pgx.Tx, input domain.CreateSimQrPaymentRequest) (persistence.Response, error) {
	if !s.Config.SimEnabled {
		return problem(409, "sim_disabled")
	}
	account, _, receiver, e := s.accountGrant(ctx, tx, input.CollectionAccountID)
	if errors.Is(e, pgx.ErrNoRows) {
		return problem(404, "not_found")
	}
	if e != nil {
		return persistence.Response{}, e
	}
	if status(account) != "active" {
		return problem(409, "collection_account_not_active")
	}
	amount := domain.Amount(input.Amount)
	if amount.AssetCode != receiver.AssetCode || amount.AssetScale != receiver.AssetScale {
		return problem(422, "asset_mismatch")
	}
	max := domain.Amount{Value: s.Config.SimMaxAmount, AssetCode: receiver.AssetCode, AssetScale: receiver.AssetScale}
	cmp, e := amount.Compare(max)
	if e != nil || cmp > 0 || amount.Validate(true) != nil {
		return problem(422, "invalid_amount")
	}
	var payer, grantID string
	e = tx.QueryRow(ctx, "SELECT p.resource_id::text,rs.session_id::text FROM pool_wallets p JOIN resources r ON r.id=p.resource_id JOIN resource_sessions rs ON rs.resource_id=p.resource_id WHERE p.role='sim_payer' AND r.status='authorized' LIMIT 1").Scan(&payer, &grantID)
	if errors.Is(e, pgx.ErrNoRows) {
		return problem(409, "sim_payer_not_authorized")
	}
	if e != nil {
		return persistence.Response{}, e
	}
	source, e := s.poolWallet(ctx, tx, payer)
	if e != nil {
		return persistence.Response{}, e
	}
	account, e = read(ctx, tx, input.CollectionAccountID, "collection_account", true)
	if e != nil {
		return persistence.Response{}, e
	}
	if status(account) != "active" {
		return problem(409, "collection_account_not_active")
	}
	id := newID()
	reference := "BREB-SIM-" + id
	v := map[string]any{"id": id, "seq": nil, "collection_account_id": input.CollectionAccountID, "amount": amount, "payer_bank": input.PayerBank, "bre_b_reference": reference, "status": "processing", "incoming_payment_url": nil, "webhook_event": nil, "webhook_delivered_at": nil, "created_at": now()}
	if e = insert(ctx, tx, "sim_payment", v); e != nil {
		return persistence.Response{}, e
	}
	if _, e = tx.Exec(ctx, "INSERT INTO sim_entries(resource_id) VALUES($1)", id); e != nil {
		return persistence.Response{}, e
	}
	f := Flow{Kind: "sim_payment", Stage: "incoming", Source: source, Receiver: receiver, AccountID: input.CollectionAccountID, Amount: op.Wire(amount), UseQuote: true, ExternalRef: "sim:" + id, BreReference: reference}
	if _, e = s.Work.Enqueue(ctx, tx, id, grantID, "start", f, time.Now()); e != nil {
		return persistence.Response{}, e
	}
	return jsonResponse(201, v)
}
func (s *Service) listSim(ctx context.Context, _ pgx.Tx, r httpapi.Request) (persistence.Response, error) {
	after := int64(0)
	limit := 100
	var e error
	if q := r.HTTP.URL.Query().Get("after"); q != "" {
		after, e = strconv.ParseInt(q, 10, 64)
		if e != nil || after < 0 {
			return problem(400, "validation_error")
		}
	}
	if q := r.HTTP.URL.Query().Get("limit"); q != "" {
		limit, e = strconv.Atoi(q)
		if e != nil || limit < 1 || limit > 200 {
			return problem(400, "validation_error")
		}
	}
	rows, e := s.DB.Pool.Query(ctx, "SELECT r.payload,e.seq,e.webhook_body FROM sim_entries e JOIN resources r ON r.id=e.resource_id WHERE e.seq>$1 AND r.status IN ('completed','failed') ORDER BY e.seq LIMIT $2", after, limit)
	if e != nil {
		return persistence.Response{}, e
	}
	defer rows.Close()
	items := []json.RawMessage{}
	next := after
	for rows.Next() {
		var b, raw []byte
		if e = rows.Scan(&b, &next, &raw); e != nil {
			return persistence.Response{}, e
		}
		if len(raw) > 0 {
			var value map[string]any
			if e = json.Unmarshal(b, &value); e != nil {
				return persistence.Response{}, e
			}
			value["webhook_event"] = json.RawMessage(raw)
			b, e = json.Marshal(value)
			if e != nil {
				return persistence.Response{}, e
			}
		}
		items = append(items, json.RawMessage(b))
	}
	if e = rows.Err(); e != nil {
		return persistence.Response{}, e
	}
	return jsonResponse(200, map[string]any{"items": items, "next_after": next})
}
func (s *Service) assignSequence(ctx context.Context, tx pgx.Tx, id string, v map[string]any) error {
	// Serialize allocation until commit so a cursor cannot observe seq 2 before seq 1.
	if _, e := tx.Exec(ctx, "SELECT pg_advisory_xact_lock(714583922)"); e != nil {
		return e
	}
	var seq int64
	if e := tx.QueryRow(ctx, "UPDATE sim_entries SET seq=COALESCE(seq,nextval('sim_terminal_sequence')) WHERE resource_id=$1 RETURNING seq", id).Scan(&seq); e != nil {
		return e
	}
	v["seq"] = seq
	return save(ctx, tx, id, v)
}
func (s *Service) updateLedger(ctx context.Context, tx pgx.Tx, id string) error {
	v, e := read(ctx, tx, id, "collection_account", true)
	if e != nil {
		return e
	}
	var incoming, outgoing string
	e = tx.QueryRow(ctx, `SELECT COALESCE(SUM((payload#>>'{amount,value}')::numeric) FILTER(WHERE kind='sim_payment' AND status='completed'),0)::text,COALESCE(SUM((payload#>>'{debit_amount,value}')::numeric) FILTER(WHERE kind='transfer' AND status='completed'),0)::text FROM resources WHERE payload->>'collection_account_id'=$1`, id).Scan(&incoming, &outgoing)
	if e != nil {
		return e
	}
	a, ok := new(big.Int).SetString(incoming, 10)
	if !ok {
		return errors.New("invalid ledger input")
	}
	b, ok := new(big.Int).SetString(outgoing, 10)
	if !ok {
		return errors.New("invalid ledger output")
	}
	a.Sub(a, b)
	if a.Sign() < 0 {
		delete(v, "ledger_balance")
	} else {
		v["ledger_balance"] = map[string]any{"value": a.String(), "asset_code": v["asset_code"], "asset_scale": v["asset_scale"]}
	}
	return save(ctx, tx, id, v)
}
