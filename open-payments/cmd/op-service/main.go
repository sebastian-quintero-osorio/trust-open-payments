// Command op-service runs Trust's private Open Payments API.
package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/config"
	"github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/httpapi"
	"github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/persistence"
)

func main() {
	if e := run(); e != nil {
		slog.Error("service stopped", "reason", e.Error())
		os.Exit(1)
	}
}
func run() error {
	cfg, e := config.Load(os.Getenv)
	if e != nil {
		return e
	}
	root, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	ctx, cancel := context.WithTimeout(root, 15*time.Second)
	defer cancel()
	store, e := persistence.Open(ctx, string(cfg.DatabaseURL))
	if e != nil {
		return e
	}
	defer store.Pool.Close()
	if e = store.Migrate(ctx); e != nil {
		return errors.New("database migration failed")
	}
	api, e := httpapi.New(cfg, store)
	if e != nil {
		return errors.New("API contract initialization failed")
	}
	srv := &http.Server{Addr: ":8080", Handler: api.Handler(), ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 15 * time.Second, WriteTimeout: 30 * time.Second, IdleTimeout: 60 * time.Second, MaxHeaderBytes: 16 << 10}
	done := make(chan error, 1)
	go func() { done <- srv.ListenAndServe() }()
	slog.Info("op-service started", "port", 8080)
	select {
	case e := <-done:
		if !errors.Is(e, http.ErrServerClosed) {
			return errors.New("HTTP listener failed")
		}
	case <-root.Done():
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if e = srv.Shutdown(shutdownCtx); e != nil {
			_ = srv.Close()
			return errors.New("graceful shutdown failed")
		}
	}
	return nil
}
