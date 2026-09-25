"""Load .reviewbot.toml."""
from __future__ import annotations

import fnmatch
import sys
from dataclasses import dataclass, field, replace
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
    # [[overrides]]: (path globs, {"rules": {...}, "severity_threshold": ..., "max_function_lines": ...})
    overrides: list[tuple[list[str], dict]] = field(default_factory=list)

    def for_path(self, path: str) -> "Config":
        """This config with every matching [[overrides]] block applied, in file order (later wins)."""
        cfg = self
        for pats, o in self.overrides:
            if any(fnmatch.fnmatch(path, p) for p in pats):
                cfg = replace(cfg, rules={**cfg.rules, **o.get("rules", {})},
                              severity_threshold=o.get("severity_threshold", cfg.severity_threshold),
                              max_function_lines=o.get("max_function_lines", cfg.max_function_lines))
        return cfg

    def set_threshold(self, sev: str) -> None:
        """A threshold from the command line beats per-path ones too."""
        self.severity_threshold = sev
        self.overrides = [(p, {k: v for k, v in o.items() if k != "severity_threshold"}) for p, o in self.overrides]

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
    for i, o in enumerate(data.get("overrides", [])):
        paths = o.get("paths")
        if not paths or isinstance(paths, str):
            raise ValueError(f"[[overrides]] #{i + 1}: `paths` must be a non-empty list of globs")
        ov = {"rules": {k: bool(v) for k, v in o.get("rules", {}).items()}}
        if "severity_threshold" in o:
            ov["severity_threshold"] = o["severity_threshold"]
        if "max_function_lines" in o:
            ov["max_function_lines"] = int(o["max_function_lines"])
        cfg.overrides.append((list(paths), ov))
    for sev in [cfg.severity_threshold] + [o.get("severity_threshold", "low") for _, o in cfg.overrides]:
        if sev not in SEVERITIES:
            raise ValueError(f"severity_threshold must be one of {SEVERITIES}, got {sev!r}")
    return cfg
