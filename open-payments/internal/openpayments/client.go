// Package openpayments adapts the official SDK, preserving opaque protocol URLs.
package openpayments

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	sdk "github.com/interledger/open-payments-go"
	"github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/config"
	"github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/domain"
)

// Error retains machine-readable upstream information while redacting bodies/URLs.
type Error struct {
	Status    int
	Code      string
	Uncertain bool
}

func (e *Error) Error() string { return "Open Payments request failed (" + e.Code + ")" }

// Amount is the Open Payments wire representation; value is always a string.
type Amount struct {
	Value      string `json:"value"`
	AssetCode  string `json:"assetCode"`
	AssetScale int    `json:"assetScale"`
}

// Wire converts Trust money to the protocol's camelCase representation.
func Wire(a domain.Amount) Amount { return Amount{a.Value, a.AssetCode, a.AssetScale} }

// Domain converts protocol money without losing precision.
func (a Amount) Domain() domain.Amount {
	return domain.Amount{Value: a.Value, AssetCode: a.AssetCode, AssetScale: a.AssetScale}
}

// Wallet contains the tenant-specific endpoints exactly as returned by its server.
type Wallet struct {
	ID             string  `json:"id"`
	PublicName     *string `json:"publicName"`
	AssetCode      string  `json:"assetCode"`
	AssetScale     int     `json:"assetScale"`
	AuthServer     string  `json:"authServer"`
	ResourceServer string  `json:"resourceServer"`
}

// Token contains credentials. Its fields redact themselves when logged or JSON encoded.
type Token struct {
	Value     config.Secret `json:"value"`
	Manage    config.Secret `json:"manage"`
	ExpiresIn int           `json:"expires_in"`
}

// Grant represents an immediate token or an interaction awaiting consent.
type Grant struct {
	AccessToken *Token `json:"access_token,omitempty"`
	Continue    struct {
		URI         config.Secret `json:"uri"`
		AccessToken struct {
			Value config.Secret `json:"value"`
		} `json:"access_token"`
		Wait int `json:"wait"`
	} `json:"continue"`
	Interact *struct {
		Redirect string `json:"redirect"`
		Finish   string `json:"finish"`
	} `json:"interact,omitempty"`
}

// Incoming represents an incoming payment including receiver-side settlement.
type Incoming struct {
	ID             string    `json:"id"`
	WalletAddress  string    `json:"walletAddress"`
	Completed      bool      `json:"completed"`
	IncomingAmount *Amount   `json:"incomingAmount,omitempty"`
	ReceivedAmount Amount    `json:"receivedAmount"`
	ExpiresAt      time.Time `json:"expiresAt"`
}

// Quote specifies exact send/receive amounts and its expiration.
type Quote struct {
	ID            string    `json:"id"`
	DebitAmount   Amount    `json:"debitAmount"`
	ReceiveAmount Amount    `json:"receiveAmount"`
	ExpiresAt     time.Time `json:"expiresAt"`
}

// Outgoing has no completed flag: settlement requires checking amounts and incoming.
type Outgoing struct {
	ID            string         `json:"id"`
	Failed        bool           `json:"failed"`
	DebitAmount   Amount         `json:"debitAmount"`
	SentAmount    Amount         `json:"sentAmount"`
	ReceiveAmount Amount         `json:"receiveAmount"`
	Metadata      map[string]any `json:"metadata"`
}

// Client uses official SDK Ed25519 signing with a constrained outbound transport.
type Client struct {
	signed       *sdk.AuthenticatedClient
	unsigned     *sdk.Client
	clientWallet string
}

// New accepts an injected trusted transport for local protocol tests; production
// passes nil and always uses DNS-pinned HTTPS with no redirects or proxies.
func New(c config.Config, transport *http.Client) (*Client, error) {
	if _, e := config.ParsePrivateKey(c.PrivateKey); e != nil {
		return nil, e
	}
	if transport == nil {
		transport = SafeHTTPClient()
	}
	copyClient := *transport
	copyClient.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	if copyClient.Timeout == 0 {
		copyClient.Timeout = 15 * time.Second
	}
	copyClient.Transport = limitedTransport{base: transport.Transport}
	signed, e := sdk.NewAuthenticatedClient(c.ClientWallet, string(c.PrivateKey), c.KeyID, sdk.WithHTTPClientAuthed(&copyClient))
	if e != nil {
		return nil, errors.New("open payments signing configuration invalid")
	}
	return &Client{signed: signed, unsigned: sdk.NewClient(sdk.WithHTTPClientUnauthed(&copyClient)), clientWallet: c.ClientWallet}, nil
}

type limitedTransport struct{ base http.RoundTripper }

func (t limitedTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	if e := validURL(r.URL.String()); e != nil {
		return nil, e
	}
	r.Header.Set("Accept", "application/json")
	base := t.base
	if base == nil {
		base = http.DefaultTransport
	}
	res, e := base.RoundTrip(r)
	if e != nil {
		return nil, e
	}
	res.Body = &limitedBody{ReadCloser: res.Body, remaining: 1 << 20}
	return res, nil
}

