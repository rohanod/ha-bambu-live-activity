#!/usr/bin/env python3
"""Replace the repository OWNER placeholder before the first commit."""

from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("Usage: python3 scripts/set-owner.py <github-username>")

owner = sys.argv[1].strip().lstrip("@")
if not owner:
    raise SystemExit("GitHub username cannot be empty")

root = Path(__file__).resolve().parents[1]
targets = [
    root / "README.md",
    root / "custom_components" / "bambu_live_activity" / "manifest.json",
]

for path in targets:
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("OWNER", owner), encoding="utf-8")

print(f"Configured repository owner: {owner}")
