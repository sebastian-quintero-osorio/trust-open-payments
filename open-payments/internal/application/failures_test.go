package application

import (
	"bytes"
	"context"
	"encoding/json"
	"github.com/google/uuid"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"testing"
	"time"
)

func TestWebhookRetriesKeepBytesAndReconciliationEvent(t *testing.T) {
	a := newApp(t)
	a.authorizeAll(t)
	account := a.account(t)
	a.Remote.mu.Lock()
	a.Remote.webhookFailures = 2
	a.Remote.mu.Unlock()
	w, v := a.request(t, "POST", "/v1/sim/puntored/qr-payments", map[string]any{"collection_account_id": account, "amount": amount("123")}, uuid.NewString())
	if w.Code != 201 {
		t.Fatal(w.Code)
	}
	id := v["id"].(string)
	a.drive(t, "sim_payment", id, "completed")
	for range 2 {
		if _, e := a.App.DB.Pool.Exec(context.Background(), "UPDATE webhook_outbox SET available_at=now() WHERE resource_id=$1", id); e != nil {
			t.Fatal(e)
		}
		if e := a.App.DeliverWebhooks(context.Background()); e != nil {
			t.Fatal(e)
		}
	}
	a.Remote.mu.Lock()
	defer a.Remote.mu.Unlock()
	if len(a.Remote.webhooks) != 3 {
		t.Fatal("retry schedule not applied", len(a.Remote.webhooks))
	}
	for _, raw := range a.Remote.webhooks {
		if !bytes.Equal(raw, a.Remote.webhooks[0]) {
			t.Fatal("retry body changed")
		}
	}
	w, list := a.request(t, "GET", "/v1/sim/puntored/qr-payments?after=0", nil, "")
	if w.Code != 200 {
		t.Fatal(w.Code)
	}
	var wire struct {
		Items []struct {
			Event json.RawMessage `json:"webhook_event"`
		} `json:"items"`
	}
	if e := json.Unmarshal(w.Body.Bytes(), &wire); e != nil || len(wire.Items) != 1 || !bytes.Equal(wire.Items[0].Event, a.Remote.webhooks[0]) {
		t.Fatal("reconciliation did not preserve raw webhook bytes", e)
	}
	if list["items"].([]any)[0].(map[string]any)["webhook_delivered_at"] == nil {
		t.Fatal("delivery not recorded")
	}
}
func TestConsentExpiryAndRejectionCannotExecutePayment(t *testing.T) {
	for _, mode := range []string{"expiry", "rejection"} {
		t.Run(mode, func(t *testing.T) {
			a := newApp(t)
			w, v := a.request(t, "POST", "/v1/payments", map[string]any{"sender_wallet_address": a.Remote.base + "/wallet/lender", "receiver_wallet_address": a.Remote.base + "/wallet/merchant", "receive_amount": amount("1000"), "external_ref": "loan:test", "return_url": "https://app.example/return?loan_id=kept"}, uuid.NewString())
			if w.Code != 201 {
				t.Fatal(w.Code, w.Body.String())
			}
			id := v["id"].(string)
			wanted := "rejected"
			if mode == "expiry" {
				wanted = "expired"
				if _, e := a.App.DB.Pool.Exec(context.Background(), "UPDATE grant_attempts SET expires_at=now()-interval '31 seconds' WHERE resource_id=$1", id); e != nil {
					t.Fatal(e)
				}
				if e := a.App.ExpireConsents(context.Background()); e != nil {
					t.Fatal(e)
				}
			} else {
				var attempt string
				if e := a.App.DB.Pool.QueryRow(context.Background(), "SELECT id::text FROM grant_attempts WHERE resource_id=$1", id).Scan(&attempt); e != nil {
					t.Fatal(e)
				}
				w, _ = a.request(t, "GET", "/v1/interact/finish/"+attempt+"?result=grant_rejected", nil, "")
				if w.Code != http.StatusFound {
					t.Fatal("rejection callback failed", w.Code, w.Body.String())
				}
				location, e := w.Result().Location()
				if e != nil || location.Query().Get("loan_id") != "kept" || location.Query().Get("op_id") != id || location.Query().Get("op_resource") != "payment" {
					t.Fatal("return URL query was lost", e)
				}
			}
			a.drive(t, "payment", id, wanted)
			for range 3 {
				if _, e := a.App.RunOnce(context.Background(), uuid.NewString()); e != nil {
					t.Fatal(e)
				}
			}
			a.Remote.mu.Lock()
			defer a.Remote.mu.Unlock()
			if a.Remote.creates != 0 {
				t.Fatal("terminal consent sent funds")
			}
		})
	}
}
func TestRestartAfterUncertainCreateDoesNotResend(t *testing.T) {
	a := newApp(t)
	a.authorizeAll(t)
	account := a.account(t)
	a.Remote.mu.Lock()
	a.Remote.lostResponse = true
	a.Remote.mu.Unlock()
	w, v := a.request(t, "POST", "/v1/transfers", map[string]any{"collection_account_id": account, "receiver_wallet_address": a.Remote.base + "/wallet/receiver", "debit_amount": amount("321"), "external_ref": "payout:restart"}, uuid.NewString())
	if w.Code != 201 {
		t.Fatal(w.Code)
	}
	for range 2 {
		if _, e := a.App.RunOnce(context.Background(), uuid.NewString()); e != nil {
			t.Fatal(e)
		}
	}
	replacement, e := New(a.cfg, a.App.DB, a.App.OP)
	if e != nil {
		t.Fatal(e)
	}
	replacement.WebhookHTTP = a.App.WebhookHTTP
	a.App = replacement
	a.App.Bind(a.API)
	a.Handler = a.API.Handler()
	a.drive(t, "transfer", v["id"].(string), "completed")
	a.Remote.mu.Lock()
	defer a.Remote.mu.Unlock()
	if a.Remote.creates != 1 {
		t.Fatal("restart duplicated outgoing")
	}
}
func TestRevocationStopsQueuedTransfers(t *testing.T) {
	a := newApp(t)
	a.authorizeAll(t)
	account := a.account(t)
	w, v := a.request(t, "POST", "/v1/transfers", map[string]any{"collection_account_id": account, "receiver_wallet_address": a.Remote.base + "/wallet/receiver", "debit_amount": amount("123"), "external_ref": "payout:revoked"}, uuid.NewString())
	if w.Code != 201 {
		t.Fatal(w.Code)
	}
	w, _ = a.request(t, "DELETE", "/v1/collection-accounts/"+account, nil, "")
	if w.Code != 204 {
		t.Fatal(w.Code)
	}
	a.drive(t, "transfer", v["id"].(string), "failed")
	_, v = a.request(t, "GET", "/v1/transfers/"+v["id"].(string), nil, "")
	if v["failure_code"] != "grant_revoked" || v["retryable"] != false {
		t.Fatal("revocation not terminal", v)
	}
	a.Remote.mu.Lock()
	defer a.Remote.mu.Unlock()
	if a.Remote.creates != 0 {
		t.Fatal("queued transfer executed after revocation")
	}
}
func TestTrustedProxyCannotBeSpoofedByDirectClients(t *testing.T) {
	s := Service{}
	s.Config.TrustedProxyCIDRs = []netip.Prefix{netip.MustParsePrefix("172.30.0.2/32")}
	r := httptest.NewRequest("GET", "/", nil)
	r.RemoteAddr = "203.0.113.5:123"
	r.Header.Set("X-Forwarded-For", "8.8.8.8")
	if s.clientIP(r) != "203.0.113.5" {
		t.Fatal("direct client spoofed forwarded IP")
	}
	r.RemoteAddr = "172.30.0.2:123"
	if s.clientIP(r) != "8.8.8.8" {
		t.Fatal("trusted proxy IP ignored")
	}
	r.Header.Set("X-Forwarded-For", "8.8.8.8, 1.1.1.1")
	if s.clientIP(r) != "172.30.0.2" {
		t.Fatal("ambiguous forwarding chain accepted")
	}
}
func TestSequenceAllocationCannotCommitOutOfOrder(t *testing.T) {
	a := newApp(t)
	a.authorizeAll(t)
	account := a.account(t)
	ids := []string{}
	for range 2 {
		w, v := a.request(t, "POST", "/v1/sim/puntored/qr-payments", map[string]any{"collection_account_id": account, "amount": amount("1")}, uuid.NewString())
		if w.Code != 201 {
			t.Fatal(w.Code)
		}
		ids = append(ids, v["id"].(string))
	}
	ctx := context.Background()
	first, e := a.App.DB.Pool.Begin(ctx)
	if e != nil {
		t.Fatal(e)
	}
	defer func() { _ = first.Rollback(ctx) }()
	v, e := read(ctx, first, ids[0], "sim_payment", true)
	if e != nil {
		t.Fatal(e)
	}
	v["status"] = "failed"
	if e = a.App.assignSequence(ctx, first, ids[0], v); e != nil {
		t.Fatal(e)
	}
	started := make(chan struct{})
	done := make(chan error, 1)
	go func() {
		second, e := a.App.DB.Pool.Begin(ctx)
		if e != nil {
			done <- e
			return
		}
		defer func() { _ = second.Rollback(ctx) }()
		v, e := read(ctx, second, ids[1], "sim_payment", true)
		if e != nil {
			done <- e
			return
		}
		v["status"] = "failed"
		close(started)
		if e = a.App.assignSequence(ctx, second, ids[1], v); e == nil {
			e = second.Commit(ctx)
		}
		done <- e
	}()
	<-started
	select {
	case e := <-done:
		t.Fatal("later sequence committed before earlier transaction", e)
	case <-time.After(30 * time.Millisecond):
	}
	if e = first.Commit(ctx); e != nil {
		t.Fatal(e)
	}
	if e = <-done; e != nil {
		t.Fatal(e)
	}
	_, list := a.request(t, "GET", "/v1/sim/puntored/qr-payments?after=0", nil, "")
	if len(list["items"].([]any)) != 2 {
		t.Fatal("reconciliation cursor lost terminal payment")
	}
}
func TestBadCallbackHashDoesNotConsumeAttempt(t *testing.T) {
	a := newApp(t)
	_, pools := a.request(t, "GET", "/v1/admin/pool-wallets", nil, "")
	id := pools["items"].([]any)[0].(map[string]any)["id"].(string)
	w, _ := a.request(t, "POST", "/v1/admin/pool-wallets/"+id+"/authorize", map[string]any{}, uuid.NewString())
	if w.Code != 201 {
		t.Fatal(w.Code)
	}
	var attempt string
	if e := a.App.DB.Pool.QueryRow(context.Background(), "SELECT id::text FROM grant_attempts WHERE resource_id=$1", id).Scan(&attempt); e != nil {
		t.Fatal(e)
	}
	w, _ = a.request(t, "GET", "/v1/interact/finish/"+attempt+"?interact_ref=ref&hash=bad", nil, "")
	if w.Code != 400 {
		t.Fatal("bad hash accepted")
	}
	a.approve(t, id)
	a.drive(t, "pool_wallet", id, "authorized")
}

