package application

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"github.com/google/uuid"
	"github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/config"
	"github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/httpapi"
	op "github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/openpayments"
	"github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/persistence"
	"github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/testutil"
	"io"
	"math/big"
	"net/http"
	"net/http/httptest"
	"net/url"
	"regexp"
	"strings"
	"sync"
	"testing"
	"time"
)

type quoteFixture struct {
	Quote      op.Quote
	IncomingID string
}
type protocolFixture struct {
	mu                            sync.Mutex
	base                          string
	incoming                      map[string]op.Incoming
	quotes                        map[string]quoteFixture
	outgoing                      map[string]op.Outgoing
	creates, continues, completes int
	extraDebit, rejectDirect      bool
	failBalance, lostResponse     bool
	webhookFailures               int
	webhooks                      [][]byte
	signatures                    []string
}

func (f *protocolFixture) serve(w http.ResponseWriter, r *http.Request) {
	f.mu.Lock()
	defer f.mu.Unlock()
	w.Header().Set("Content-Type", "application/json")
	path := r.URL.Path
	if strings.HasPrefix(path, "/wallet/") {
		name := strings.TrimPrefix(path, "/wallet/")
		writeFixture(w, map[string]any{"id": f.base + path, "publicName": name, "assetCode": "USD", "assetScale": 2, "authServer": f.base + "/tenant/auth/" + name, "resourceServer": f.base + "/tenant/rs/" + name})
		return
	}
	if path == "/webhook" {
		b, e := io.ReadAll(r.Body)
		if e != nil {
			w.WriteHeader(500)
			return
		}
		f.webhooks = append(f.webhooks, b)
		f.signatures = append(f.signatures, r.Header.Get("X-Puntored-Signature"))
		if f.webhookFailures > 0 {
			f.webhookFailures--
			w.WriteHeader(500)
		}
		return
	}
	if strings.HasPrefix(path, "/tenant/auth/") {
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		id := uuid.NewString()
		grant := map[string]any{"continue": map[string]any{"uri": f.base + "/opaque/c/" + id, "access_token": map[string]string{"value": "continuation"}, "wait": 0}}
		if body["interact"] != nil {
			grant["interact"] = map[string]string{"redirect": f.base + "/consent/" + id, "finish": "server-nonce"}
		} else {
			grant["access_token"] = map[string]any{"value": "incoming-token", "manage": f.base + "/opaque/m/" + id, "expires_in": 600}
		}
		writeFixture(w, grant)
		return
	}
	if strings.HasPrefix(path, "/opaque/c/") {
		if r.Method == "DELETE" {
			w.WriteHeader(204)
			return
		}
		f.continues++
		writeFixture(w, map[string]any{"access_token": map[string]any{"value": "authorized-token", "manage": f.base + "/opaque/m/" + strings.TrimPrefix(path, "/opaque/c/"), "expires_in": 600}})
		return
	}
	if strings.HasPrefix(path, "/opaque/m/") {
		writeFixture(w, map[string]any{"access_token": map[string]any{"value": "rotated-token", "manage": f.base + path + "-next", "expires_in": 600}})
		return
	}
	var body struct {
		WalletAddress   string         `json:"walletAddress"`
		IncomingAmount  *op.Amount     `json:"incomingAmount"`
		ExpiresAt       time.Time      `json:"expiresAt"`
		Receiver        string         `json:"receiver"`
		DebitAmount     *op.Amount     `json:"debitAmount"`
		IncomingPayment string         `json:"incomingPayment"`
		QuoteID         string         `json:"quoteId"`
		Metadata        map[string]any `json:"metadata"`
	}
	if r.Method == "POST" {
		_ = json.NewDecoder(r.Body).Decode(&body)
	}
	if strings.HasSuffix(path, "/incoming-payments") && r.Method == "POST" {
		id := f.base + path + "/" + uuid.NewString()
		incoming := op.Incoming{ID: id, WalletAddress: body.WalletAddress, IncomingAmount: body.IncomingAmount, ReceivedAmount: op.Amount{Value: "0", AssetCode: "USD", AssetScale: 2}, ExpiresAt: body.ExpiresAt}
		f.incoming[id] = incoming
		w.WriteHeader(201)
		writeFixture(w, incoming)
		return
	}
	if strings.HasSuffix(path, "/quotes") && r.Method == "POST" {
		incoming := f.incoming[body.Receiver]
		amount := incoming.IncomingAmount
		if body.DebitAmount != nil {
			amount = body.DebitAmount
		}
		if amount == nil {
			w.WriteHeader(400)
			return
		}
		id := f.base + path + "/" + uuid.NewString()
		quote := op.Quote{ID: id, DebitAmount: *amount, ReceiveAmount: *amount, ExpiresAt: time.Now().Add(5 * time.Minute)}
		if f.extraDebit {
			n, _ := new(big.Int).SetString(quote.DebitAmount.Value, 10)
			quote.DebitAmount.Value = n.Add(n, big.NewInt(1)).String()
		}
		f.quotes[id] = quoteFixture{Quote: quote, IncomingID: body.Receiver}
		w.WriteHeader(201)
		writeFixture(w, quote)
		return
	}
	if strings.HasSuffix(path, "/outgoing-payments") {
		if r.Method == "GET" {
			items := []op.Outgoing{}
			for _, o := range f.outgoing {
				items = append(items, o)
			}
			writeFixture(w, map[string]any{"result": items, "pagination": map[string]any{"hasNextPage": false}})
			return
		}
		if f.rejectDirect && body.QuoteID == "" {
			w.WriteHeader(400)
			writeFixture(w, map[string]any{"error": map[string]string{"code": "invalid_request"}})
			return
		}
		f.creates++
		incomingID := body.IncomingPayment
		var debit op.Amount
		if body.QuoteID != "" {
			q := f.quotes[body.QuoteID]
			incomingID = q.IncomingID
			debit = q.Quote.DebitAmount
		} else if body.DebitAmount != nil {
			debit = *body.DebitAmount
		}
		incoming := f.incoming[incomingID]
		id := f.base + path + "/" + uuid.NewString()
		out := op.Outgoing{ID: id, DebitAmount: debit, ReceiveAmount: debit, SentAmount: debit, Metadata: body.Metadata}
		if f.failBalance {
			out.Failed = true
			out.SentAmount.Value = "0"
			out.Metadata["cancellationReason"] = "Insufficient funds"
		} else {
			incoming.ReceivedAmount = debit
			if incoming.IncomingAmount != nil {
				incoming.Completed = true
			}
			f.incoming[incomingID] = incoming
		}
		f.outgoing[id] = out
		if f.lostResponse {
			f.lostResponse = false
			w.WriteHeader(500)
			writeFixture(w, map[string]any{"error": map[string]string{"code": "internal_error"}})
			return
		}
		w.WriteHeader(201)
		writeFixture(w, out)
		return
	}
	if strings.HasSuffix(path, "/complete") {
		id := f.base + strings.TrimSuffix(path, "/complete")
		incoming := f.incoming[id]
		incoming.Completed = true
		f.incoming[id] = incoming
		f.completes++
		writeFixture(w, incoming)
		return
	}
	target := f.base + path
	if in, ok := f.incoming[target]; ok {
		writeFixture(w, in)
		return
	}
	if out, ok := f.outgoing[target]; ok {
		writeFixture(w, out)
		return
	}
	if q, ok := f.quotes[target]; ok {
		writeFixture(w, q.Quote)
		return
	}
	w.WriteHeader(404)
}
func writeFixture(w http.ResponseWriter, v any) { _ = json.NewEncoder(w).Encode(v) }

