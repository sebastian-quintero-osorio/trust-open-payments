// Package httpapi implements the internal API's security and contract boundary.
package httpapi

import (
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"io"
	"mime"
	"net/http"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/config"
	"github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/persistence"
)

// Request is validated input for a business operation.
type Request struct {
	HTTP *http.Request
	Body json.RawMessage
}

// Operation executes local business mutations in the idempotency transaction.
type Operation func(context.Context, pgx.Tx, Request) (persistence.Response, error)

// Server binds operation implementations to contract routes.
type Server struct {
	Config            config.Config
	Store             *persistence.Store
	Contract          *Contract
	Operations        map[string]Operation
	BrowserOperations map[string]http.HandlerFunc
}

// New constructs the API without registering unfinished financial operations.
func New(c config.Config, s *persistence.Store) (*Server, error) {
	ct, e := LoadContract()
	if e != nil {
		return nil, e
	}
	return &Server{Config: c, Store: s, Contract: ct, Operations: map[string]Operation{}}, nil
}

// Handler enforces authentication before validation, idempotency, and dispatch.
func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	for _, r := range s.Contract.Routes {
		route := r
		mux.HandleFunc(route.Method+" "+route.Path, func(w http.ResponseWriter, r *http.Request) { s.serve(w, r, route) })
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("Referrer-Policy", "no-referrer")
		w.Header().Set("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
		public := r.Method == http.MethodGet && (r.URL.Path == "/v1/health" || strings.HasPrefix(r.URL.Path, "/v1/interact/finish/") || strings.HasPrefix(r.URL.Path, "/qr/")) || r.Method == http.MethodPost && strings.HasPrefix(r.URL.Path, "/qr/")
		if strings.HasPrefix(r.URL.Path, "/v1/") && !public {
			provided := sha256.Sum256([]byte(r.Header.Get("X-Internal-Api-Key")))
			expected := sha256.Sum256([]byte(s.Config.InternalAPIKey))
			if len(s.Config.InternalAPIKey) == 0 || len(r.Header.Values("X-Internal-Api-Key")) != 1 || subtle.ConstantTimeCompare(provided[:], expected[:]) != 1 {
				write(w, ErrorResponse(401, "unauthorized", "Invalid internal API key", false))
				return
			}
		}
		mux.ServeHTTP(w, r)
	})
}
func (s *Server) serve(w http.ResponseWriter, r *http.Request, route Route) {
	ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
	defer cancel()
	r = r.WithContext(ctx)
	if handler, ok := s.BrowserOperations[route.OperationID]; ok {
		handler(w, r)
		return
	}
	if route.OperationID == "getHealth" {
		status := "ok"
		if s.Store != nil {
			ctx, cancel := context.WithTimeout(r.Context(), time.Second)
			defer cancel()
			if s.Store.Pool.Ping(ctx) != nil {
				status = "degraded"
			}
		}
		b, e := json.Marshal(map[string]string{"status": status, "version": "0.1.0", "client_wallet_address": s.Config.ClientWallet})
		if e != nil {
			write(w, ErrorResponse(503, "upstream_error", "Service unavailable", true))
			return
		}
		write(w, persistence.Response{Status: 200, Body: b, Headers: http.Header{"Content-Type": []string{"application/json"}}})
		return
	}
	parameterErr := validateParameters(r, route)
	if route.Method != "POST" || !strings.HasPrefix(route.Path, "/v1/") {
		if parameterErr != nil {
			write(w, ErrorResponse(400, "validation_error", "Invalid path or query parameters", false))
			return
		}
		s.invoke(w, r, route, nil, nil)
		return
	}
	key := r.Header.Get("Idempotency-Key")
	if len(r.Header.Values("Idempotency-Key")) != 1 || len(key) < 1 || len(key) > 255 || strings.TrimSpace(key) != key {
		write(w, ErrorResponse(400, "validation_error", "Idempotency-Key is required (1–255 bytes)", false))
		return
	}
	raw, e := io.ReadAll(http.MaxBytesReader(w, r.Body, 64<<10))
	if e != nil {
		write(w, ErrorResponse(400, "validation_error", "Request body exceeds limit or cannot be read", false))
		return
	}
	var invalid *persistence.Response
	setInvalid := func(message string) {
		response := ErrorResponse(400, "validation_error", message, false)
		invalid = &response
	}
	if parameterErr != nil {
		setInvalid("Invalid path or query parameters")
	}
	if len(raw) == 0 && !route.BodyRequired {
		raw = []byte("{}")
	} else {
		mt, _, e := mime.ParseMediaType(r.Header.Get("Content-Type"))
		if e != nil || mt != "application/json" {
			setInvalid("Content-Type must be application/json")
		}
	}
	canonical, hash, e := CanonicalJSON(raw)
	if e != nil {
		hash = sha256.Sum256(raw)
		setInvalid("Invalid JSON")
	}
	if s.Store == nil {
		write(w, ErrorResponse(503, "upstream_error", "Database unavailable", true))
		return
	}
	response, e := s.Store.Execute(r.Context(), route.Method+" "+r.URL.Path, key, hash, func(ctx context.Context, tx pgx.Tx) (persistence.Response, error) {
		if invalid != nil {
			return *invalid, nil
		}
		if route.Body != nil {
			if e := validateBody(route.Body, canonical); e != nil {
				return ErrorResponse(400, "validation_error", "Body does not match API contract", false), nil
			}
		}
		op, ok := s.Operations[route.OperationID]
		if !ok {
			return ErrorResponse(503, "upstream_error", "Operation unavailable", true), nil
		}
		return op(ctx, tx, Request{HTTP: r, Body: canonical})
	})
	if e != nil {
		switch {
		case errors.Is(e, persistence.ErrConflict):
			response = ErrorResponse(409, "idempotency_conflict", "Key already used with another body", false)
		case errors.Is(e, persistence.ErrInProgress):
			response = ErrorResponse(409, "request_in_progress", "Request is still in progress", true)
		default:
			response = ErrorResponse(503, "upstream_error", "Database unavailable", true)
		}
	}
	write(w, response)
}
func (s *Server) invoke(w http.ResponseWriter, r *http.Request, route Route, tx pgx.Tx, body []byte) {
	op, ok := s.Operations[route.OperationID]
	if !ok {
		write(w, ErrorResponse(503, "upstream_error", "Operation unavailable", true))
		return
	}
	res, e := op(r.Context(), tx, Request{HTTP: r, Body: body})
	if e != nil {
		res = ErrorResponse(503, "upstream_error", "Service unavailable", true)
	}
	write(w, res)
}

// ErrorResponse produces the contract error envelope without internal details.
func ErrorResponse(status int, code, message string, retryable bool) persistence.Response {
	b, _ := json.Marshal(struct {
		Error struct {
			Code      string `json:"code"`
			Message   string `json:"message"`
			Retryable bool   `json:"retryable"`
		} `json:"error"`
	}{Error: struct {
		Code      string `json:"code"`
		Message   string `json:"message"`
		Retryable bool   `json:"retryable"`
	}{code, message, retryable}})
	return persistence.Response{Status: status, Body: b, Headers: http.Header{"Content-Type": []string{"application/json"}}}
}
func write(w http.ResponseWriter, r persistence.Response) {
	for h, vs := range r.Headers {
		for _, v := range vs {
			w.Header().Add(h, v)
		}
	}
	if r.Replayed {
		w.Header().Set("Idempotent-Replayed", "true")
	}
	w.WriteHeader(r.Status)
	_, _ = w.Write(r.Body)
}
