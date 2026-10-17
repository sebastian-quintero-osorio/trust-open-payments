// Package contract embeds the authoritative OpenAPI contract.
package contract

import _ "embed"

// OpenAPI is immutable runtime input for JSON Schema validation.
//
//go:embed op-service.openapi.yaml
var OpenAPI []byte
