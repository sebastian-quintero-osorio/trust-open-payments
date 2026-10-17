// Package domain defines financial value validation independent of HTTP and storage.
package domain

import (
	"errors"
	"math/big"
	"regexp"
)

var unsignedInteger = regexp.MustCompile(`^(0|[1-9][0-9]*)$`)

// Amount represents money in integer minor units; it never passes through floats.
type Amount struct {
	Value      string `json:"value"`
	AssetCode  string `json:"asset_code"`
	AssetScale int    `json:"asset_scale"`
}

// Validate checks the representation and optional positivity constraint.
func (a Amount) Validate(positive bool) error {
	if !unsignedInteger.MatchString(a.Value) || a.AssetCode == "" || a.AssetScale < 0 || a.AssetScale > 255 {
		return errors.New("invalid amount")
	}
	if positive && a.Value == "0" {
		return errors.New("amount must be positive")
	}
	return nil
}

// Compare compares values only when both currency and scale match.
func (a Amount) Compare(b Amount) (int, error) {
	if a.AssetCode != b.AssetCode || a.AssetScale != b.AssetScale {
		return 0, errors.New("asset mismatch")
	}
	if e := a.Validate(false); e != nil {
		return 0, e
	}
	if e := b.Validate(false); e != nil {
		return 0, e
	}
	x, _ := new(big.Int).SetString(a.Value, 10)
	y, _ := new(big.Int).SetString(b.Value, 10)
	return x.Cmp(y), nil
}
