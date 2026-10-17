package httpapi

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"

	"github.com/santhosh-tekuri/jsonschema/v6"
	contract "github.com/sebastian-quintero-osorio/trust-open-payments/open-payments"
	"go.yaml.in/yaml/v3"
)

// Contract provides request/response validation from the embedded OpenAPI 3.1 schemas.
type Contract struct {
	Schemas map[string]*jsonschema.Schema
	Routes  []Route
}

// Route contains contract metadata for HTTP dispatch and request validation.
type Route struct {
	Method       string
	Path         string
	OperationID  string
	Body         *jsonschema.Schema
	BodyRequired bool
	Parameters   []Parameter
}

// Parameter describes a path or query input in the contract.
type Parameter struct {
	Name     string         `json:"name"`
	In       string         `json:"in"`
	Required bool           `json:"required"`
	Schema   map[string]any `json:"schema"`
}

// LoadContract compiles all schemas, preserving integer and nullable definitions.
func LoadContract() (*Contract, error) {
	var doc map[string]any
	if e := yaml.Unmarshal(contract.OpenAPI, &doc); e != nil {
		return nil, e
	}
	b, e := json.Marshal(doc)
	if e != nil {
		return nil, e
	}
	if e = json.Unmarshal(b, &doc); e != nil {
		return nil, e
	}
	c := jsonschema.NewCompiler()
	c.AssertFormat()
	if e = c.AddResource("https://contract.trust.invalid/openapi", doc); e != nil {
		return nil, e
	}
	out := &Contract{Schemas: map[string]*jsonschema.Schema{}}
	for name := range doc["components"].(map[string]any)["schemas"].(map[string]any) {
		s, e := c.Compile("https://contract.trust.invalid/openapi#/components/schemas/" + name)
		if e != nil {
			return nil, e
		}
		out.Schemas[name] = s
	}
	for path, x := range doc["paths"].(map[string]any) {
		item := x.(map[string]any)
		for method, v := range item {
			if method == "parameters" {
				continue
			}
			op, ok := v.(map[string]any)
			if !ok {
				continue
			}
			id, _ := op["operationId"].(string)
			r := Route{Method: strings.ToUpper(method), Path: path, OperationID: id}
			if rb, ok := op["requestBody"].(map[string]any); ok {
				r.BodyRequired, _ = rb["required"].(bool)
				if _, ok := rb["content"].(map[string]any)["application/json"]; ok {
					s, e := c.Compile("https://contract.trust.invalid/openapi#/paths/" + strings.ReplaceAll(strings.ReplaceAll(path, "~", "~0"), "/", "~1") + "/" + method + "/requestBody/content/application~1json/schema")
					if e != nil {
						return nil, e
					}
					r.Body = s
				}
			}
			for _, src := range []map[string]any{item, op} {
				ps, _ := src["parameters"].([]any)
				for _, p := range ps {
					m := p.(map[string]any)
					if ref, ok := m["$ref"].(string); ok {
						parts := strings.Split(ref, "/")
						m = doc["components"].(map[string]any)["parameters"].(map[string]any)[parts[len(parts)-1]].(map[string]any)
					}
					b, e := json.Marshal(m)
					if e != nil {
						return nil, e
					}
					var param Parameter
					if e = json.Unmarshal(b, &param); e != nil {
						return nil, e
					}
					r.Parameters = append(r.Parameters, param)
				}
			}
			out.Routes = append(out.Routes, r)
		}
	}
	return out, nil
}

