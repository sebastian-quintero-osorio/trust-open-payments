package application

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/domain"
	"github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/httpapi"
	"github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/persistence"
	"html/template"
	"math/big"
	"net"
	"net/http"
	"regexp"
	"strings"
	"unicode/utf8"
)

var qrTemplate = template.Must(template.New("qr").Parse(`<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Pagar con Trust</title><h1>{{.Name}}</h1><p>Bre-B simulado · {{.Asset}}</p><form method="post" action="{{.Action}}"><label>Monto <input name="amount" inputmode="decimal" required maxlength="100"></label><label>Banco <input name="payer_bank" maxlength="60"></label><input type="hidden" name="form_token" value="{{.Token}}"><button>Pagar</button></form></html>`))

func (s *Service) qrPage(w http.ResponseWriter, r *http.Request) {
	if !s.Config.SimEnabled {
		http.NotFound(w, r)
		return
	}
	id := r.PathValue("collection_account_id")
	if _, e := uuid.Parse(id); e != nil {
		http.NotFound(w, r)
		return
	}
	v, e := read(r.Context(), s.DB.Pool, id, "collection_account", false)
	if e != nil || status(v) != "active" {
		http.NotFound(w, r)
		return
	}
	token := newID()
	if _, e = s.DB.Pool.Exec(r.Context(), "INSERT INTO qr_forms(token,account_id) VALUES($1,$2)", token, id); e != nil {
		http.Error(w, "Servicio no disponible", http.StatusServiceUnavailable)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Header().Set("Content-Security-Policy", "default-src 'none'; form-action 'self'; frame-ancestors 'none'")
	_ = qrTemplate.Execute(w, map[string]any{"Name": v["display_name"], "Asset": v["asset_code"], "Action": "/qr/" + id + "/pay", "Token": token})
}

var majorAmount = regexp.MustCompile(`^[0-9]+(?:\.[0-9]+)?$`)

// MinorUnits parses major-unit form input with exact integer arithmetic.
func MinorUnits(text string, scale int) (string, error) {
	if !majorAmount.MatchString(text) || scale < 0 || scale > 255 || len(text) > 100 {
		return "", errAmount
	}
	parts := strings.SplitN(text, ".", 2)
	fraction := ""
	if len(parts) == 2 {
		fraction = parts[1]
	}
	if len(fraction) > scale {
		return "", errAmount
	}
	digits := parts[0] + fraction + strings.Repeat("0", scale-len(fraction))
	n, ok := new(big.Int).SetString(digits, 10)
	if !ok || n.Sign() <= 0 {
		return "", errAmount
	}
	return n.String(), nil
}

var errAmount = &formError{}

type formError struct{}

func (*formError) Error() string { return "invalid amount" }
func (s *Service) qrPay(w http.ResponseWriter, r *http.Request) {
	if !s.Config.SimEnabled {
		http.NotFound(w, r)
		return
	}
	id := r.PathValue("collection_account_id")
	if _, e := uuid.Parse(id); e != nil {
		http.NotFound(w, r)
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, 4096)
	if e := r.ParseForm(); e != nil {
		http.Error(w, "Formulario inválido", http.StatusBadRequest)
		return
	}
	for _, key := range []string{"amount", "payer_bank", "form_token"} {
		if len(r.PostForm[key]) > 1 {
			http.Error(w, "Formulario inválido", http.StatusBadRequest)
			return
		}
	}
	token := r.PostForm.Get("form_token")
	if _, e := uuid.Parse(token); e != nil {
		http.Error(w, "Formulario inválido", http.StatusBadRequest)
		return
	}
	v, e := read(r.Context(), s.DB.Pool, id, "collection_account", false)
	if e != nil || status(v) != "active" {
		http.NotFound(w, r)
		return
	}
	var account domain.CollectionAccount
	b, e := json.Marshal(v)
	if e != nil {
		http.Error(w, "Servicio no disponible", http.StatusServiceUnavailable)
		return
	}
	if e = json.Unmarshal(b, &account); e != nil {
		http.Error(w, "Servicio no disponible", http.StatusServiceUnavailable)
		return
	}
	value, e := MinorUnits(r.PostForm.Get("amount"), account.AssetScale)
	bank := r.PostForm.Get("payer_bank")
	if e != nil || utf8.RuneCountInString(bank) > 60 {
		http.Error(w, "Monto o banco inválido", http.StatusBadRequest)
		return
	}
	input := domain.CreateSimQrPaymentRequest{CollectionAccountID: id, Amount: domain.PositiveAmount(domain.Amount{Value: value, AssetCode: account.AssetCode, AssetScale: account.AssetScale}), PayerBank: &bank}
	raw, e := json.Marshal(input)
	if e != nil {
		http.Error(w, "Servicio no disponible", http.StatusServiceUnavailable)
		return
	}
	_, hash, e := httpapi.CanonicalJSON(raw)
	if e != nil {
		http.Error(w, "Formulario inválido", http.StatusBadRequest)
		return
	}
	reply, e := s.DB.Execute(r.Context(), "POST /qr/"+id+"/pay", token, hash, func(ctx context.Context, tx pgx.Tx) (persistence.Response, error) {
		var valid bool
		if e := tx.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM qr_forms WHERE token=$1 AND account_id=$2 AND used_at IS NULL AND expires_at>now())", token, id).Scan(&valid); e != nil {
			return persistence.Response{}, e
		}
		if !valid {
			return problem(400, "validation_error")
		}
		host, _, e := net.SplitHostPort(r.RemoteAddr)
		if e != nil {
			host = r.RemoteAddr
		}
		ip := sha256.Sum256([]byte(host))
		var count int
		e = tx.QueryRow(ctx, `INSERT INTO qr_rate_limits(ip_hash,minute,requests) VALUES($1,date_trunc('minute',now()),1) ON CONFLICT(ip_hash,minute) DO UPDATE SET requests=qr_rate_limits.requests+1 WHERE qr_rate_limits.requests<5 RETURNING requests`, ip[:]).Scan(&count)
		if e == pgx.ErrNoRows {
			return problem(429, "rate_limited")
		}
		if e != nil {
			return persistence.Response{}, e
		}
		result, e := s.startSim(ctx, tx, input)
		if e != nil || result.Status != 201 {
			return result, e
		}
		if _, e = tx.Exec(ctx, "UPDATE qr_forms SET used_at=now() WHERE token=$1", token); e != nil {
			return persistence.Response{}, e
		}
		return result, nil
	})
	if e != nil {
		http.Error(w, "Pago no disponible; reintenta con el mismo formulario", http.StatusServiceUnavailable)
		return
	}
	if reply.Status != 201 {
		http.Error(w, "No fue posible enviar el pago", reply.Status)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	if reply.Replayed {
		w.Header().Set("Idempotent-Replayed", "true")
	}
	_, _ = w.Write([]byte(`<!doctype html><html lang="es"><meta charset="utf-8"><title>Trust</title><h1>Pago enviado</h1><p>La confirmación del recaudo está en proceso.</p></html>`))
}
