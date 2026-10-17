package application

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"github.com/jackc/pgx/v5"
	"io"
	"net/http"
	"strconv"
	"time"
)

// WebhookSignature signs exactly the stored raw bytes with a fresh delivery timestamp.
func WebhookSignature(secret string, timestamp int64, body []byte) string {
	t := strconv.FormatInt(timestamp, 10)
	mac := hmac.New(sha256.New, []byte(secret))
	_, _ = mac.Write([]byte(t + "."))
	_, _ = mac.Write(body)
	return "t=" + t + ",v1=" + hex.EncodeToString(mac.Sum(nil))
}
func (s *Service) queueWebhook(ctx context.Context, id string, f Flow) error {
	tx, e := s.DB.Pool.Begin(ctx)
	if e != nil {
		return e
	}
	defer func() { _ = tx.Rollback(ctx) }()
	v, e := read(ctx, tx, id, "sim_payment", true)
	if e != nil {
		return e
	}
	if status(v) != "processing" {
		return nil
	}
	account, e := read(ctx, tx, f.AccountID, "collection_account", false)
	if e != nil {
		return e
	}
	progress(v, f)
	eventID := newID()
	event := map[string]any{"event_id": eventID, "type": "qr_payment.received", "occurred_at": now(), "data": map[string]any{"collection_account_id": f.AccountID, "collection_wallet_address": account["wallet_address"], "amount": f.Amount.Domain(), "bre_b_reference": v["bre_b_reference"], "payer_bank": v["payer_bank"], "incoming_payment_url": f.Incoming.ID}}
	raw, e := json.Marshal(event)
	if e != nil {
		return e
	}
	if _, e = tx.Exec(ctx, "INSERT INTO webhook_outbox(resource_id,event_id,raw_body) VALUES($1,$2,$3) ON CONFLICT(resource_id) DO NOTHING", id, eventID, raw); e != nil {
		return e
	}
	if e = save(ctx, tx, id, v); e != nil {
		return e
	}
	return tx.Commit(ctx)
}

// DeliverWebhooks retries only notifications, never financial operations.
func (s *Service) DeliverWebhooks(ctx context.Context) error {
	rows, e := s.DB.Pool.Query(ctx, "SELECT resource_id::text FROM webhook_outbox WHERE status='queued' AND available_at<=now() ORDER BY available_at LIMIT 20")
	if e != nil {
		return e
	}
	ids := []string{}
	for rows.Next() {
		var id string
		if e = rows.Scan(&id); e != nil {
			rows.Close()
			return e
		}
		ids = append(ids, id)
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return e
	}
	for _, id := range ids {
		if e = s.deliverWebhook(ctx, id); e != nil {
			return e
		}
	}
	return nil
}
func (s *Service) deliverWebhook(ctx context.Context, id string) error {
	tx, e := s.DB.Pool.Begin(ctx)
	if e != nil {
		return e
	}
	defer func() { _ = tx.Rollback(ctx) }()
	v, e := read(ctx, tx, id, "sim_payment", true)
	if e != nil {
		return e
	}
	var raw []byte
	var attempts int
	e = tx.QueryRow(ctx, "SELECT raw_body,attempts FROM webhook_outbox WHERE resource_id=$1 AND status='queued' AND available_at<=now() FOR UPDATE SKIP LOCKED", id).Scan(&raw, &attempts)
	if e == pgx.ErrNoRows {
		return nil
	}
	if e != nil {
		return e
	}
	requestCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	req, e := http.NewRequestWithContext(requestCtx, "POST", s.Config.WebhookURL, bytes.NewReader(raw))
	if e != nil {
		return e
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Puntored-Signature", WebhookSignature(string(s.Config.WebhookSecret), time.Now().Unix(), raw))
	res, sendErr := s.WebhookHTTP.Do(req)
	delivered := false
	if sendErr == nil {
		delivered = res.StatusCode == http.StatusOK
		_, _ = io.Copy(io.Discard, io.LimitReader(res.Body, 4096))
		_ = res.Body.Close()
	}
	attempts++
	state := "queued"
	next := time.Now()
	if delivered {
		state = "delivered"
	} else {
		delays := []time.Duration{time.Second, 5 * time.Second, 30 * time.Second, 2 * time.Minute}
		if attempts > len(delays) {
			state = "exhausted"
		} else {
			next = next.Add(delays[attempts-1])
		}
	}
	if _, e = tx.Exec(ctx, "UPDATE webhook_outbox SET attempts=$1,status=$2,available_at=$3 WHERE resource_id=$4", attempts, state, next, id); e != nil {
		return e
	}
	var event any
	if e = json.Unmarshal(raw, &event); e != nil {
		return e
	}
	v["webhook_event"] = event
	if delivered {
		v["webhook_delivered_at"] = now()
	}
	if status(v) == "processing" {
		if e = s.changeState(ctx, tx, id, "sim_payment", v, "completed", ""); e != nil {
			return e
		}
		if e = s.assignSequence(ctx, tx, id, v); e != nil {
			return e
		}
		if accountID, ok := v["collection_account_id"].(string); ok {
			if e = s.updateLedger(ctx, tx, accountID); e != nil {
				return e
			}
		}
	} else {
		if e = save(ctx, tx, id, v); e != nil {
			return e
		}
	}
	var deliveredAt any
	if delivered {
		deliveredAt = v["webhook_delivered_at"]
	}
	if _, e = tx.Exec(ctx, "UPDATE sim_entries SET webhook_body=$1,webhook_delivered_at=COALESCE($2::timestamptz,webhook_delivered_at) WHERE resource_id=$3", raw, deliveredAt, id); e != nil {
		return fmt.Errorf("persist webhook outcome: %w", e)
	}
	return tx.Commit(ctx)
}
