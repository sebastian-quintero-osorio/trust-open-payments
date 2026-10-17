package application

import (
	"context"
	"net/http/httptest"
	"net/url"
	"regexp"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestQRReceiptTracksOutcomeWithoutSendingAnotherPayment(t *testing.T) {
	for _, outcome := range []string{"confirmed", "insufficient_balance", "notification_pending"} {
		t.Run(outcome, func(t *testing.T) {
			a := newApp(t)
			a.authorizeAll(t)
			account := a.account(t)
			a.Remote.mu.Lock()
			a.Remote.failBalance = outcome == "insufficient_balance"
			if outcome == "notification_pending" {
				a.Remote.webhookFailures = 10
			}
			a.Remote.mu.Unlock()
			get := func(path string) *httptest.ResponseRecorder {
				w := httptest.NewRecorder()
				a.Handler.ServeHTTP(w, httptest.NewRequest("GET", path, nil))
				return w
			}
			page := get("/qr/" + account)
			token := regexp.MustCompile(`name="form_token" value="([^"]+)"`).FindStringSubmatch(page.Body.String())[1]
			receipt := "/qr/" + account + "?receipt=" + token
			form := url.Values{"form_token": {token}, "amount": {"1.23"}}
			req := httptest.NewRequest("POST", "/qr/"+account+"/pay", strings.NewReader(form.Encode()))
			req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
			w := httptest.NewRecorder()
			a.Handler.ServeHTTP(w, req)
			if w.Code != 200 || !strings.Contains(w.Body.String(), "Pago en proceso") || !strings.Contains(w.Body.String(), receipt) || !strings.Contains(w.Body.String(), `http-equiv="refresh"`) {
				t.Fatal("missing live receipt", w.Code, w.Body.String())
			}
			var payment string
			if e := a.App.DB.Pool.QueryRow(context.Background(), "SELECT id::text FROM resources WHERE kind='sim_payment'").Scan(&payment); e != nil {
				t.Fatal(e)
			}
			state := "completed"
			if outcome == "insufficient_balance" {
				state = "failed"
			}
			a.drive(t, "sim_payment", payment, state)
			// Status survives cache removal and disabling the payment channel.
			if _, e := a.App.DB.Pool.Exec(context.Background(), "DELETE FROM idempotency_records"); e != nil {
				t.Fatal(e)
			}
			a.App.Config.SimEnabled = false
			w = get(receipt)
			want := "Recaudo confirmado"
			if outcome == "insufficient_balance" {
				want = "no tiene saldo suficiente"
			}
			if outcome == "notification_pending" {
				want = "La confirmación en Trust sigue pendiente"
			}
			if w.Code != 200 || !strings.Contains(w.Body.String(), want) {
				t.Fatal(w.Code, w.Body.String())
			}
			refresh := strings.Contains(w.Body.String(), `http-equiv="refresh"`)
			if refresh != (outcome == "notification_pending") {
				t.Fatal("incorrect refresh", w.Body.String())
			}
			if outcome == "notification_pending" {
				if _, e := a.App.DB.Pool.Exec(context.Background(), "UPDATE webhook_outbox SET attempts=4,available_at=now() WHERE resource_id=$1", payment); e != nil {
					t.Fatal(e)
				}
				if e := a.App.DeliverWebhooks(context.Background()); e != nil {
					t.Fatal(e)
				}
				w = get(receipt)
				if !strings.Contains(w.Body.String(), "no se pudo entregar") || strings.Contains(w.Body.String(), `http-equiv="refresh"`) {
					t.Fatal(w.Body.String())
				}
			}
			for _, path := range []string{"/qr/" + account + "?receipt=invalid", "/qr/" + account + "?receipt=" + uuid.NewString(), "/qr/" + uuid.NewString() + "?receipt=" + token, "/qr/" + account + "?receipt=" + payment} {
				if w := get(path); w.Code != 404 {
					t.Fatal("receipt exposed", path, w.Code)
				}
			}
			var count int
			if e := a.App.DB.Pool.QueryRow(context.Background(), "SELECT count(*) FROM resources WHERE kind='sim_payment'").Scan(&count); e != nil || count != 1 {
				t.Fatal("lookup created payment", count, e)
			}
		})
	}
}