// CanonicalJSON sorts object keys, removes whitespace, preserves integer precision,
// and rejects duplicate keys and trailing JSON documents before hashing.
func CanonicalJSON(raw []byte) ([]byte, [32]byte, error) {
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	v, e := readValue(dec, 0)
	if e != nil {
		return nil, [32]byte{}, e
	}
	if _, e = dec.Token(); !errors.Is(e, io.EOF) {
		return nil, [32]byte{}, errors.New("trailing JSON data")
	}
	b, e := json.Marshal(v)
	if e != nil {
		return nil, [32]byte{}, e
	}
	return b, sha256.Sum256(b), nil
}
func readValue(d *json.Decoder, depth int) (any, error) {
	if depth > 64 {
		return nil, errors.New("JSON nesting exceeds limit")
	}
	t, e := d.Token()
	if e != nil {
		return nil, e
	}
	delim, ok := t.(json.Delim)
	if !ok {
		if n, ok := t.(json.Number); ok {
			return canonicalNumber(n)
		}
		return t, nil
	}
	switch delim {
	case '{':
		m := map[string]any{}
		for d.More() {
			key, e := d.Token()
			if e != nil {
				return nil, e
			}
			s, ok := key.(string)
			if !ok {
				return nil, errors.New("invalid object key")
			}
			if _, ok = m[s]; ok {
				return nil, errors.New("duplicate JSON key")
			}
			v, e := readValue(d, depth+1)
			if e != nil {
				return nil, e
			}
			m[s] = v
		}
		if _, e = d.Token(); e != nil {
			return nil, e
		}
		return m, nil
	case '[':
		a := []any{}
		for d.More() {
			v, e := readValue(d, depth+1)
			if e != nil {
				return nil, e
			}
			a = append(a, v)
		}
		if _, e = d.Token(); e != nil {
			return nil, e
		}
		return a, nil
	}
	return nil, errors.New("unexpected delimiter")
}
func validateBody(s *jsonschema.Schema, b []byte) error {
	var v any
	d := json.NewDecoder(bytes.NewReader(b))
	d.UseNumber()
	if e := d.Decode(&v); e != nil {
		return e
	}
	return s.Validate(v)
}
func validateParameters(r *http.Request, route Route) error {
	for _, p := range route.Parameters {
		var values []string
		switch p.In {
		case "query":
			values = r.URL.Query()[p.Name]
		case "path":
			values = []string{r.PathValue(p.Name)}
		default:
			continue
		}
		if len(values) == 0 {
			if p.Required {
				return fmt.Errorf("missing %s", p.Name)
			}
			continue
		}
		if len(values) != 1 || (p.Required && values[0] == "") {
			return errors.New("invalid parameter")
		}
		compiler := jsonschema.NewCompiler()
		compiler.AssertFormat()
		if e := compiler.AddResource("https://param.trust.invalid/schema", p.Schema); e != nil {
			return e
		}
		schema, e := compiler.Compile("https://param.trust.invalid/schema")
		if e != nil {
			return e
		}
		var v any = values[0]
		if p.Schema["type"] == "integer" {
			var n json.Number
			d := json.NewDecoder(strings.NewReader(values[0]))
			d.UseNumber()
			if e = d.Decode(&n); e != nil {
				return errors.New("invalid integer parameter")
			}
			if strings.ContainsAny(string(n), ".eE") {
				return errors.New("invalid integer parameter")
			}
			v = n
		}
		if e = schema.Validate(v); e != nil {
			return errors.New("invalid parameter")
		}
	}
	return nil
}

// canonicalNumber normalizes numeric spelling without passing through float64.
// Exponents are bounded to prevent tiny inputs expanding into unbounded buffers.
func canonicalNumber(n json.Number) (json.Number, error) {
	text := string(n)
	negative := strings.HasPrefix(text, "-")
	text = strings.TrimPrefix(text, "-")
	exponent := 0
	if i := strings.IndexAny(text, "eE"); i >= 0 {
		parsed, err := strconv.Atoi(text[i+1:])
		if err != nil || parsed < -1024 || parsed > 1024 {
			return "", errors.New("numeric exponent exceeds limit")
		}
		exponent = parsed
		text = text[:i]
	}
	decimal := strings.IndexByte(text, '.')
	if decimal < 0 {
		decimal = len(text)
	}
	digits := strings.ReplaceAll(text, ".", "")
	position := decimal + exponent
	for len(digits) > 0 && digits[0] == '0' {
		digits = digits[1:]
		position--
	}
	digits = strings.TrimRight(digits, "0")
	if digits == "" {
		return json.Number("0"), nil
	}
	var normalized string
	switch {
	case position <= 0:
		normalized = "0." + strings.Repeat("0", -position) + digits
	case position >= len(digits):
		normalized = digits + strings.Repeat("0", position-len(digits))
	default:
		normalized = digits[:position] + "." + digits[position:]
	}
	if negative {
		normalized = "-" + normalized
	}
	return json.Number(normalized), nil
}
