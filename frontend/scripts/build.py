#!/usr/bin/env python3
"""Create a deployable static frontend. /api/v1 needs a reverse proxy."""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
target = ROOT / 'dist'
if target.exists(): shutil.rmtree(target)
shutil.copytree(ROOT / 'src', target)
print(f'Built {target}')
