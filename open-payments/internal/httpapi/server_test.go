package httpapi

import (
	"bytes"
	"encoding/json"
	"github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/config"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestContractCompilesAndHealth(t *testing.T) {
	s, e := New(config.Config{InternalAPIKey: config.Secret(strings.Repeat("x", 32)), ClientWallet: "https://wallet.example/trust"}, nil)
	if e != nil {
		t.Fatal(e)
	}
	w := httptest.NewRecorder()
	s.Handler().ServeHTTP(w, httptest.NewRequest("GET", "/v1/health", nil))
	if w.Code != 200 {
		t.Fatal(w.Code, w.Body.String())
	}
	var body any
	if e = json.Unmarshal(w.Body.Bytes(), &body); e != nil {
		t.Fatal(e)
	}
	if e = s.Contract.Schemas["Health"].Validate(body); e != nil {
		t.Fatal(e)
	}
}
func TestEveryPrivateRouteRequiresAuthentication(t *testing.T) {
	s, e := New(config.Config{InternalAPIKey: "private-test-key"}, nil)
	if e != nil {
		t.Fatal(e)
	}
	for _, r := range s.Contract.Routes {
		if r.OperationID == "getHealth" || r.OperationID == "interactFinish" || strings.HasPrefix(r.Path, "/qr/") {
			continue
		}
		path := strings.ReplaceAll(r.Path, "{collection_account_id}", "123")
		for _, key := range []string{"", "wrong"} {
			req := httptest.NewRequest(r.Method, path, strings.NewReader(`{}`))
			if key != "" {
				req.Header.Set("X-Internal-Api-Key", key)
			}
			w := httptest.NewRecorder()
			s.Handler().ServeHTTP(w, req)
			if w.Code != 401 {
				t.Errorf("%s %s: %d", r.Method, r.Path, w.Code)
			}
		}
	}
}
func TestCanonicalJSON(t *testing.T) {
	a, ha, e := CanonicalJSON([]byte(`{"z":"99999999999999999999999", "a":{"y":2,"x":1}}`))
	if e != nil {
		t.Fatal(e)
	}
	b, hb, e := CanonicalJSON([]byte(` { "a": {"x":1, "y":2}, "z":"99999999999999999999999" } `))
	if e != nil || !bytes.Equal(a, b) || ha != hb {
		t.Fatal("canonical representations differ", e)
	}
	for _, raw := range []string{`{"a":1,"a":2}`, `{"a":{"x":1,"x":2}}`, `{} {}`, `[`, ``, strings.Repeat("[", 66) + strings.Repeat("]", 66)} {
		if _, _, e = CanonicalJSON([]byte(raw)); e == nil {
			t.Fatalf("accepted invalid JSON %q", raw)
		}
	}
}
func TestRequestSchemas(t *testing.T) {
	c, e := LoadContract()
	if e != nil {
		t.Fatal(e)
	}
	base := `{"collection_account_id":"2c4e6a80-1b3d-4f5a-8c7e-9d0f1a2b3c4d","receiver_wallet_address":"https://wallet.example/receiver","debit_amount":{"value":"99999999999999999999999","asset_code":"USD","asset_scale":2},"external_ref":"payout:1"}`
	if e = validateBody(c.Schemas["CreateTransferRequest"], []byte(base)); e != nil {
		t.Fatal(e)
	}
	for _, raw := range []string{`{}`, strings.Replace(base, `"99999999999999999999999"`, `"0"`, 1), strings.Replace(base, `"asset_scale":2`, `"asset_scale":256`, 1), strings.Replace(base, `"99999999999999999999999"`, `4.8`, 1), strings.Replace(base, "2c4e6a80-1b3d-4f5a-8c7e-9d0f1a2b3c4d", "invalid", 1)} {
		if e = validateBody(c.Schemas["CreateTransferRequest"], []byte(raw)); e == nil {
			t.Fatal("accepted invalid transfer", raw)
		}
	}
}
func TestUnauthenticatedRequestsNeverReachOperation(t *testing.T) {
	s, e := New(config.Config{InternalAPIKey: "private"}, nil)
	if e != nil {
		t.Fatal(e)
	}
	req := httptest.NewRequest(http.MethodPost, "/v1/payments", strings.NewReader("not-json"))
	w := httptest.NewRecorder()
	s.Handler().ServeHTTP(w, req)
	if w.Code != 401 {
		t.Fatal(w.Code)
	}
}

func TestCanonicalNumbersAreExact(t *testing.T) {
	for _, tc := range []struct{ raw, want string }{
		{`{"n":2.0}`, `{"n":2}`},
		{`{"n":2e0}`, `{"n":2}`},
		{`{"n":-0.00}`, `{"n":0}`},
		{`{"n":12300e-2}`, `{"n":123}`},
		{`{"n":1.234e-2}`, `{"n":0.01234}`},
		{`{"n":10000000000000000000000000000000000000001}`, `{"n":10000000000000000000000000000000000000001}`},
	} {
		b, _, err := CanonicalJSON([]byte(tc.raw))
		if err != nil || string(b) != tc.want {
			t.Fatalf("%s: %s %v", tc.raw, b, err)
		}
	}
	if _, _, e := CanonicalJSON([]byte(`{"n":1e99999999999}`)); e == nil {
		t.Fatal("unbounded exponent accepted")
	}
}
