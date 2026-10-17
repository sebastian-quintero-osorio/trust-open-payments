package config

import (
	"bytes"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/x509"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"log/slog"
	"strings"
	"testing"
)

func validEnv(t *testing.T) map[string]string {
	t.Helper()
	_, key, e := ed25519.GenerateKey(rand.Reader)
	if e != nil {
		t.Fatal(e)
	}
	der, e := x509.MarshalPKCS8PrivateKey(key)
	if e != nil {
		t.Fatal(e)
	}
	return map[string]string{"INTERNAL_API_KEY": strings.Repeat("a", 32), "DATABASE_URL": "postgres://user:private@localhost/test", "OP_PRIVATE_KEY": string(pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: der})), "OP_KEY_ID": "key-id", "OP_CLIENT_WALLET_ADDRESS": "https://wallet.example/trust", "PUBLIC_BASE_URL": "https://pay.example", "ALLOWED_RETURN_URL_PREFIXES": "https://app.example/"}
}
func TestLoadAndRedaction(t *testing.T) {
	env := validEnv(t)
	c, e := Load(func(k string) string { return env[k] })
	if e != nil {
		t.Fatal(e)
	}
	if c.PollInterval.Seconds() != 2 || c.InteractionTimeout.Seconds() != 600 {
		t.Fatal("incorrect defaults")
	}
	for _, format := range []string{"%v", "%+v", "%#v"} {
		s := fmt.Sprintf(format, c)
		for _, name := range []string{"OP_PRIVATE_KEY", "INTERNAL_API_KEY", "DATABASE_URL"} {
			if strings.Contains(s, env[name]) {
				t.Fatalf("secret disclosed: %s", name)
			}
		}
	}
}
func TestRejectInvalidConfiguration(t *testing.T) {
	for _, tc := range []struct{ key, value string }{{"INTERNAL_API_KEY", "short"}, {"DATABASE_URL", "https://bad:secret@example"}, {"OP_PRIVATE_KEY", "SECRET"}, {"PUBLIC_BASE_URL", "https://user:secret@example"}, {"OP_CLIENT_WALLET_ADDRESS", "http://example"}, {"SIM_ENABLED", "yes"}, {"POLL_INTERVAL_SECONDS", "0"}, {"INTERACTION_TIMEOUT_SECONDS", "999999999999999999"}, {"ALLOWED_RETURN_URL_PREFIXES", ""}} {
		t.Run(tc.key, func(t *testing.T) {
			env := validEnv(t)
			env[tc.key] = tc.value
			_, e := Load(func(k string) string { return env[k] })
			if e == nil {
				t.Fatal("accepted invalid configuration")
			}
			if strings.Contains(e.Error(), "SECRET") || strings.Contains(e.Error(), "bad:secret") {
				t.Fatal("error disclosed secret")
			}
		})
	}
}
func TestReturnURLAllowlist(t *testing.T) {
	c := Config{AllowedReturnPrefixes: []string{"https://app.example/"}}
	for _, tc := range []struct {
		url     string
		allowed bool
	}{{"https://app.example/return?loan=1", true}, {"https://app.example.evil/return", false}, {"https://app.example@evil/", false}, {"http://app.example/", false}, {"https://app.example:444/", false}, {"https://evil/?next=https://app.example/", false}} {
		if c.ReturnURLAllowed(tc.url) != tc.allowed {
			t.Errorf("unexpected decision for %s", tc.url)
		}
	}
}

func TestDemoDefaultsKeepReturnURLsRestricted(t *testing.T) {
	env := validEnv(t)
	env["DEMO_MODE"] = "true"
	delete(env, "ALLOWED_RETURN_URL_PREFIXES")
	c, err := Load(func(k string) string { return env[k] })
	if err != nil {
		t.Fatal(err)
	}
	if !c.ReturnURLAllowed("https://pay.example/v1/health?op_status=completed") || c.ReturnURLAllowed("https://evil.example/") || c.ReturnURLAllowed("https://pay.example/other") {
		t.Fatal("demo return URL restriction failed")
	}
	if len(c.TrustedProxyCIDRs) != 0 {
		t.Fatal("demo must not trust unconfigured proxy headers")
	}
	env["ALLOWED_RETURN_URL_PREFIXES"] = "https://app.example/"
	c, err = Load(func(k string) string { return env[k] })
	if err != nil || !c.ReturnURLAllowed("https://app.example/return") || c.ReturnURLAllowed("https://pay.example/v1/health") {
		t.Fatal("explicit return allowlist must take precedence")
	}
	delete(env, "ALLOWED_RETURN_URL_PREFIXES")
	env["PUBLIC_BASE_URL"] = "http://localhost:8080"
	if _, err = Load(func(k string) string { return env[k] }); err == nil {
		t.Fatal("demo accepted insecure callback URL")
	}
}

func TestStructuredLogsRedactNestedSecrets(t *testing.T) {
	env := validEnv(t)
	cfg, err := Load(func(k string) string { return env[k] })
	if err != nil {
		t.Fatal(err)
	}
	var buffer bytes.Buffer
	logger := slog.New(slog.NewJSONHandler(&buffer, nil))
	logger.Info("configuration", "config", cfg)
	encoded, err := json.Marshal(cfg)
	if err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{"DATABASE_URL", "INTERNAL_API_KEY", "OP_PRIVATE_KEY"} {
		if strings.Contains(buffer.String(), env[key]) || strings.Contains(string(encoded), env[key]) {
			t.Fatalf("nested secret disclosed: %s", key)
		}
	}
	if !strings.Contains(buffer.String(), "[REDACTED]") {
		t.Fatal("redaction marker missing")
	}
}