type limitedBody struct {
	io.ReadCloser
	remaining int64
}

func (b *limitedBody) Read(p []byte) (int, error) {
	if b.remaining <= 0 {
		return 0, errors.New("upstream body exceeds limit")
	}
	if int64(len(p)) > b.remaining {
		p = p[:b.remaining]
	}
	n, e := b.ReadCloser.Read(p)
	b.remaining -= int64(n)
	return n, e
}

// Resolve accepts HTTPS wallet addresses or payment pointers and validates discovery.
func (c *Client) Resolve(ctx context.Context, value string) (Wallet, error) {
	if strings.HasPrefix(value, "$") {
		value = "https://" + strings.TrimPrefix(value, "$")
	}
	if e := validURL(value); e != nil {
		return Wallet{}, e
	}
	w, e := c.unsigned.WalletAddress.Get(ctx, sdk.WalletAddressGetParams{URL: value})
	if e != nil {
		var se *sdk.OpenPaymentsClientError
		if errors.As(e, &se) && se.Status == 404 {
			return Wallet{}, &Error{Status: 422, Code: "wallet_not_found"}
		}
		return Wallet{}, &Error{Status: 502, Code: "upstream_error"}
	}
	if w.Id == nil || w.AuthServer == nil || w.ResourceServer == nil {
		return Wallet{}, &Error{Status: 502, Code: "upstream_error"}
	}
	out := Wallet{ID: *w.Id, PublicName: w.PublicName, AssetCode: w.AssetCode, AssetScale: int(w.AssetScale), AuthServer: *w.AuthServer, ResourceServer: *w.ResourceServer}
	for _, u := range []string{out.ID, out.AuthServer, out.ResourceServer} {
		if validURL(u) != nil {
			return Wallet{}, &Error{Status: 502, Code: "upstream_error"}
		}
	}
	if out.AssetCode == "" || out.AssetScale < 0 || out.AssetScale > 255 {
		return Wallet{}, &Error{Status: 502, Code: "upstream_error"}
	}
	return out, nil
}
func (c *Client) request(ctx context.Context, method, target string, token config.Secret, payload any, result any) error {
	if e := validURL(target); e != nil {
		return e
	}
	var body []byte
	var e error
	if payload != nil {
		body, e = json.Marshal(payload)
		if e != nil {
			return &Error{Code: "invalid_request", Status: 400}
		}
	}
	req, e := http.NewRequestWithContext(ctx, method, target, bytes.NewReader(body))
	if e != nil {
		return &Error{Code: "invalid_url", Status: 422}
	}
	if token != "" {
		req.Header.Set("Authorization", "GNAP "+string(token))
	}
	res, e := c.signed.DoSigned(req)
	if e != nil {
		return &Error{Code: "upstream_error", Status: 502, Uncertain: method == http.MethodPost}
	}
	defer func() { _ = res.Body.Close() }()
	if res.StatusCode < 200 || res.StatusCode >= 300 {
		var envelope struct {
			Error json.RawMessage `json:"error"`
		}
		b, _ := io.ReadAll(io.LimitReader(res.Body, 4096))
		code := "upstream_error"
		if json.Unmarshal(b, &envelope) == nil {
			var obj struct {
				Code string `json:"code"`
			}
			if json.Unmarshal(envelope.Error, &obj) == nil && obj.Code != "" {
				code = obj.Code
			} else {
				var text string
				if json.Unmarshal(envelope.Error, &text) == nil {
					code = text
				}
			}
		}
		return &Error{Status: res.StatusCode, Code: code, Uncertain: res.StatusCode >= 500 && method == http.MethodPost}
	}
	if result != nil {
		if e = json.NewDecoder(res.Body).Decode(result); e != nil {
			return &Error{Code: "upstream_error", Status: 502, Uncertain: method == http.MethodPost}
		}
	}
	return nil
}

// Access identifies a wallet and its least-privilege grant permissions.
func Access(w Wallet, kind string, actions []string) map[string]any {
	return map[string]any{"type": kind, "identifier": w.ID, "actions": actions}
}

// RequestGrant uses the wallet's authServer without modifying its tenant path.
func (c *Client) RequestGrant(ctx context.Context, w Wallet, access map[string]any, interact any) (Grant, error) {
	payload := map[string]any{"client": c.clientWallet, "access_token": map[string]any{"access": []any{access}}}
	if interact != nil {
		payload["interact"] = interact
	}
	var g Grant
	e := c.request(ctx, "POST", w.AuthServer, "", payload, &g)
	if e == nil && g.AccessToken == nil && g.Interact == nil {
		return Grant{}, &Error{Code: "upstream_error", Status: 502}
	}
	return g, e
}

