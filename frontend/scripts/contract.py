#!/usr/bin/env python3
"""Regenerate the checked-in operation map and test schema from the contract."""
import json
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = yaml.safe_load((ROOT.parent / 'trust-api.openapi.yaml').read_text())
operations = {op['operationId']: {'method': method.upper(), 'path': path, 'public': op.get('security') == []}
              for path, methods in spec['paths'].items() for method, op in methods.items()
              if method in ['get', 'post', 'put', 'patch', 'delete']}
(ROOT / 'src/lib/operations.js').write_text('// Generated from ../trust-api.openapi.yaml by scripts/contract.py.\nexport const operations = ' + json.dumps(operations, ensure_ascii=False, indent=2) + ';\n')
(ROOT / 'tests/openapi.json').write_text(json.dumps(spec, ensure_ascii=False, indent=2) + '\n')
