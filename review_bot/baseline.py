"""Baseline: remember today's findings so later runs only report new ones."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from .diff import FileDiff
from .findings import Finding

DEFAULT_PATH = ".reviewbot-baseline.json"
FILE_LEVEL = {"missing-tests"}  # anchored to an arbitrary line; identity is the file alone


def fingerprints(findings: list[Finding], files: list[FileDiff]) -> list[str]:
    """Line-number-free identity: file + rule + category + whitespace-normalized line text.

    Survives code moving up/down; changes when the flagged line itself changes.
    """
    text = {f.path: f.added for f in files}
    out = []
    for f in findings:
        line = "" if f.rule in FILE_LEVEL else " ".join(text.get(f.file, {}).get(f.line, "").split())
        raw = "\0".join([f.file, f.rule or f.category, f.category, line])
        out.append(hashlib.sha256(raw.encode()).hexdigest()[:20])
    return out


def save(path: str | Path, findings: list[Finding], files: list[FileDiff]) -> int:
    counts = Counter(fingerprints(findings, files))
    doc = {"version": 1, "fingerprints": dict(sorted(counts.items()))}
    Path(path).write_text(json.dumps(doc, indent=1) + "\n")
    return sum(counts.values())


def load(path: str | Path) -> Counter:
    doc = json.loads(Path(path).read_text())
    if doc.get("version") != 1:
        raise ValueError(f"{path}: unsupported baseline version {doc.get('version')!r}")
    return Counter(doc["fingerprints"])


def new_only(findings: list[Finding], files: list[FileDiff], known: Counter) -> list[Finding]:
    """Drop findings already in the baseline; a fingerprint recorded N times suppresses N matches."""
    left, out = Counter(known), []
    for f, fp in zip(findings, fingerprints(findings, files)):
        if left[fp] > 0:
            left[fp] -= 1
        else:
            out.append(f)
    return out