func TestExpiredQuoteCannotExceedApprovedDebit(t *testing.T) {
	a := newApp(t)
	w, v := a.request(t, "POST", "/v1/payments", map[string]any{"sender_wallet_address": a.Remote.base + "/wallet/lender", "receiver_wallet_address": a.Remote.base + "/wallet/merchant", "receive_amount": amount("1000"), "external_ref": "loan:requote", "return_url": "https://app.example/return"}, uuid.NewString())
	if w.Code != 201 {
		t.Fatal(w.Code, w.Body.String())
	}
	id := v["id"].(string)
	ctx := context.Background()
	flow, e := a.App.loadFlow(ctx, a.App.DB.Pool, id)
	if e != nil {
		t.Fatal(e)
	}
	flow.Quote.ExpiresAt = time.Now().Add(-time.Second)
	if e = a.App.saveFlow(ctx, a.App.DB.Pool, id, flow); e != nil {
		t.Fatal(e)
	}
	a.Remote.mu.Lock()
	a.Remote.extraDebit = true
	a.Remote.mu.Unlock()
	a.approve(t, id)
	a.drive(t, "payment", id, "failed")
	_, v = a.request(t, "GET", "/v1/payments/"+id, nil, "")
	if v["failure_code"] != "quote_expired" {
		t.Fatal("approved limit exceeded", v)
	}
	a.Remote.mu.Lock()
	defer a.Remote.mu.Unlock()
	if a.Remote.creates != 0 {
		t.Fatal("sent funds above consent limit")
	}
}
func TestFixedSendFallsBackToQuoteOnlyAfterExplicitRejection(t *testing.T) {
	a := newApp(t)
	a.authorizeAll(t)
	account := a.account(t)
	a.Remote.mu.Lock()
	a.Remote.rejectDirect = true
	a.Remote.mu.Unlock()
	w, v := a.request(t, "POST", "/v1/transfers", map[string]any{"collection_account_id": account, "receiver_wallet_address": a.Remote.base + "/wallet/receiver", "debit_amount": amount("123"), "external_ref": "payout:quoted"}, uuid.NewString())
	if w.Code != 201 {
		t.Fatal(w.Code)
	}
	a.drive(t, "transfer", v["id"].(string), "completed")
	a.Remote.mu.Lock()
	defer a.Remote.mu.Unlock()
	if a.Remote.creates != 1 || a.Remote.completes != 1 {
		t.Fatal("fixed-send fallback duplicated money")
	}
}

