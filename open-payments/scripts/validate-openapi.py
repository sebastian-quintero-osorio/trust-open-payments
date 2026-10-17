"""Validate the original contract as OpenAPI 3.1 without rewriting it."""
from pathlib import Path
from openapi_spec_validator import validate
from openapi_spec_validator.readers import read_from_filename

contract = Path(__file__).resolve().parents[1] / "op-service.openapi.yaml"
spec, base_uri = read_from_filename(str(contract))
validate(spec, base_uri=base_uri)
print("OpenAPI 3.1 contract: valid")
