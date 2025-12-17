"""Load .reviewbot.toml."""
from __future__ import annotations

import fnmatch
import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

from .findings import SEVERITIES

DEFAULT_MODEL = "claude-opus-5-5"


@dataclass
class Config:
    severity_threshold: str = "low"
    ignore: list[str] = field(default_factory=list)
    rules: dict[str, bool] = field(default_factory=dict)
    max_function_lines: int = 80
    llm: bool = True
    model: str = DEFAULT_MODEL
    max_llm_files: int = 25

    def rule_on(self, rule: str) -> bool:
        return self.rules.get(rule, True)

    def ignored(self, path: str) -> bool:
        return any(fnmatch.fnmatch(path, pat) for pat in self.ignore)


def load_config(path: str | Path | None = None) -> Config:
    p = Path(path) if path else Path(".reviewbot.toml")
    if not p.exists():
        if path:
            raise FileNotFoundError(p)
        return Config()
    data = tomllib.loads(p.read_text())
    review = data.get("review", {})
    llm = data.get("llm", {})
    cfg = Config(
        severity_threshold=review.get("severity_threshold", "low"),
        ignore=list(review.get("ignore", [])),
        rules={k: bool(v) for k, v in data.get("rules", {}).items()},
        max_function_lines=int(review.get("max_function_lines", 80)),
        llm=bool(llm.get("enabled", True)),
        model=llm.get("model", DEFAULT_MODEL),
        max_llm_files=int(llm.get("max_files", 25)),
    )
    if cfg.severity_threshold not in SEVERITIES:
        raise ValueError(f"severity_threshold must be one of {SEVERITIES}")
    return cfg
