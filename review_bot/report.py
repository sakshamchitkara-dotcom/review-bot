"""Render findings as terminal text, Markdown, or SARIF 2.1.0."""
from __future__ import annotations

import json
from collections import Counter

from . import __version__
from .findings import Finding, severity_rank, suggestion_block

_COLOR = {"critical": "\033[1;31m", "high": "\033[31m", "medium": "\033[33m", "low": "\033[36m", "info": "\033[2m"}
_RESET = "\033[0m"


def sort_findings(fs: list[Finding]) -> list[Finding]:
    return sorted(fs, key=lambda f: (-severity_rank(f.severity), f.file, f.line))


def summary(fs: list[Finding]) -> str:
    if not fs:
        return "No findings."
    c = Counter(f.severity for f in fs)
    parts = [f"{c[s]} {s}" for s in ("critical", "high", "medium", "low", "info") if c[s]]
    return f"{len(fs)} finding(s): " + ", ".join(parts)


def severity_table(fs: list[Finding]) -> str:
    c = Counter(f.severity for f in fs)
    sevs = ("critical", "high", "medium", "low", "info")
    return "\n".join(["| " + " | ".join(sevs) + " |", "|" + "---|" * len(sevs),
                      "| " + " | ".join(str(c[s]) for s in sevs) + " |"])


def to_terminal(fs: list[Finding], color: bool = False) -> str:
    lines = []
    for f in sort_findings(fs):
        sev = f.severity.upper()
        if color:
            sev = f"{_COLOR.get(f.severity, '')}{sev}{_RESET}"
        lines.append(f"{f.file}:{f.line}: {sev} [{f.category}/{f.rule or f.source}] {f.message}")
        if f.suggestion:
            lines.append(f"    -> {f.suggestion}")
        if f.fix is not None:
            lines.append(f"    fix: {f.fix.strip()}")
    lines.append(summary(fs))
    return "\n".join(lines)


def _md_cell(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def to_markdown(fs: list[Finding], title: str = "review-bot report") -> str:
    out = [f"## {title}", "", summary(fs), ""]
    if fs:
        out += ["| Severity | Location | Category | Finding | Suggestion |", "|---|---|---|---|---|"]
        for f in sort_findings(fs):
            out.append(f"| {f.severity} | `{f.file}:{f.line}` | {f.category} ({f.source}) | "
                       f"{_md_cell(f.message)} | {_md_cell(f.suggestion)} |")
        fixes = [f for f in sort_findings(fs) if f.fix is not None]
        if fixes:
            out += ["", "### Suggested fixes"]
            for f in fixes:
                out += ["", f"`{f.file}:{f.line}`: {f.message}", "", suggestion_block(f.fix)]
    return "\n".join(out) + "\n"


_SARIF_LEVEL = {"critical": "error", "high": "error", "medium": "warning", "low": "note", "info": "note"}


_SECURITY_SEVERITY = {"critical": "9.5", "high": "8.0", "medium": "5.5", "low": "3.0", "info": "1.0"}


def _sarif_rule(rid: str, fs: list[Finding]) -> dict:
    """Rule metadata for GitHub code scanning: descriptions from the registry, level from the worst hit."""
    from .rules import RULES

    worst = max((f.severity for f in fs), key=severity_rank)
    rule = {"id": rid, "name": rid, "defaultConfiguration": {"level": _SARIF_LEVEL.get(worst, "note")},
            "properties": {"tags": sorted({f.category for f in fs})}}
    if rid in RULES:
        r = RULES[rid]
        rule["shortDescription"] = {"text": r.summary}
        rule["fullDescription"] = {"text": r.why}
        rule["help"] = {"text": r.why, "markdown": f"{r.why}\n\nRun `review-bot explain {rid}` for an example."}
    if any(f.category == "security" for f in fs):
        rule["properties"]["security-severity"] = _SECURITY_SEVERITY[worst]
    return rule


def to_sarif(fs: list[Finding]) -> str:
    rule_ids = sorted({f.rule or f.category for f in fs})
    run = {
        "tool": {"driver": {
            "name": "review-bot",
            "version": __version__,
            "informationUri": "https://github.com/sakshamchitkara-dotcom/review-bot",
            "rules": [_sarif_rule(r, [f for f in fs if (f.rule or f.category) == r]) for r in rule_ids],
        }},
        "results": [
            {
                "ruleId": f.rule or f.category,
                "level": _SARIF_LEVEL.get(f.severity, "note"),
                "message": {"text": f.message + (f"\nSuggestion: {f.suggestion}" if f.suggestion else "")},
                "locations": [{"physicalLocation": {
                    "artifactLocation": {"uri": f.file, "uriBaseId": "%SRCROOT%"},
                    "region": {"startLine": max(1, f.line)},
                }}],
                "properties": {"severity": f.severity, "category": f.category, "source": f.source},
            }
            for f in sort_findings(fs)
        ],
    }
    return json.dumps({
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [run],
    }, indent=2)
