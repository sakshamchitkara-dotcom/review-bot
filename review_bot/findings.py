"""The one data type everything passes around."""
from __future__ import annotations

import re
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
    fix: str | None = None  # replacement text for `line` (GitHub suggestion); None = no fix

    def to_dict(self) -> dict:
        return asdict(self)


def suggestion_block(fix: str) -> str:
    """A GitHub ```suggestion block; the fence grows if the fix itself contains backticks."""
    fence = "`" * max(3, max((len(m) for m in re.findall(r"`+", fix)), default=0) + 1)
    return f"{fence}suggestion\n{fix}\n{fence}"
