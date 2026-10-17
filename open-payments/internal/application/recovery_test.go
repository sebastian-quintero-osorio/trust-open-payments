package application

import (
	"context"
	"errors"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/config"
)

func submittedTransfer(t *testing.T, a *testApp, account string, lost bool) string {
	t.Helper()
	a.Remote.mu.Lock()
	a.Remote.lostResponse = lost
	a.Remote.mu.Unlock()
	w, v := a.request(t, "POST", "/v1/transfers", map[string]any{"collection_account_id": account, "receiver_wallet_address": a.Remote.base + "/wallet/receiver", "debit_amount": amount("123"), "external_ref": "payout:" + uuid.NewString()}, uuid.NewString())
	if w.Code != http.StatusCreated {
		t.Fatal(w.Code, w.Body.String())
	}
	for range 20 {
		if _, err := a.App.RunOnce(context.Background(), uuid.NewString()); err != nil {
			t.Fatal(err)
		}
		a.Remote.mu.Lock()
		created := a.Remote.creates
		a.Remote.mu.Unlock()
		if created == 1 {
			return v["id"].(string)
		}
		time.Sleep(time.Millisecond)
	}
	t.Fatal("outgoing was not submitted")
	return ""
}

func TestSubmittedPaymentsRequireReconciliationAfterAuthorizationLoss(t *testing.T) {
	for _, lost := range []bool{false, true} {
		for _, mode := range []string{"revoke", "rotation_failure"} {
			t.Run(mode+map[bool]string{false: "/known", true: "/uncertain"}[lost], func(t *testing.T) {
				a := newApp(t)
				a.authorizeAll(t)
				account := a.account(t)
				id := submittedTransfer(t, a, account, lost)
				if mode == "revoke" {
					w, _ := a.request(t, "DELETE", "/v1/collection-accounts/"+account, nil, "")
					if w.Code != 204 {
						t.Fatal(w.Code)
					}
				} else {
					var sid string
					if err := a.App.DB.Pool.QueryRow(context.Background(), "SELECT grant_id::text FROM jobs WHERE resource_id=$1", id).Scan(&sid); err != nil {
						t.Fatal(err)
					}
					session, err := a.App.Work.LoadSession(context.Background(), a.App.DB.Pool, sid)
					if err != nil {
						t.Fatal(err)
					}
					session.TokenExpiresAt = time.Now().Add(-time.Hour)
					session.Grant.AccessToken.Manage = config.Secret(a.Remote.base + "/unavailable-management")
					if err = a.App.Work.SaveSession(context.Background(), a.App.DB.Pool, sid, session); err != nil {
						t.Fatal(err)
					}
				}
				reconciled := false
				for range 20 {
					_, err := a.App.RunOnce(context.Background(), uuid.NewString())
					if errors.Is(err, ErrReconciliationRequired) {
						reconciled = true
					} else if err != nil {
						t.Fatal(err)
					}
					time.Sleep(time.Millisecond)
				}
				_, v := a.request(t, "GET", "/v1/transfers/"+id, nil, "")
				if !reconciled || v["status"] != "processing" || v["retryable"] != false {
					t.Fatal("uncertain money became retryable", v)
				}
				var kind string
				if err := a.App.DB.Pool.QueryRow(context.Background(), "SELECT kind FROM jobs WHERE resource_id=$1", id).Scan(&kind); err != nil || kind != "poll" {
					t.Fatal("reconciliation blocks later start jobs", kind, err)
				}
				a.Remote.mu.Lock()
				defer a.Remote.mu.Unlock()
				if a.Remote.creates != 1 {
					t.Fatal("outgoing repeated")
				}
			})
		}
	}
}

func TestFinancialReplaySurvivesResponseCacheCleanup(t *testing.T) {
	a := newApp(t)
	a.authorizeAll(t)
	account, key := a.account(t), uuid.NewString()
	body := map[string]any{"collection_account_id": account, "receiver_wallet_address": a.Remote.base + "/wallet/receiver", "debit_amount": amount("123"), "external_ref": "payout:durable"}
	w, first := a.request(t, "POST", "/v1/transfers", body, key)
	if w.Code != 201 {
		t.Fatal(w.Code)
	}
	a.drive(t, "transfer", first["id"].(string), "completed")
	if _, err := a.App.DB.Pool.Exec(context.Background(), "UPDATE idempotency_records SET expires_at=now()-interval '1 second' WHERE key=$1", key); err != nil {
		t.Fatal(err)
	}
	if err := a.App.DB.Cleanup(context.Background()); err != nil {
		t.Fatal(err)
	}
	w, second := a.request(t, "POST", "/v1/transfers", body, key)
	if w.Code != 201 || w.Header().Get("Idempotent-Replayed") != "true" || second["id"] != first["id"] {
		t.Fatal("financial receipt lost", w.Code, second)
	}
	body["debit_amount"] = amount("124")
	w, _ = a.request(t, "POST", "/v1/transfers", body, key)
	if w.Code != 409 {
		t.Fatal("old financial key accepted another intent", w.Code)
	}
	a.Remote.mu.Lock()
	defer a.Remote.mu.Unlock()
	if a.Remote.creates != 1 {
		t.Fatal("cache expiry duplicated money")
	}
}

func TestDisabledQRExplainsAvailabilityWithoutCreatingPayment(t *testing.T) {
	a := newApp(t)
	a.authorizeAll(t)
	account := a.account(t)
	a.App.Config.SimEnabled = false
	w, _ := a.request(t, "GET", "/qr/"+account, nil, "")
	if w.Code != 503 || !strings.Contains(w.Body.String(), "Pagos por QR no disponibles") || strings.Contains(w.Body.String(), "<form") {
		t.Fatal("inactive payment channel looks payable or missing", w.Code, w.Body.String())
	}
	var forms int
	if err := a.App.DB.Pool.QueryRow(context.Background(), "SELECT count(*) FROM qr_forms WHERE account_id=$1", account).Scan(&forms); err != nil || forms != 0 {
		t.Fatal("disabled QR created a payable form", forms, err)
	}
	a.Remote.mu.Lock()
	defer a.Remote.mu.Unlock()
	if a.Remote.creates != 0 {
		t.Fatal("disabled QR moved funds")
	}
}

func TestPendingOutgoingIsNotFailedBecauseIncomingExpired(t *testing.T) {
	a := newApp(t)
	a.authorizeAll(t)
	id := submittedTransfer(t, a, a.account(t), false)
	a.Remote.mu.Lock()
	for key, outgoing := range a.Remote.outgoing {
		outgoing.SentAmount.Value = "0"
		a.Remote.outgoing[key] = outgoing
	}
	for key, incoming := range a.Remote.incoming {
		incoming.ReceivedAmount.Value = "0"
		incoming.ExpiresAt = time.Now().Add(-time.Hour)
		a.Remote.incoming[key] = incoming
	}
	a.Remote.mu.Unlock()
	time.Sleep(2 * time.Millisecond)
	_, err := a.App.RunOnce(context.Background(), uuid.NewString())
	if !errors.Is(err, ErrReconciliationRequired) {
		t.Fatal("expired incoming with pending outgoing was not reconciled", err)
	}
	_, v := a.request(t, "GET", "/v1/transfers/"+id, nil, "")
	if v["status"] != "processing" || v["retryable"] != false {
		t.Fatal(v)
	}
}