func TestRevokedCollectionWalletCanBeReauthorizedAndAssignedAgain(t *testing.T) {
	a := newApp(t)
	a.authorizeAll(t)
	old := a.account(t)
	_ = a.account(t)
	var poolID string
	if e := a.App.DB.Pool.QueryRow(context.Background(), "SELECT pool_id::text FROM collection_wallets WHERE account_id=$1", old).Scan(&poolID); e != nil {
		t.Fatal(e)
	}
	w, _ := a.request(t, "DELETE", "/v1/collection-accounts/"+old, nil, "")
	if w.Code != 204 {
		t.Fatal(w.Code)
	}
	w, _ = a.request(t, "POST", "/v1/admin/pool-wallets/"+poolID+"/authorize", map[string]any{}, uuid.NewString())
	if w.Code != 201 {
		t.Fatal(w.Code, w.Body.String())
	}
	a.approve(t, poolID)
	a.drive(t, "pool_wallet", poolID, "authorized")
	replacement := a.account(t)
	if replacement == old {
		t.Fatal("revoked account ID reused")
	}
	var nextPool string
	if e := a.App.DB.Pool.QueryRow(context.Background(), "SELECT pool_id::text FROM collection_wallets WHERE account_id=$1 AND active", replacement).Scan(&nextPool); e != nil || nextPool != poolID {
		t.Fatal("reauthorized wallet not reusable", e)
	}
	_, v := a.request(t, "GET", "/v1/collection-accounts/"+old, nil, "")
	if v["status"] != "revoked" {
		t.Fatal("historical account reactivated")
	}
}
