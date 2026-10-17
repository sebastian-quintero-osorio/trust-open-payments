package application

import (
	"context"
	"encoding/json"
	"github.com/google/uuid"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func fastAPIReceiver(t *testing.T, secret string) string {
	t.Helper()
	python := os.Getenv("TRUST_RECEIVER_PYTHON")
	if python == "" {
		if os.Getenv("REQUIRE_INTEGRATION") == "1" {
			t.Fatal("TRUST_RECEIVER_PYTHON is required for FastAPI integration")
		}
		t.Skip("set TRUST_RECEIVER_PYTHON to run the FastAPI contract integration")
	}
	listener, e := net.Listen("tcp", "127.0.0.1:0")
	if e != nil {
		t.Fatal(e)
	}
	address := listener.Addr().String()
	file, e := listener.(*net.TCPListener).File()
	if e != nil {
		_ = listener.Close()
		t.Fatal(e)
	}
	_ = listener.Close()
	defer func() { _ = file.Close() }()
	command := exec.Command(python, filepath.Join("..", "..", "tests", "trust_receiver.py"), "3")
	command.Env = append(os.Environ(), "TRUST_FIXTURE_SECRET="+secret, "TRUST_FIXTURE_DB="+filepath.Join(t.TempDir(), "trust.sqlite"))
	command.ExtraFiles = []*os.File{file}
	if e = command.Start(); e != nil {
		t.Fatal(e)
	}
	done := make(chan error, 1)
	go func() { done <- command.Wait() }()
	finished := false
	t.Cleanup(func() {
		if !finished {
			_ = command.Process.Kill()
			<-done
		}
	})
	base := "http://" + address
	client := &http.Client{Timeout: time.Second}
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		response, e := client.Get(base + "/ready")
		if e == nil {
			code := response.StatusCode
			_ = response.Body.Close()
			if code == http.StatusOK {
				return base
			}
		}
		select {
		case e := <-done:
			finished = true
			t.Fatal("FastAPI fixture exited", e)
		default:
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatal("FastAPI fixture did not start")
	return ""
}
func TestWebhookIntegrationWithFastAPI(t *testing.T) {
	a := newApp(t)
	base := fastAPIReceiver(t, string(a.cfg.WebhookSecret))
	a.App.Config.WebhookURL = base + "/api/v1/webhooks/puntored/payments"
	a.App.WebhookHTTP = &http.Client{Timeout: time.Second}
	a.authorizeAll(t)
	account := a.account(t)
	w, sim := a.request(t, "POST", "/v1/sim/puntored/qr-payments", map[string]any{"collection_account_id": account, "amount": amount("1234")}, uuid.NewString())
	if w.Code != 201 {
		t.Fatal(w.Code, w.Body.String())
	}
	a.drive(t, "sim_payment", sim["id"].(string), "completed")
	client := &http.Client{Timeout: time.Second}
	response, e := client.Get(base + "/events")
	if e != nil {
		t.Fatal(e)
	}
	var result struct {
		Items []json.RawMessage `json:"items"`
	}
	e = json.NewDecoder(response.Body).Decode(&result)
	_ = response.Body.Close()
	if e != nil || len(result.Items) != 1 {
		t.Fatal("FastAPI did not persist webhook", e)
	}
	var raw []byte
	if e = a.App.DB.Pool.QueryRow(context.Background(), "SELECT raw_body FROM webhook_outbox WHERE resource_id=$1", sim["id"]).Scan(&raw); e != nil {
		t.Fatal(e)
	}
	req, e := http.NewRequest("POST", a.App.Config.WebhookURL, strings.NewReader(string(raw)))
	if e != nil {
		t.Fatal(e)
	}
	req.Header.Set("X-Puntored-Signature", WebhookSignature(string(a.cfg.WebhookSecret), time.Now().Unix(), raw))
	duplicate, e := client.Do(req)
	if e != nil {
		t.Fatal(e)
	}
	_ = duplicate.Body.Close()
	if duplicate.StatusCode != http.StatusOK {
		t.Fatal("duplicate not accepted")
	}
	response, e = client.Get(base + "/events")
	if e != nil {
		t.Fatal(e)
	}
	e = json.NewDecoder(response.Body).Decode(&result)
	_ = response.Body.Close()
	if e != nil || len(result.Items) != 1 {
		t.Fatal("FastAPI duplicated event", e)
	}
	req, e = http.NewRequest("POST", a.App.Config.WebhookURL, strings.NewReader(string(raw)))
	if e != nil {
		t.Fatal(e)
	}
	bad, e := client.Do(req)
	if e != nil {
		t.Fatal(e)
	}
	_ = bad.Body.Close()
	if bad.StatusCode != http.StatusUnauthorized {
		t.Fatal("unsigned webhook accepted")
	}
}
