// Package config loads runtime settings without exposing secret values in errors.
package config

import (
	"crypto/ed25519"
	"crypto/x509"
	"encoding/pem"
	"errors"
	"fmt"
	"log/slog"
	"math/big"
	"net/url"
	"strconv"
	"strings"
	"time"
)

// Secret prevents accidental disclosure through standard formatting and slog.
type Secret string

// String prevents disclosure through fmt.Stringer.
func (Secret) String() string { return "[REDACTED]" }

// GoString prevents disclosure through Go-syntax formatting.
func (Secret) GoString() string { return "[REDACTED]" }

// LogValue redacts direct structured logging of a secret.
func (Secret) LogValue() slog.Value { return slog.StringValue("[REDACTED]") }

// MarshalJSON also redacts secrets nested in structured log fields.
func (Secret) MarshalJSON() ([]byte, error) { return []byte(`"[REDACTED]"`), nil }

// MarshalText redacts secrets used by text-based serializers.
func (Secret) MarshalText() ([]byte, error) { return []byte("[REDACTED]"), nil }

// Config contains validated runtime configuration. Secrets must never be logged.
type Config struct {
	InternalAPIKey        Secret
	DatabaseURL           Secret
	PrivateKey            Secret
	KeyID                 string
	ClientWallet          string
	PublicBaseURL         string
	PoolWallets           []string
	SimPayerWallet        string
	SimEnabled            bool
	SimMaxAmount          string
	WebhookURL            string
	WebhookSecret         Secret
	AllowedReturnPrefixes []string
	InteractionTimeout    time.Duration
	PollInterval          time.Duration
}

// Load reads all documented settings. Error messages name fields, never values.
func Load(get func(string) string) (Config, error) {
	c := Config{InternalAPIKey: Secret(get("INTERNAL_API_KEY")), DatabaseURL: Secret(get("DATABASE_URL")), PrivateKey: Secret(get("OP_PRIVATE_KEY")), KeyID: get("OP_KEY_ID"), ClientWallet: get("OP_CLIENT_WALLET_ADDRESS"), PublicBaseURL: strings.TrimRight(get("PUBLIC_BASE_URL"), "/"), SimPayerWallet: get("SIM_PAYER_WALLET_ADDRESS"), SimMaxAmount: get("SIM_MAX_AMOUNT"), WebhookURL: get("TRUST_WEBHOOK_URL"), WebhookSecret: Secret(get("TRUST_WEBHOOK_SECRET"))}
	for _, field := range []struct {
		name  string
		value string
	}{{"INTERNAL_API_KEY", string(c.InternalAPIKey)}, {"DATABASE_URL", string(c.DatabaseURL)}, {"OP_PRIVATE_KEY", string(c.PrivateKey)}, {"OP_KEY_ID", c.KeyID}, {"OP_CLIENT_WALLET_ADDRESS", c.ClientWallet}, {"PUBLIC_BASE_URL", c.PublicBaseURL}} {
		if strings.TrimSpace(field.value) == "" {
			return Config{}, fmt.Errorf("%s is required", field.name)
		}
	}
	if len(c.InternalAPIKey) < 32 {
		return Config{}, errors.New("INTERNAL_API_KEY must contain at least 32 bytes")
	}
	if _, err := ParsePrivateKey(c.PrivateKey); err != nil {
		return Config{}, err
	}
	u, err := url.Parse(string(c.DatabaseURL))
	if err != nil || (u.Scheme != "postgres" && u.Scheme != "postgresql") || u.Hostname() == "" || u.Path == "" {
		return Config{}, errors.New("DATABASE_URL must be a PostgreSQL connection URL")
	}
	for _, field := range []struct{ name, value string }{{"PUBLIC_BASE_URL", c.PublicBaseURL}, {"OP_CLIENT_WALLET_ADDRESS", c.ClientWallet}} {
		if !HTTPSURL(field.value) {
			return Config{}, fmt.Errorf("%s must be an absolute HTTPS URL without credentials or fragment", field.name)
		}
	}
	c.PoolWallets = split(get("POOL_WALLET_ADDRESSES"))
	c.AllowedReturnPrefixes = split(get("ALLOWED_RETURN_URL_PREFIXES"))
	if len(c.AllowedReturnPrefixes) == 0 {
		return Config{}, errors.New("ALLOWED_RETURN_URL_PREFIXES is required")
	}
	for _, v := range append(append([]string{}, c.PoolWallets...), c.AllowedReturnPrefixes...) {
		if !HTTPSURL(v) {
			return Config{}, errors.New("wallet and return URL lists must contain absolute HTTPS URLs")
		}
	}
	if c.SimPayerWallet != "" && !HTTPSURL(c.SimPayerWallet) {
		return Config{}, errors.New("SIM_PAYER_WALLET_ADDRESS must be an absolute HTTPS URL")
	}
	for _, w := range c.PoolWallets {
		if w == c.SimPayerWallet {
			return Config{}, errors.New("sim payer wallet cannot also be a collection wallet")
		}
	}
	if s := get("SIM_ENABLED"); s != "" {
		c.SimEnabled, err = strconv.ParseBool(s)
		if err != nil {
			return Config{}, errors.New("SIM_ENABLED must be a boolean")
		}
	}
	if c.SimEnabled {
		n, ok := new(big.Int).SetString(c.SimMaxAmount, 10)
		if !ok || n.Sign() <= 0 || n.String() != c.SimMaxAmount {
			return Config{}, errors.New("SIM_MAX_AMOUNT must be a positive canonical integer")
		}
		if c.SimPayerWallet == "" || !HTTPSURL(c.WebhookURL) || len(c.WebhookSecret) < 32 {
			return Config{}, errors.New("simulator requires SIM_PAYER_WALLET_ADDRESS, HTTPS TRUST_WEBHOOK_URL and a TRUST_WEBHOOK_SECRET of at least 32 bytes")
		}
	}
	c.InteractionTimeout, err = seconds(get("INTERACTION_TIMEOUT_SECONDS"), 600)
	if err != nil {
		return Config{}, errors.New("INTERACTION_TIMEOUT_SECONDS must be between 1 and 86400")
	}
	c.PollInterval, err = seconds(get("POLL_INTERVAL_SECONDS"), 2)
	if err != nil {
		return Config{}, errors.New("POLL_INTERVAL_SECONDS must be between 1 and 86400")
	}
	return c, nil
}
func split(s string) []string {
	var out []string
	seen := map[string]bool{}
	for _, v := range strings.Split(s, ",") {
		v = strings.TrimSpace(v)
		if v != "" && !seen[v] {
			out = append(out, v)
			seen[v] = true
		}
	}
	return out
}
func seconds(s string, fallback int64) (time.Duration, error) {
	n := fallback
	var err error
	if s != "" {
		n, err = strconv.ParseInt(s, 10, 64)
	}
	if err != nil || n <= 0 || n > 86400 {
		return 0, errors.New("invalid duration")
	}
	return time.Duration(n) * time.Second, nil
}

