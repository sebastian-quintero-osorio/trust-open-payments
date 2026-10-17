package openpayments

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestFinishHashTenantAndEncodings(t *testing.T) {
	digest := sha256.Sum256([]byte("client\nserver\nref\nhttps://auth.example/tenant"))
	for _, enc := range []*base64.Encoding{base64.StdEncoding, base64.RawURLEncoding} {
		hash := enc.EncodeToString(digest[:])
		if !VerifyFinish("client", "server", "ref", "https://auth.example/tenant", hash) {
			t.Fatal("valid hash rejected")
		}
		if VerifyFinish("client", "server", "ref", "https://auth.example", hash) || VerifyFinish("wrong", "server", "ref", "https://auth.example/tenant", hash) {
			t.Fatal("wrong binding accepted")
		}
	}
	if VerifyFinish("client", "server", "ref", "https://auth.example/tenant", "bad") {
		t.Fatal("invalid encoding accepted")
	}
}

func TestInteractiveContinueRotateAndCancelUseOpaqueURLs(t *testing.T) {
	cfg, _ := testConfig(t)
	var base string
	var requests int
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests++
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/tenant/auth":
			var p map[string]any
			if e := json.NewDecoder(r.Body).Decode(&p); e != nil {
				t.Error(e)
			}
			interact := p["interact"].(map[string]any)
			finish := interact["finish"].(map[string]any)
			if finish["nonce"] != "client-nonce" || finish["hash_method"] != "sha-256" {
				t.Error("incorrect interaction finish")
			}
			access := p["access_token"].(map[string]any)["access"].([]any)[0].(map[string]any)
			if access["limits"].(map[string]any)["debitAmount"].(map[string]any)["value"] != "123" {
				t.Error("missing debit limit")
			}
			_, _ = fmt.Fprintf(w, `{"interact":{"redirect":%q,"finish":"server-nonce"},"continue":{"uri":%q,"access_token":{"value":"continuation"},"wait":5}}`, base+"/consent", base+"/opaque/c")
		case "/opaque/c":
			if r.Method == "DELETE" {
				w.WriteHeader(204)
				return
			}
			_, _ = fmt.Fprintf(w, `{"access_token":{"value":"old","manage":%q,"expires_in":600}}`, base+"/opaque/m")
		case "/opaque/m":
			if r.Header.Get("Authorization") != "GNAP old" {
				t.Error("old token not used")
			}
			_, _ = fmt.Fprintf(w, `{"access_token":{"value":"new","manage":%q,"expires_in":600}}`, base+"/opaque/new-m")
		default:
			t.Error("unexpected URL", r.URL.Path)
		}
	}))
	defer srv.Close()
	base = srv.URL
	c, e := New(cfg, srv.Client())
	if e != nil {
		t.Fatal(e)
	}
	g, e := c.InteractiveGrant(context.Background(), Wallet{ID: base + "/wallet", AuthServer: base + "/tenant/auth"}, "https://pay.example/finish", "client-nonce", &Amount{"123", "USD", 2}, base+"/incoming/i")
	if e != nil {
		t.Fatal(e)
	}
	confirmed, e := c.ContinueGrant(context.Background(), g, "ref")
	if e != nil {
		t.Fatal(e)
	}
	token, e := c.RotateToken(context.Background(), *confirmed.AccessToken)
	if e != nil || string(token.Value) != "new" || string(token.Manage) != base+"/opaque/new-m" {
		t.Fatal("rotation failed", e)
	}
	if e = c.CancelGrant(context.Background(), g); e != nil {
		t.Fatal(e)
	}
	if requests != 4 {
		t.Fatal("unexpected request count", requests)
	}
}