// NonInteractiveGrant requests incoming or quote permissions without interaction.
func (c *Client) NonInteractiveGrant(ctx context.Context, w Wallet, kind string, actions []string) (Grant, error) {
	if kind != "incoming-payment" && kind != "quote" {
		return Grant{}, &Error{Code: "invalid_request", Status: 400}
	}
	g, e := c.RequestGrant(ctx, w, Access(w, kind, actions), nil)
	if e == nil && (g.AccessToken == nil || g.AccessToken.Value == "") {
		return Grant{}, &Error{Code: "upstream_error", Status: 502}
	}
	return g, e
}
func resource(base, kind string) string { return strings.TrimRight(base, "/") + "/" + kind }

// CreateIncoming creates fixed-receive or unbounded fixed-send incoming payments.
func (c *Client) CreateIncoming(ctx context.Context, w Wallet, t config.Secret, amount *Amount, expiry time.Time, metadata map[string]any) (Incoming, error) {
	if amount != nil {
		if e := amount.Domain().Validate(true); e != nil {
			return Incoming{}, &Error{Code: "InvalidAmount", Status: 422}
		}
	}
	p := map[string]any{"walletAddress": w.ID, "expiresAt": expiry.UTC().Format(time.RFC3339), "metadata": metadata}
	if amount != nil {
		p["incomingAmount"] = amount
	}
	var result Incoming
	e := c.request(ctx, "POST", resource(w.ResourceServer, "incoming-payments"), t, p, &result)
	if e == nil && !SameOrigin(result.ID, w.ResourceServer) {
		return Incoming{}, &Error{Status: 502, Code: "upstream_error", Uncertain: true}
	}
	return result, e
}

// GetIncoming reads a private incoming payment.
func (c *Client) GetIncoming(ctx context.Context, target string, t config.Secret) (Incoming, error) {
	var i Incoming
	e := c.request(ctx, "GET", target, t, nil, &i)
	return i, e
}

// CompleteIncoming releases fixed-send funds to the receiving wallet balance.
func (c *Client) CompleteIncoming(ctx context.Context, target string, t config.Secret) (Incoming, error) {
	var i Incoming
	e := c.request(ctx, "POST", resource(target, "complete"), t, nil, &i)
	return i, e
}

// CreateQuote supports fixed-receive (nil debit) and fixed-send (explicit debit).
func (c *Client) CreateQuote(ctx context.Context, w Wallet, t config.Secret, incoming string, debit *Amount) (Quote, error) {
	p := map[string]any{"walletAddress": w.ID, "receiver": incoming, "method": "ilp"}
	if debit != nil {
		if e := debit.Domain().Validate(true); e != nil {
			return Quote{}, &Error{Status: 422, Code: "InvalidAmount"}
		}
		p["debitAmount"] = debit
	}
	var q Quote
	e := c.request(ctx, "POST", resource(w.ResourceServer, "quotes"), t, p, &q)
	if e == nil && (!SameOrigin(q.ID, w.ResourceServer) || q.DebitAmount.Domain().Validate(true) != nil || q.ReceiveAmount.Domain().Validate(true) != nil) {
		return Quote{}, &Error{Status: 502, Code: "upstream_error"}
	}
	return q, e
}

// GetQuote reads an existing quote.
func (c *Client) GetQuote(ctx context.Context, target string, t config.Secret) (Quote, error) {
	var q Quote
	e := c.request(ctx, "GET", target, t, nil, &q)
	return q, e
}

// CreateOutgoing sends exactly the supplied quote or debit amount, without retries.
func (c *Client) CreateOutgoing(ctx context.Context, w Wallet, t config.Secret, quote, incoming string, debit *Amount, metadata map[string]any) (Outgoing, error) {
	p := map[string]any{"walletAddress": w.ID, "metadata": metadata}
	if quote != "" {
		p["quoteId"] = quote
	} else {
		if debit == nil || debit.Domain().Validate(true) != nil {
			return Outgoing{}, &Error{Status: 422, Code: "InvalidAmount"}
		}
		p["incomingPayment"] = incoming
		p["debitAmount"] = debit
	}
	var o Outgoing
	e := c.request(ctx, "POST", resource(w.ResourceServer, "outgoing-payments"), t, p, &o)
	if e == nil && !SameOrigin(o.ID, w.ResourceServer) {
		return Outgoing{}, &Error{Status: 502, Code: "upstream_error", Uncertain: true}
	}
	return o, e
}

// GetOutgoing reads progress; no automatic POST retries are performed.
func (c *Client) GetOutgoing(ctx context.Context, target string, t config.Secret) (Outgoing, error) {
	var o Outgoing
	e := c.request(ctx, "GET", target, t, nil, &o)
	return o, e
}

// SameOrigin prevents resource IDs from forwarding credentials to another host.
func SameOrigin(a, b string) bool {
	u, e := url.Parse(a)
	v, f := url.Parse(b)
	return e == nil && f == nil && config.HTTPSURL(a) && config.HTTPSURL(b) && strings.EqualFold(u.Host, v.Host)
}
