package application

import (
	"bytes"
	"encoding/gob"
	"testing"

	op "github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/openpayments"
)

// Jobs and resource flows persisted before the cleanup contain these extra fields.
// They must still decode so an upgrade can resume payments already in progress.
func TestFlowDecodesPersistedRemovedFields(t *testing.T) {
	legacy := struct {
		Kind, Stage, GrantID, AccountID, ExternalRef string
		QuoteGrant                                   op.Grant
		Amount                                       op.Amount
		Outgoing                                     op.Outgoing
	}{
		Kind: "transfer", Stage: "outgoing_uncertain", GrantID: "old-grant",
		AccountID: "account", ExternalRef: "sale:1",
		QuoteGrant: op.Grant{AccessToken: &op.Token{Value: "old-token"}},
		Amount:     op.Amount{Value: "1200", AssetCode: "COP", AssetScale: 2},
		Outgoing:   op.Outgoing{ID: "https://wallet.example/outgoing/1"},
	}
	var stored bytes.Buffer
	if err := gob.NewEncoder(&stored).Encode(legacy); err != nil {
		t.Fatal(err)
	}
	var restored Flow
	if err := gob.NewDecoder(&stored).Decode(&restored); err != nil {
		t.Fatal(err)
	}
	if restored.Kind != legacy.Kind || restored.Stage != legacy.Stage ||
		restored.AccountID != legacy.AccountID || restored.ExternalRef != legacy.ExternalRef ||
		restored.Amount != legacy.Amount || restored.Outgoing.ID != legacy.Outgoing.ID {
		t.Fatalf("persisted payment state changed: %+v", restored)
	}
}
