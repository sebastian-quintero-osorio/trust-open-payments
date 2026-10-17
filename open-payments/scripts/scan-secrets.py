#!/usr/bin/env python3
"""Scan publishable files, including staged files, without reading ignored secrets."""
from pathlib import Path
import shutil
import subprocess
import tempfile


def main():
    root = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip())
    paths = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root).split(b"\0")
    # Tracked files remain included even when an ignore rule matches them.
    # Consequently force-adding .env does not bypass this check.
    with tempfile.TemporaryDirectory(prefix="trust-secret-scan-") as directory:
        destination = Path(directory)
        for raw in set(paths):
            if not raw:
                continue
            relative = Path(raw.decode("utf-8", errors="surrogateescape"))
            source = root / relative
            if source.is_file() and not source.is_symlink():
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        subprocess.run(["gitleaks", "dir", "--redact", "--no-banner", str(destination)], check=True)
    subprocess.run(["gitleaks", "git", "--redact", "--no-banner", str(root)], check=True)


if __name__ == "__main__":
    main()
