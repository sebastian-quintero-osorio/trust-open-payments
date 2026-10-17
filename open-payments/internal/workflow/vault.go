// Package workflow provides encrypted grant state and durable work coordination.
package workflow

import (
	"bytes"
	"crypto/aes"
	"crypto/cipher"
	"crypto/hkdf"
	"crypto/rand"
	"crypto/sha256"
	"encoding/gob"
	"encoding/json"
	"errors"

	"github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/config"
)

// Vault encrypts credentials with authenticated, record-bound AES-256-GCM.
// Its key is domain-separated from the existing Ed25519 seed via HKDF.
type Vault struct{ aead cipher.AEAD }

// NewVault derives an encryption key without adding an undocumented secret setting.
// Replacing OP_PRIVATE_KEY requires migrating encrypted state with the old key.
func NewVault(secret config.Secret) (*Vault, error) {
	key, e := config.ParsePrivateKey(secret)
	if e != nil {
		return nil, e
	}
	derived, e := hkdf.Key(sha256.New, key.Seed(), nil, "trust/op-service/vault/v1", 32)
	if e != nil {
		return nil, e
	}
	block, e := aes.NewCipher(derived)
	if e != nil {
		return nil, e
	}
	aead, e := cipher.NewGCM(block)
	if e != nil {
		return nil, e
	}
	return &Vault{aead: aead}, nil
}

// Seal serializes internal credentials with gob so redacting JSON marshalers cannot
// erase token values. Plaintext is never persisted or returned to HTTP clients.
func (v *Vault) Seal(id string, value any) ([]byte, error) {
	var b bytes.Buffer
	if e := gob.NewEncoder(&b).Encode(value); e != nil {
		return nil, e
	}
	nonce := make([]byte, v.aead.NonceSize())
	if _, e := rand.Read(nonce); e != nil {
		return nil, e
	}
	return v.aead.Seal(nonce, nonce, b.Bytes(), []byte(id)), nil
}

// Open authenticates the record ID before decoding, preventing row substitution.
func (v *Vault) Open(id string, data []byte, value any) error {
	n := v.aead.NonceSize()
	if len(data) < n {
		return errors.New("encrypted state is truncated")
	}
	plain, e := v.aead.Open(nil, data[:n], data[n:], []byte(id))
	if e != nil {
		return errors.New("encrypted state authentication failed")
	}
	return gob.NewDecoder(bytes.NewReader(plain)).Decode(value)
}

func init() { gob.Register(map[string]any{}); gob.Register([]any{}); gob.Register(json.Number("")) }
