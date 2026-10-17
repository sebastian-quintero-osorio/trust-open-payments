package main

import (
	"bytes"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/x509"
	"encoding/pem"
	"github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/testutil"
	"io"
	"net/http"
	"os"
	"os/exec"
	"strings"
	"syscall"
	"testing"
	"time"
)

func TestServiceProcess(t *testing.T) {
	if os.Getenv("OP_SERVICE_TEST_CHILD") == "1" {
		if e := run(); e != nil {
			t.Fatal(e)
		}
		return
	}
	dsn := os.Getenv("TEST_DATABASE_URL")
	if dsn == "" {
		if os.Getenv("REQUIRE_INTEGRATION") == "1" {
			t.Fatal("TEST_DATABASE_URL required")
		}
		t.Skip("PostgreSQL integration requires TEST_DATABASE_URL")
	}
	dsn = testutil.DatabaseURL(t)
	_, key, e := ed25519.GenerateKey(rand.Reader)
	if e != nil {
		t.Fatal(e)
	}
	der, e := x509.MarshalPKCS8PrivateKey(key)
	if e != nil {
		t.Fatal(e)
	}
	secret := string(pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: der}))
	exe, e := os.Executable()
	if e != nil {
		t.Fatal(e)
	}
	cmd := exec.Command(exe, "-test.run=^TestServiceProcess$")
	cmd.Env = append(os.Environ(), "OP_SERVICE_TEST_CHILD=1", "INTERNAL_API_KEY="+strings.Repeat("s", 32), "DATABASE_URL="+dsn, "OP_PRIVATE_KEY="+secret, "OP_KEY_ID=test", "OP_CLIENT_WALLET_ADDRESS=https://wallet.example/trust", "PUBLIC_BASE_URL=https://pay.example", "ALLOWED_RETURN_URL_PREFIXES=https://app.example/", "SIM_ENABLED=false")
	var output bytes.Buffer
	cmd.Stdout = &output
	cmd.Stderr = &output
	if e = cmd.Start(); e != nil {
		t.Fatal(e)
	}
	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()
	finished := false
	t.Cleanup(func() {
		if !finished {
			_ = cmd.Process.Kill()
			<-done
		}
	})
	client := &http.Client{Timeout: time.Second}
	deadline := time.Now().Add(10 * time.Second)
	ready := false
	for time.Now().Before(deadline) {
		res, err := client.Get("http://127.0.0.1:8080/v1/health")
		if err == nil {
			b, readErr := io.ReadAll(res.Body)
			closeErr := res.Body.Close()
			if readErr != nil || closeErr != nil {
				t.Fatal("health body unreadable")
			}
			if res.StatusCode == 200 && strings.Contains(string(b), `"status":"ok"`) {
				ready = true
				break
			}
		}
		select {
		case e := <-done:
			finished = true
			t.Fatalf("service exited early: %v %s", e, output.String())
		default:
		}
		time.Sleep(25 * time.Millisecond)
	}
	if !ready {
		t.Fatal("service failed to become ready")
	}
	if e = cmd.Process.Signal(syscall.SIGTERM); e != nil {
		t.Fatal(e)
	}
	select {
	case e = <-done:
		finished = true
		if e != nil {
			t.Fatal("service shutdown failed", e, output.String())
		}
	case <-time.After(12 * time.Second):
		t.Fatal("shutdown timed out")
	}
	for _, value := range []string{secret, strings.Repeat("s", 32), dsn} {
		if strings.Contains(output.String(), value) {
			t.Fatal("secret found in process logs")
		}
	}
}
