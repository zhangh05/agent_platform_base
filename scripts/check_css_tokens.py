#!/usr/bin/env python3
"""Scan frontend CSS/TSX for undefined var(--token) references.

Parses all *.css files for `--name:` definitions, then checks every
`var(--x)` reference in *.tsx/*.css.  References with a fallback
(e.g. `var(--x, default)`) are allowed (warn-only); those without
a fallback that are also not defined cause a non-zero exit.
"""
import re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "frontend" / "src"
if not ROOT.exists():
    ROOT = Path(__file__).resolve().parent.parent  # fallback

defs: set[str] = set()
refs: list[tuple[Path, int, str, bool]] = []  # file, line, token, has_fallback

# Collect definitions
for css in ROOT.rglob("*.css"):
    for i, line in enumerate(css.read_text().splitlines(), 1):
        for m in re.finditer(r"--([a-z][a-z0-9-]*)\s*:", line):
            defs.add(m.group(1))

# Collect references
for ext in ("*.tsx", "*.ts", "*.css"):
    for f in ROOT.rglob(ext):
        text = f.read_text()
        for i, line in enumerate(text.splitlines(), 1):
            for m in re.finditer(r"var\(\s*--([a-z][a-z0-9-]*)\s*(?:,\s*([^)]+))?\s*\)", line):
                token = m.group(1)
                fallback = m.group(2)
                refs.append((f, i, token, bool(fallback and fallback.strip())))

undefined_no_fb = [(f, i, t) for f, i, t, fb in refs if t not in defs and not fb]
undefined_fb = [(f, i, t) for f, i, t, fb in refs if t not in defs and fb]

print(f"OK: {len(defs)} tokens defined; ", end="")
if undefined_no_fb:
    print(f"{len(undefined_no_fb)} undefined (no-fallback):")
    for f, i, t in undefined_no_fb:
        print(f"  {f}:{i}  --{t}")
    sys.exit(1)
elif undefined_fb:
    print(f"{len(undefined_fb)} have-fallback references noted (warn):")
    for f, i, t in undefined_fb:
        print(f"  {f}:{i}  --{t}")
    sys.exit(0)
else:
    print("no undefined references.")
    sys.exit(0)