type testApp struct {
	App     *Service
	API     *httpapi.Server
	Handler http.Handler
	Remote  *protocolFixture
	cfg     config.Config
}

func newApp(t *testing.T) *testApp {
	t.Helper()
	fixture := &protocolFixture{incoming: map[string]op.Incoming{}, quotes: map[string]quoteFixture{}, outgoing: map[string]op.Outgoing{}}
	remote := httptest.NewTLSServer(http.HandlerFunc(fixture.serve))
	fixture.base = remote.URL
	t.Cleanup(remote.Close)
	_, key, e := ed25519.GenerateKey(rand.Reader)
	if e != nil {
		t.Fatal(e)
	}
	der, e := x509.MarshalPKCS8PrivateKey(key)
	if e != nil {
		t.Fatal(e)
	}
	cfg := config.Config{InternalAPIKey: config.Secret(strings.Repeat("a", 32)), PrivateKey: config.Secret(pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: der})), ClientWallet: remote.URL + "/wallet/trust", KeyID: "test-key", PublicBaseURL: "https://pay.example", AllowedReturnPrefixes: []string{"https://app.example/"}, InteractionTimeout: time.Minute, PollInterval: time.Millisecond, PoolWallets: []string{remote.URL + "/wallet/pool1", remote.URL + "/wallet/pool2"}, SimPayerWallet: remote.URL + "/wallet/payer", SimEnabled: true, SimMaxAmount: "50000", WebhookURL: remote.URL + "/webhook", WebhookSecret: config.Secret(strings.Repeat("w", 32))}
	db, e := persistence.Open(context.Background(), testutil.DatabaseURL(t))
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(db.Pool.Close)
	if e = db.Migrate(context.Background()); e != nil {
		t.Fatal(e)
	}
	client, e := op.New(cfg, remote.Client())
	if e != nil {
		t.Fatal(e)
	}
	app, e := New(cfg, db, client)
	if e != nil {
		t.Fatal(e)
	}
	app.WebhookHTTP = remote.Client()
	if e = app.Bootstrap(context.Background()); e != nil {
		t.Fatal(e)
	}
	api, e := httpapi.New(cfg, db)
	if e != nil {
		t.Fatal(e)
	}
	app.Bind(api)
	return &testApp{App: app, API: api, Handler: api.Handler(), Remote: fixture, cfg: cfg}
}
func (a *testApp) request(t *testing.T, method, path string, body any, key string) (*httptest.ResponseRecorder, map[string]any) {
	t.Helper()
	var data []byte
	var e error
	if body != nil {
		data, e = json.Marshal(body)
		if e != nil {
			t.Fatal(e)
		}
	}
	r := httptest.NewRequest(method, path, strings.NewReader(string(data)))
	r.Header.Set("X-Internal-Api-Key", string(a.cfg.InternalAPIKey))
	r.Header.Set("Content-Type", "application/json")
	if key != "" {
		r.Header.Set("Idempotency-Key", key)
	}
	w := httptest.NewRecorder()
	a.Handler.ServeHTTP(w, r)
	out := map[string]any{}
	if w.Body.Len() > 0 && strings.HasPrefix(w.Header().Get("Content-Type"), "application/json") {
		if e = json.Unmarshal(w.Body.Bytes(), &out); e != nil {
			t.Fatal(e)
		}
	}
	return w, out
}
func (a *testApp) approve(t *testing.T, id string) {
	t.Helper()
	var attempt, sessionID string
	if e := a.App.DB.Pool.QueryRow(context.Background(), "SELECT id::text,session_id::text FROM grant_attempts WHERE resource_id=$1 AND used_at IS NULL ORDER BY created_at DESC LIMIT 1", id).Scan(&attempt, &sessionID); e != nil {
		t.Fatal(e)
	}
	session, e := a.App.Work.LoadSession(context.Background(), a.App.DB.Pool, sessionID)
	if e != nil {
		t.Fatal(e)
	}
	ref := "approved-ref"
	digest := sha256.Sum256([]byte(session.ClientNonce + "\n" + session.ServerNonce + "\n" + ref + "\n" + session.Wallet.AuthServer))
	query := url.Values{"hash": []string{base64.StdEncoding.EncodeToString(digest[:])}, "interact_ref": []string{ref}}
	r := httptest.NewRequest("GET", "/v1/interact/finish/"+attempt+"?"+query.Encode(), nil)
	w := httptest.NewRecorder()
	a.Handler.ServeHTTP(w, r)
	if w.Code != 200 && w.Code != 302 {
		t.Fatal("callback failed", w.Code, w.Body.String())
	}
	w = httptest.NewRecorder()
	a.Handler.ServeHTTP(w, r)
	if w.Code != 200 && w.Code != 302 {
		t.Fatal("callback replay failed")
	}
}
func (a *testApp) drive(t *testing.T, kind, id, wanted string) {
	t.Helper()
	owner := uuid.NewString()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		if _, e := a.App.RunOnce(context.Background(), owner); e != nil {
			t.Fatal("worker failed", e)
		}
		if e := a.App.DeliverWebhooks(context.Background()); e != nil {
			t.Fatal("webhook failed", e)
		}
		v, e := read(context.Background(), a.App.DB.Pool, id, kind, false)
		if e != nil {
			t.Fatal(e)
		}
		if status(v) == wanted {
			return
		}
		if status(v) == "failed" && wanted != "failed" {
			t.Fatal("unexpected failure", v)
		}
		time.Sleep(time.Millisecond)
	}
	v, _ := read(context.Background(), a.App.DB.Pool, id, kind, false)
	t.Fatal("workflow did not reach", wanted, v)
}
func (a *testApp) authorizeAll(t *testing.T) {
	t.Helper()
	w, out := a.request(t, "GET", "/v1/admin/pool-wallets", nil, "")
	if w.Code != 200 {
		t.Fatal(w.Code, w.Body.String())
	}
	for _, item := range out["items"].([]any) {
		id := item.(map[string]any)["id"].(string)
		w, _ = a.request(t, "POST", "/v1/admin/pool-wallets/"+id+"/authorize", map[string]any{}, uuid.NewString())
		if w.Code != 201 {
			t.Fatal(w.Code, w.Body.String())
		}
		a.approve(t, id)
		a.drive(t, "pool_wallet", id, "authorized")
	}
}
func (a *testApp) account(t *testing.T) string {
	t.Helper()
	w, v := a.request(t, "POST", "/v1/collection-accounts", map[string]any{"external_ref": "merchant:" + uuid.NewString(), "display_name": "Tienda <Trust>", "asset_code": "USD"}, uuid.NewString())
	if w.Code != 201 {
		t.Fatal(w.Code, w.Body.String())
	}
	validateResponse(t, a, "CollectionAccount", v)
	return v["id"].(string)
}
func validateResponse(t *testing.T, a *testApp, schema string, value any) {
	t.Helper()
	if e := a.API.Contract.Schemas[schema].Validate(value); e != nil {
		t.Fatal("response violates contract", schema, e)
	}
}
func amount(value string) map[string]any {
	return map[string]any{"value": value, "asset_code": "USD", "asset_scale": 2}
}
func TestCreditQRCollectionAndDistribution(t *testing.T) {
	a := newApp(t)
	a.authorizeAll(t)
	account := a.account(t)
	base := a.Remote.base
	w, payment := a.request(t, "POST", "/v1/payments", map[string]any{"sender_wallet_address": base + "/wallet/lender", "receiver_wallet_address": base + "/wallet/merchant", "receive_amount": amount("10000"), "external_ref": "loan:1:disb", "return_url": "https://app.example/return?loan_id=1&client=web"}, "loan:1:disb:1")
	if w.Code != 201 {
		t.Fatal(w.Code, w.Body.String())
	}
	validateResponse(t, a, "Payment", payment)
	paymentID := payment["id"].(string)
	a.approve(t, paymentID)
	a.drive(t, "payment", paymentID, "completed")
	w, payment = a.request(t, "GET", "/v1/payments/"+paymentID, nil, "")
	if w.Code != 200 {
		t.Fatal(w.Code)
	}
	validateResponse(t, a, "Payment", payment)
	w, sim := a.request(t, "POST", "/v1/sim/puntored/qr-payments", map[string]any{"collection_account_id": account, "amount": amount("4800"), "payer_bank": "Bancolombia"}, "sim:1")
	if w.Code != 201 {
		t.Fatal(w.Code, w.Body.String())
	}
	validateResponse(t, a, "SimQrPayment", sim)
	a.drive(t, "sim_payment", sim["id"].(string), "completed")
	w, list := a.request(t, "GET", "/v1/sim/puntored/qr-payments?after=0&limit=100", nil, "")
	if w.Code != 200 {
		t.Fatal(w.Code)
	}
	validateResponse(t, a, "SimQrPaymentList", list)
	if len(list["items"].([]any)) != 1 {
		t.Fatal("missing reconciled payment")
	}
	a.Remote.mu.Lock()
	if len(a.Remote.webhooks) != 1 || a.Remote.signatures[0] == "" {
		t.Fatal("webhook missing")
	}
	a.Remote.mu.Unlock()
	for i, value := range []string{"1000", "3500", "300"} {
		w, transfer := a.request(t, "POST", "/v1/transfers", map[string]any{"collection_account_id": account, "receiver_wallet_address": base + "/wallet/receiver" + fmt.Sprint(i), "debit_amount": amount(value), "external_ref": fmt.Sprint("payout:", i)}, fmt.Sprint("payout:", i, ":1"))
		if w.Code != 201 {
			t.Fatal(w.Code, w.Body.String())
		}
		validateResponse(t, a, "Transfer", transfer)
		id := transfer["id"].(string)
		a.drive(t, "transfer", id, "completed")
		_, transfer = a.request(t, "GET", "/v1/transfers/"+id, nil, "")
		validateResponse(t, a, "Transfer", transfer)
	}
	_, accountBody := a.request(t, "GET", "/v1/collection-accounts/"+account, nil, "")
	if accountBody["ledger_balance"].(map[string]any)["value"] != "0" {
		t.Fatal("ledger mismatch", accountBody)
	}
	a.Remote.mu.Lock()
	defer a.Remote.mu.Unlock()
	if a.Remote.creates != 5 || a.Remote.completes != 3 || a.Remote.continues != 4 {
		t.Fatal("unexpected protocol side effects", a.Remote.creates, a.Remote.completes, a.Remote.continues)
	}
}
func TestLostOutgoingResponseIsReconciledWithoutReposting(t *testing.T) {
	a := newApp(t)
	a.authorizeAll(t)
	id := a.account(t)
	a.Remote.mu.Lock()
	a.Remote.lostResponse = true
	a.Remote.mu.Unlock()
	w, v := a.request(t, "POST", "/v1/transfers", map[string]any{"collection_account_id": id, "receiver_wallet_address": a.Remote.base + "/wallet/receiver", "debit_amount": amount("123"), "external_ref": "payout:uncertain"}, uuid.NewString())
	if w.Code != 201 {
		t.Fatal(w.Code, w.Body.String())
	}
	a.drive(t, "transfer", v["id"].(string), "completed")
	a.Remote.mu.Lock()
	defer a.Remote.mu.Unlock()
	if a.Remote.creates != 1 {
		t.Fatal("uncertain outgoing duplicated", a.Remote.creates)
	}
}
func TestInsufficientFundsPreservesActualSentAmount(t *testing.T) {
	a := newApp(t)
	a.authorizeAll(t)
	id := a.account(t)
	a.Remote.mu.Lock()
	a.Remote.failBalance = true
	a.Remote.mu.Unlock()
	w, v := a.request(t, "POST", "/v1/transfers", map[string]any{"collection_account_id": id, "receiver_wallet_address": a.Remote.base + "/wallet/receiver", "debit_amount": amount("123"), "external_ref": "payout:empty"}, uuid.NewString())
	if w.Code != 201 {
		t.Fatal(w.Code)
	}
	transferID := v["id"].(string)
	a.drive(t, "transfer", transferID, "failed")
	_, v = a.request(t, "GET", "/v1/transfers/"+transferID, nil, "")
	if v["failure_code"] != "insufficient_balance" || v["retryable"] != true || v["sent_amount"].(map[string]any)["value"] != "0" {
		t.Fatal("failure not reconciled", v)
	}
	validateResponse(t, a, "Transfer", v)
}
func TestMajorUnitParsing(t *testing.T) {
	for _, tc := range []struct {
		text  string
		scale int
		want  string
	}{{"48.00", 2, "4800"}, {"000.01", 2, "1"}, {"1", 0, "1"}, {"9007199254740993.01", 2, "900719925474099301"}} {
		got, e := MinorUnits(tc.text, tc.scale)
		if e != nil || got != tc.want {
			t.Fatal(tc, got, e)
		}
	}
	for _, s := range []string{"0", "-1", "1e3", "1,00", "1.001", "NaN"} {
		if _, e := MinorUnits(s, 2); e == nil {
			t.Fatal("invalid amount accepted", s)
		}
	}
}

