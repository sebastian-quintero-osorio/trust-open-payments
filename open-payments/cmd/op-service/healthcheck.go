package main

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"time"
)

// probeHealth is intentionally independent of secrets and database configuration.
func probeHealth() error {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	req, e := http.NewRequestWithContext(ctx, "GET", "http://127.0.0.1:8080/v1/health", nil)
	if e != nil {
		return errors.New("health probe failed")
	}
	res, e := http.DefaultClient.Do(req)
	if e != nil {
		return errors.New("health probe failed")
	}
	defer func() { _ = res.Body.Close() }()
	var health struct {
		Status string `json:"status"`
	}
	if e = json.NewDecoder(res.Body).Decode(&health); e != nil || res.StatusCode != http.StatusOK || health.Status != "ok" {
		return errors.New("service is not healthy")
	}
	return nil
}
