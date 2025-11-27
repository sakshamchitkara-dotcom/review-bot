"""The one data type everything passes around."""
from __future__ import annotations

from dataclasses import asdict, dataclass

SEVERITIES = ("info", "low", "medium", "high", "critical")


def severity_rank(sev: str) -> int:
    return SEVERITIES.index(sev) if sev in SEVERITIES else 0


@dataclass
class Finding:
    file: str
    line: int
    severity: str
    category: str
    message: str
    suggestion: str = ""
    rule: str = ""
    source: str = "static"  # "static" | "llm"

    def to_dict(self) -> dict:
        return asdict(self)