func TestQRFormEscapesNameAndReplaysWithoutDoublePayment(t *testing.T) {
	a := newApp(t)
	a.authorizeAll(t)
	id := a.account(t)
	get := httptest.NewRecorder()
	a.Handler.ServeHTTP(get, httptest.NewRequest("GET", "/qr/"+id, nil))
	if get.Code != 200 || strings.Contains(get.Body.String(), "<Trust>") {
		t.Fatal("unsafe QR page", get.Code, get.Body.String())
	}
	pattern := regexp.MustCompile(`name="form_token" value="([^"]+)"`)
	match := pattern.FindStringSubmatch(get.Body.String())
	if len(match) != 2 {
		t.Fatal("missing form token")
	}
	form := url.Values{"form_token": []string{match[1]}, "amount": []string{"1.23"}, "payer_bank": []string{"Demo"}}
	send := func() *httptest.ResponseRecorder {
		r := httptest.NewRequest("POST", "/qr/"+id+"/pay", strings.NewReader(form.Encode()))
		r.Header.Set("Content-Type", "application/x-www-form-urlencoded")
		w := httptest.NewRecorder()
		a.Handler.ServeHTTP(w, r)
		return w
	}
	first, second := send(), send()
	if first.Code != 200 || second.Code != 200 || second.Header().Get("Idempotent-Replayed") != "true" {
		t.Fatal("form replay failed", first.Code, second.Code, first.Body.String(), second.Body.String())
	}
	var count int
	if e := a.App.DB.Pool.QueryRow(context.Background(), "SELECT count(*) FROM resources WHERE kind='sim_payment'").Scan(&count); e != nil || count != 1 {
		t.Fatal("duplicate form payment", count, e)
	}
}
func TestConcurrentAccountAssignmentCannotReuseWalletOrExternalRef(t *testing.T) {
	a := newApp(t)
	a.authorizeAll(t)
	responses := make(chan int, 2)
	body := map[string]any{"external_ref": "merchant:same", "display_name": "Shop", "asset_code": "USD"}
	for range 2 {
		go func() {
			w, _ := a.request(t, "POST", "/v1/collection-accounts", body, uuid.NewString())
			responses <- w.Code
		}()
	}
	first, second := <-responses, <-responses
	if !(first == 201 && second == 409 || first == 409 && second == 201) {
		t.Fatal("concurrent reference assigned twice", first, second)
	}
	_ = a.account(t)
	w, _ := a.request(t, "POST", "/v1/collection-accounts", map[string]any{"external_ref": "merchant:extra", "display_name": "Shop", "asset_code": "USD"}, uuid.NewString())
	if w.Code != 409 {
		t.Fatal("pool wallet reused", w.Code)
	}
}
