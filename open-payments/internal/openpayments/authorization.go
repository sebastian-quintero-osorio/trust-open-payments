package openpayments

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"errors"
	"strings"

	"github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/config"
)

// NewNonce generates an unpredictable callback nonce independent of resource IDs.
func NewNonce() (string, error) {
	b := make([]byte, 32)
	if _, e := rand.Read(b); e != nil {
		return "", e
	}
	return base64.RawURLEncoding.EncodeToString(b), nil
}

// InteractiveGrant requests outgoing access, optionally with an exact debit limit.
func (c *Client) InteractiveGrant(ctx context.Context, w Wallet, finish, nonce string, debit *Amount, incoming string) (Grant, error) {
	if validURL(finish) != nil {
		return Grant{}, &Error{Status: 422, Code: "invalid_url"}
	}
	access := Access(w, "outgoing-payment", []string{"create", "read", "list"})
	if debit != nil {
		if e := debit.Domain().Validate(true); e != nil {
			return Grant{}, e
		}
		access["limits"] = map[string]any{"debitAmount": debit, "receiver": incoming}
	}
	interact := map[string]any{"start": []string{"redirect"}, "finish": map[string]any{"method": "redirect", "uri": finish, "nonce": nonce, "hash_method": "sha-256"}}
	g, e := c.RequestGrant(ctx, w, access, interact)
	if e == nil && (g.Interact == nil || validURL(g.Interact.Redirect) != nil || g.Interact.Finish == "" || g.Continue.URI == "" || g.Continue.AccessToken.Value == "") {
		return Grant{}, &Error{Status: 502, Code: "upstream_error"}
	}
	return g, e
}

// VerifyFinish accepts standard padded base64 and unpadded base64url, comparing
// the digest in constant time and retaining the exact authServer tenant URL.
func VerifyFinish(clientNonce, serverNonce, ref, authServer, supplied string) bool {
	if clientNonce == "" || serverNonce == "" || ref == "" {
		return false
	}
	var actual []byte
	var e error
	for _, enc := range []*base64.Encoding{base64.StdEncoding, base64.RawURLEncoding} {
		actual, e = enc.DecodeString(supplied)
		if e == nil {
			break
		}
	}
	if e != nil {
		return false
	}
	expected := sha256.Sum256([]byte(strings.Join([]string{clientNonce, serverNonce, ref, authServer}, "\n")))
	return subtle.ConstantTimeCompare(expected[:], actual) == 1
}

// ContinueGrant uses the opaque continuation URL exactly as issued.
func (c *Client) ContinueGrant(ctx context.Context, g Grant, ref string) (Grant, error) {
	var result Grant
	e := c.request(ctx, "POST", string(g.Continue.URI), g.Continue.AccessToken.Value, map[string]string{"interact_ref": ref}, &result)
	if e == nil && (result.AccessToken == nil || result.AccessToken.Value == "" || result.AccessToken.Manage == "") {
		return Grant{}, &Error{Status: 502, Code: "upstream_error"}
	}
	return result, e
}

// RotateToken uses the management URI verbatim, including for expired tokens.
func (c *Client) RotateToken(ctx context.Context, t Token) (Token, error) {
	var result struct {
		AccessToken Token `json:"access_token"`
	}
	e := c.request(ctx, "POST", string(t.Manage), t.Value, nil, &result)
	if e == nil && (result.AccessToken.Value == "" || result.AccessToken.Manage == "") {
		return Token{}, &Error{Status: 502, Code: "upstream_error", Uncertain: true}
	}
	return result.AccessToken, e
}

// CancelGrant revokes the grant at its continuation URI, never a reconstructed URL.
func (c *Client) CancelGrant(ctx context.Context, g Grant) error {
	if g.Continue.URI == "" {
		return errors.New("missing grant continuation URI")
	}
	return c.request(ctx, "DELETE", string(g.Continue.URI), g.Continue.AccessToken.Value, nil, nil)
}

// CheckTokenOrigin verifies that an opaque management URL still belongs to its AS.
func CheckTokenOrigin(g Grant, authServer string) error {
	for _, target := range []config.Secret{g.Continue.URI} {
		if target != "" && !SameOrigin(string(target), authServer) {
			return &Error{Status: 502, Code: "upstream_error"}
		}
	}
	if g.AccessToken != nil && !SameOrigin(string(g.AccessToken.Manage), authServer) {
		return &Error{Status: 502, Code: "upstream_error"}
	}
	return nil
}