// HTTPSURL checks URL syntax; outbound requests also need DNS and IP filtering.
func HTTPSURL(s string) bool {
	u, e := url.Parse(s)
	return e == nil && u.Scheme == "https" && u.Hostname() != "" && u.User == nil && u.Fragment == "" && u.Opaque == ""
}

// ParsePrivateKey accepts a PEM PKCS#8 Ed25519 private key.
func ParsePrivateKey(s Secret) (ed25519.PrivateKey, error) {
	b, rest := pem.Decode([]byte(s))
	if b == nil || b.Type != "PRIVATE KEY" || len(strings.TrimSpace(string(rest))) != 0 {
		return nil, errors.New("OP_PRIVATE_KEY must be one PEM PKCS#8 Ed25519 private key")
	}
	k, e := x509.ParsePKCS8PrivateKey(b.Bytes)
	if e != nil {
		return nil, errors.New("OP_PRIVATE_KEY is invalid")
	}
	key, ok := k.(ed25519.PrivateKey)
	if !ok {
		return nil, errors.New("OP_PRIVATE_KEY must be Ed25519")
	}
	return key, nil
}

// ReturnURLAllowed checks the origin before matching configured URL prefixes.
func (c Config) ReturnURLAllowed(s string) bool {
	if !HTTPSURL(s) {
		return false
	}
	u, _ := url.Parse(s)
	for _, prefix := range c.AllowedReturnPrefixes {
		p, _ := url.Parse(prefix)
		if u.Scheme == p.Scheme && strings.EqualFold(u.Host, p.Host) && strings.HasPrefix(s, prefix) {
			return true
		}
	}
	return false
}
