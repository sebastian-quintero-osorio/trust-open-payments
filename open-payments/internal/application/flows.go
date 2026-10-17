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

// Flow is encrypted execution state. Stage is persisted before creating an outgoing.
type Flow struct {
	Kind, Stage, AccountID, ExternalRef, Description, BreReference string
	Source, Receiver                                               op.Wallet
	IncomingGrant                                                  op.Grant
	IncomingTokenExpires                                           time.Time
	Incoming                                                       op.Incoming
	Quote                                                          op.Quote
	Outgoing                                                       op.Outgoing
	Amount                                                         op.Amount
	DebitLimit                                                     op.Amount
	FixedSend, UseQuote                                            bool
}

// Control is the encrypted body of a callback or cancellation job.
type Control struct {
	Ref     string
	Calling bool
}

func (s *Service) saveFlow(ctx context.Context, db workflow.DB, id string, f Flow) error {
	b, e := s.Work.Vault.Seal("flow:"+id, f)
	if e != nil {
		return e
	}
	_, e = db.Exec(ctx, "INSERT INTO resource_flows(resource_id,data_cipher) VALUES($1,$2) ON CONFLICT(resource_id) DO UPDATE SET data_cipher=EXCLUDED.data_cipher", id, b)
	return e
}
func (s *Service) loadFlow(ctx context.Context, db workflow.DB, id string) (Flow, error) {
	var b []byte
	if e := db.QueryRow(ctx, "SELECT data_cipher FROM resource_flows WHERE resource_id=$1", id).Scan(&b); e != nil {
		return Flow{}, e
	}
	var f Flow
	e := s.Work.Vault.Open("flow:"+id, b, &f)
	return f, e
}
func (s *Service) createPayment(ctx context.Context, tx pgx.Tx, r httpapi.Request) (persistence.Response, error) {
	var input domain.CreatePaymentRequest
	if e := json.Unmarshal(r.Body, &input); e != nil {
		return problem(400, "validation_error")
	}
	if !s.Config.ReturnURLAllowed(input.ReturnURL) {
		return problem(422, "return_url_not_allowed")
	}
	receiver, e := s.OP.Resolve(ctx, input.ReceiverWalletAddress)
	if e != nil {
		return upstream(e)
	}
	amount := domain.Amount(input.ReceiveAmount)
	if amount.AssetCode != receiver.AssetCode || amount.AssetScale != receiver.AssetScale {
		return problem(422, "asset_mismatch")
	}
	sender, e := s.OP.Resolve(ctx, input.SenderWalletAddress)
	if e != nil {
		return upstream(e)
	}
	id := newID()
	f := Flow{Kind: "payment", Stage: "outgoing", Source: sender, Receiver: receiver, Amount: op.Wire(amount), ExternalRef: input.ExternalRef, UseQuote: true}
	if input.Description != nil {
		f.Description = *input.Description
	}
	if e = s.prepareIncoming(ctx, &f, time.Hour); e != nil {
		return upstream(e)
	}
	if e = s.prepareQuote(ctx, &f); e != nil {
		return upstream(e)
	}
	f.DebitLimit = f.Quote.DebitAmount
	v := map[string]any{"id": id, "external_ref": input.ExternalRef, "sender_wallet_address": sender.ID, "receiver_wallet_address": receiver.ID, "receive_amount": amount, "debit_amount": f.Quote.DebitAmount.Domain(), "sent_amount": nil, "status": "awaiting_authorization", "authorization_url": nil, "authorization_expires_at": nil, "failure_code": nil, "incoming_payment_url": f.Incoming.ID, "outgoing_payment_url": nil, "created_at": now(), "updated_at": now()}
	if e = insert(ctx, tx, "payment", v); e != nil {
		return persistence.Response{}, e
	}
	consent, e := s.beginConsent(ctx, tx, id, sender, input.ReturnURL, &f.DebitLimit, f.Incoming.ID, f)
	if e != nil {
		return upstream(e)
	}
	v["authorization_url"] = consent.URL
	v["authorization_expires_at"] = consent.Expires.UTC().Format(time.RFC3339Nano)
	if e = save(ctx, tx, id, v); e != nil {
		return persistence.Response{}, e
	}
	return jsonResponse(201, v)
}
func (s *Service) prepareIncoming(ctx context.Context, f *Flow, ttl time.Duration) error {
	actions := []string{"create", "read"}
	if f.FixedSend {
		actions = append(actions, "complete")
	}
	g, e := s.OP.NonInteractiveGrant(ctx, f.Receiver, "incoming-payment", actions)
	if e != nil {
		return e
	}
	f.IncomingGrant = g
	f.IncomingTokenExpires = time.Now().Add(time.Duration(g.AccessToken.ExpiresIn) * time.Second)
	var amount *op.Amount
	if !f.FixedSend {
		amount = &f.Amount
	}
	f.Incoming, e = s.OP.CreateIncoming(ctx, f.Receiver, g.AccessToken.Value, amount, time.Now().Add(ttl), map[string]any{"external_ref": f.ExternalRef, "bre_b_reference": f.BreReference})
	return e
}
func (s *Service) prepareQuote(ctx context.Context, f *Flow) error {
	g, e := s.OP.NonInteractiveGrant(ctx, f.Source, "quote", []string{"create", "read"})
	if e != nil {
		return e
	}
	var debit *op.Amount
	if f.FixedSend {
		debit = &f.Amount
	}
	f.Quote, e = s.OP.CreateQuote(ctx, f.Source, g.AccessToken.Value, f.Incoming.ID, debit)
	if e != nil {
		return e
	}
	if f.Quote.DebitAmount.AssetCode != f.Source.AssetCode || f.Quote.DebitAmount.AssetScale != f.Source.AssetScale {
		return &op.Error{Status: 422, Code: "asset_mismatch"}
	}
	if !f.FixedSend {
		cmp, e := f.Quote.ReceiveAmount.Domain().Compare(f.Amount.Domain())
		if e != nil || cmp != 0 {
			return &op.Error{Status: 502, Code: "upstream_error"}
		}
	}
	return nil
}
func (s *Service) accountGrant(ctx context.Context, tx pgx.Tx, accountID string) (map[string]any, string, op.Wallet, error) {
	account, e := read(ctx, tx, accountID, "collection_account", false)
	if e != nil {
		return nil, "", op.Wallet{}, e
	}
	var poolID, grantID string
	e = tx.QueryRow(ctx, "SELECT c.pool_id::text,rs.session_id::text FROM collection_wallets c JOIN resource_sessions rs ON rs.resource_id=c.pool_id WHERE c.account_id=$1", accountID).Scan(&poolID, &grantID)
	if e != nil {
		return nil, "", op.Wallet{}, e
	}
	w, e := s.poolWallet(ctx, tx, poolID)
	return account, grantID, w, e
}
func (s *Service) createTransfer(ctx context.Context, tx pgx.Tx, r httpapi.Request) (persistence.Response, error) {
	var input domain.CreateTransferRequest
	if e := json.Unmarshal(r.Body, &input); e != nil {
		return problem(400, "validation_error")
	}
	account, grantID, source, e := s.accountGrant(ctx, tx, input.CollectionAccountID)
	if errors.Is(e, pgx.ErrNoRows) {
		return problem(404, "not_found")
	}
	if e != nil {
		return persistence.Response{}, e
	}
	if status(account) != "active" {
		return problem(409, "collection_account_not_active")
	}
	amount := domain.Amount(input.DebitAmount)
	if amount.AssetCode != source.AssetCode || amount.AssetScale != source.AssetScale {
		return problem(422, "asset_mismatch")
	}
	lookup, cancel := context.WithTimeout(ctx, 1500*time.Millisecond)
	defer cancel()
	receiver, e := s.OP.Resolve(lookup, input.ReceiverWalletAddress)
	if e != nil {
		return upstream(e)
	}
	account, e = read(ctx, tx, input.CollectionAccountID, "collection_account", true)
	if e != nil {
		return persistence.Response{}, e
	}
	if status(account) != "active" {
		return problem(409, "collection_account_not_active")
	}
	id := newID()
	f := Flow{Kind: "transfer", Stage: "incoming", Source: source, Receiver: receiver, Amount: op.Wire(amount), FixedSend: true, AccountID: input.CollectionAccountID, ExternalRef: input.ExternalRef}
	if input.Description != nil {
		f.Description = *input.Description
	}
	v := map[string]any{"id": id, "external_ref": input.ExternalRef, "collection_account_id": input.CollectionAccountID, "receiver_wallet_address": receiver.ID, "description": input.Description, "debit_amount": amount, "sent_amount": nil, "receive_amount": nil, "status": "processing", "failure_code": nil, "retryable": false, "incoming_payment_url": nil, "outgoing_payment_url": nil, "created_at": now(), "completed_at": nil}
	if e = insert(ctx, tx, "transfer", v); e != nil {
		return persistence.Response{}, e
	}
	if _, e = s.Work.Enqueue(ctx, tx, id, grantID, "start", f, time.Now()); e != nil {
		return persistence.Response{}, e
	}
	return jsonResponse(201, v)
}
