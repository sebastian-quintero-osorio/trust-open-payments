package openpayments

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/config"
)

func testConfig(t *testing.T) (config.Config, ed25519.PublicKey) {
	t.Helper()
	pub, key, e := ed25519.GenerateKey(rand.Reader)
	if e != nil {
		t.Fatal(e)
	}
	der, e := x509.MarshalPKCS8PrivateKey(key)
	if e != nil {
		t.Fatal(e)
	}
	return config.Config{ClientWallet: "https://client.example/trust", KeyID: "test-key", PrivateKey: config.Secret(pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: der}))}, pub
}
func verifySignature(t *testing.T, r *http.Request, pub ed25519.PublicKey) {
	t.Helper()
	input := strings.TrimPrefix(r.Header.Get("Signature-Input"), "sig1=")
	parts := strings.SplitN(input, ")", 2)
	if len(parts) != 2 {
		t.Error("missing signature input")
		return
	}
	components := strings.Fields(strings.TrimPrefix(parts[0], "("))
	var lines []string
	for _, comp := range components {
		comp = strings.Trim(comp, `"`)
		var v string
		switch comp {
		case "@method":
			v = r.Method
		case "@target-uri":
			v = "https://" + r.Host + r.URL.RequestURI()
		default:
			v = r.Header.Get(comp)
		}
		lines = append(lines, fmt.Sprintf("%q: %s", comp, v))
	}
	lines = append(lines, `"@signature-params": `+input)
	sig := strings.TrimSuffix(strings.TrimPrefix(r.Header.Get("Signature"), "sig1=:"), ":")
	b, e := base64.StdEncoding.DecodeString(sig)
	if e != nil || !ed25519.Verify(pub, []byte(strings.Join(lines, "\n")), b) {
		t.Error("invalid Ed25519 signature")
	}
}
func TestProtocolFlowPreservesTenantsAndExactAmounts(t *testing.T) {
	cfg, pub := testConfig(t)
	var base string
	var signed atomic.Int32
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Path == "/wallet" {
			_, _ = fmt.Fprintf(w, `{"id":%q,"assetCode":"USD","assetScale":2,"authServer":%q,"resourceServer":%q}`, base+"/wallet", base+"/tenant/auth", base+"/tenant/rs")
			return
		}
		verifySignature(t, r, pub)
		signed.Add(1)
		switch r.URL.Path {
		case "/tenant/auth":
			var p map[string]any
			if e := json.NewDecoder(r.Body).Decode(&p); e != nil {
				t.Error(e)
			}
			if p["client"] != cfg.ClientWallet {
				t.Error("incorrect client identity")
			}
			_, _ = io.WriteString(w, `{"access_token":{"value":"test-token","manage":"`+base+`/opaque/manage","expires_in":600},"continue":{"uri":"`+base+`/opaque/continue","access_token":{"value":"continuation"}}}`)
		case "/tenant/rs/incoming-payments":
			var p map[string]any
			if e := json.NewDecoder(r.Body).Decode(&p); e != nil {
				t.Error(e)
			}
			amount := p["incomingAmount"].(map[string]any)
			if amount["value"] != "99999999999999999999999" {
				t.Error("amount lost precision")
			}
			w.WriteHeader(201)
			_, _ = fmt.Fprintf(w, `{"id":%q,"expiresAt":%q}`, base+"/tenant/rs/incoming-payments/i", time.Now().Add(time.Hour).UTC().Format(time.RFC3339))
		case "/tenant/rs/quotes":
			w.WriteHeader(201)
			_, _ = fmt.Fprintf(w, `{"id":%q,"debitAmount":{"value":"100","assetCode":"USD","assetScale":2},"receiveAmount":{"value":"99","assetCode":"USD","assetScale":2},"expiresAt":%q}`, base+"/tenant/rs/quotes/q", time.Now().Add(time.Minute).UTC().Format(time.RFC3339))
		case "/tenant/rs/outgoing-payments":
			w.WriteHeader(201)
			_, _ = fmt.Fprintf(w, `{"id":%q,"failed":false,"sentAmount":{"value":"100","assetCode":"USD","assetScale":2}}`, base+"/tenant/rs/outgoing-payments/o")
		default:
			_, _ = io.WriteString(w, `{"completed":true,"receivedAmount":{"value":"99","assetCode":"USD","assetScale":2}}`)
		}
	}))
	defer srv.Close()
	base = srv.URL
	c, e := New(cfg, srv.Client())
	if e != nil {
		t.Fatal(e)
	}
	ctx := context.Background()
	wallet, e := c.Resolve(ctx, base+"/wallet")
	if e != nil {
		t.Fatal(e)
	}
	grant, e := c.NonInteractiveGrant(ctx, wallet, "incoming-payment", []string{"create", "read", "complete"})
	if e != nil {
		t.Fatal(e)
	}
	if string(grant.AccessToken.Manage) != base+"/opaque/manage" {
		t.Fatal("manage URL modified")
	}
	a := Amount{"99999999999999999999999", "USD", 2}
	incoming, e := c.CreateIncoming(ctx, wallet, grant.AccessToken.Value, &a, time.Now().Add(time.Hour), map[string]any{"external_ref": "loan:1"})
	if e != nil {
		t.Fatal(e)
	}
	q, e := c.CreateQuote(ctx, wallet, grant.AccessToken.Value, incoming.ID, nil)
	if e != nil {
		t.Fatal(e)
	}
	if _, e = c.GetQuote(ctx, q.ID, grant.AccessToken.Value); e != nil {
		t.Fatal(e)
	}
	out, e := c.CreateOutgoing(ctx, wallet, grant.AccessToken.Value, q.ID, "", nil, nil)
	if e != nil {
		t.Fatal(e)
	}
	if _, e = c.GetOutgoing(ctx, out.ID, grant.AccessToken.Value); e != nil {
		t.Fatal(e)
	}
	if _, e = c.GetIncoming(ctx, incoming.ID, grant.AccessToken.Value); e != nil {
		t.Fatal(e)
	}
	completed, e := c.CompleteIncoming(ctx, incoming.ID, grant.AccessToken.Value)
	if e != nil || !completed.Completed {
		t.Fatal("not completed", e)
	}
	if signed.Load() != 8 {
		t.Fatal("unexpected request count", signed.Load())
	}
}
func TestSSRFAndRedirects(t *testing.T) {
	for _, ip := range []string{"127.0.0.1", "10.0.0.1", "169.254.169.254", "100.64.0.1", "::1", "::ffff:127.0.0.1", "fc00::1", "2001:db8::1", "64:ff9b::a00:1"} {
		if PublicIP(netip.MustParseAddr(ip)) {
			t.Error("unsafe public IP", ip)
		}
	}
	for _, ip := range []string{"8.8.8.8", "2606:4700:4700::1111"} {
		if !PublicIP(netip.MustParseAddr(ip)) {
			t.Error("public IP rejected", ip)
		}
	}
	cfg, _ := testConfig(t)
	c, e := New(cfg, nil)
	if e != nil {
		t.Fatal(e)
	}
	for _, u := range []string{"http://example.com", "https://user:pass@example.com", "https://127.0.0.1/wallet", "https://[::1]/wallet"} {
		if _, e = c.Resolve(context.Background(), u); e == nil {
			t.Error("unsafe wallet accepted", u)
		}
	}
	var hit atomic.Int32
	target := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { hit.Add(1) }))
	defer target.Close()
	origin := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { http.Redirect(w, r, target.URL, http.StatusFound) }))
	defer origin.Close()
	c, e = New(cfg, origin.Client())
	if e != nil {
		t.Fatal(e)
	}
	if _, e = c.Resolve(context.Background(), origin.URL); e == nil || hit.Load() != 0 {
		t.Fatal("followed redirect")
	}
}
func TestErrorsAreRedactedAndPOSTNeverRetried(t *testing.T) {
	cfg, _ := testConfig(t)
	var hit atomic.Int32
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hit.Add(1)
		w.WriteHeader(500)
		_, _ = io.WriteString(w, `{"error":{"code":"bad_gateway","description":"SECRET-MUST-NOT-LEAK"}}`)
	}))
	defer srv.Close()
	c, e := New(cfg, srv.Client())
	if e != nil {
		t.Fatal(e)
	}
	_, e = c.RequestGrant(context.Background(), Wallet{ID: srv.URL, AuthServer: srv.URL}, nil, nil)
	if e == nil || strings.Contains(e.Error(), "SECRET") || hit.Load() != 1 {
		t.Fatal("unsafe upstream handling", e)
	}
}
