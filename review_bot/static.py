"""Static pass: regex heuristics on added lines + Python AST checks. No LLM."""
from __future__ import annotations

import ast
import re
from pathlib import PurePosixPath
from typing import Callable, Iterable

from .config import Config
from .diff import FileDiff
from .findings import Finding

SourceGetter = Callable[[str], "str | None"]

LANG = {
    ".py": "python", ".js": "js", ".jsx": "js", ".ts": "js", ".tsx": "js", ".mjs": "js",
    ".cjs": "js", ".go": "go", ".rb": "ruby", ".java": "java", ".kt": "java", ".php": "php",
    ".rs": "rust", ".cs": "csharp", ".sh": "shell", ".c": "c", ".cpp": "c", ".h": "c",
}
CODE_EXTS = set(LANG)


def lang_of(path: str) -> str | None:
    return LANG.get(PurePosixPath(path).suffix.lower())


def is_test_path(path: str) -> bool:
    p = PurePosixPath(path)
    name = p.name.lower()
    return (
        any(part in ("test", "tests", "__tests__", "spec", "specs") for part in p.parts[:-1])
        or name.startswith("test_")
        or re.search(r"(_test|\.test|\.spec|_spec|Test)\.[a-z]+$", p.name) is not None
    )


# --- secrets ---------------------------------------------------------------
SECRET_PATTERNS = [
    ("AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "critical"),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})\b"), "critical"),
    ("Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}"), "critical"),
    ("OpenAI-style API key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}\b"), "critical"),
    ("Slack token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"), "critical"),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), "high"),
    ("Private key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"), "critical"),
    ("Hardcoded credential", re.compile(
        r"""(?i)(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)\w*"""
        r"""["']?\s*[:=]\s*["']([^"'\s]{8,})["']"""), "high"),
]
PLACEHOLDER = re.compile(r"(?i)(example|dummy|changeme|placeholder|your[_-]|xxx+|\*{3,}|<.*>|\$\{|os\.environ|getenv)")

# --- line rules ------------------------------------------------------------
DEBUG = {
    "python": re.compile(r"^\s*(?:print\(|breakpoint\(\)|import pdb|pdb\.set_trace\(|ipdb\.set_trace\()"),
    "js": re.compile(r"\b(?:console\.(?:log|debug|trace)\(|debugger\s*;?\s*$)"),
    "ruby": re.compile(r"\b(?:binding\.pry|byebug)\b|^\s*(?:puts|p) "),
    "go": re.compile(r"^\s*fmt\.Print(?:ln|f)?\("),
    "java": re.compile(r"System\.(?:out|err)\.print(?:ln)?\(|\.printStackTrace\(\)"),
    "php": re.compile(r"\b(?:var_dump|print_r|dd)\("),
}
TODO = re.compile(r"(?:#|//|/\*|--|<!--)\s*.*\b(TODO|FIXME|XXX|HACK)\b")
BARE_EXCEPT = re.compile(r"^\s*except\s*:")
EMPTY_CATCH = re.compile(r"catch\s*(?:\([^)]*\))?\s*\{\s*\}")
SQL_KW = re.compile(r"""(?i)["'`][^"'`]*\b(SELECT\b.+\bFROM|INSERT\s+INTO|UPDATE\b.+\bSET|DELETE\s+FROM)\b""")
SQL_DYNAMIC = re.compile(r"""["'`]\s*\+|\+\s*["'`]|\bf["']|\.format\(|["']\s*%\s*[\w(]|\$\{""")
EVAL = {
    "python": re.compile(r"(?<![\w.])(?:eval|exec)\s*\("),
    "js": re.compile(r"(?<![\w.])eval\s*\(|new\s+Function\s*\(|setTimeout\(\s*[\"'`]"),
    "ruby": re.compile(r"(?<![\w.])(?:eval|instance_eval|class_eval)\b"),
    "php": re.compile(r"(?<![\w>])eval\s*\("),
}
SHELL_TRUE = re.compile(r"shell\s*=\s*True")


def _mask(s: str) -> str:
    return s[:4] + "…" if len(s) > 4 else "…"


def scan_line(path: str, lang: str | None, ln: int, text: str, cfg: Config) -> Iterable[Finding]:
    on = cfg.rule_on
    if on("secret"):
        for name, rx, sev in SECRET_PATTERNS:
            m = rx.search(text)
            if not m:
                continue
            val = m.group(m.lastindex or 0)
            if name == "Hardcoded credential" and PLACEHOLDER.search(val):
                continue
            yield Finding(path, ln, sev, "security", f"Possible {name} committed ({_mask(val)}).",
                          "Remove it, rotate the credential, and load it from the environment or a secret store.",
                          rule="secret")
            break
    if lang is None:
        if on("todo") and TODO.search(text):
            yield Finding(path, ln, "info", "maintainability", f"{TODO.search(text).group(1)} left in change.",
                          "Track it in an issue or resolve it before merging.", rule="todo")
        return
    test = is_test_path(path)
    if on("debug-print") and not test and lang in DEBUG and DEBUG[lang].search(text):
        yield Finding(path, ln, "low", "debug", "Debug output / breakpoint left in code.",
                      "Remove it or use the project's logger.", rule="debug-print")
    if on("todo") and (m := TODO.search(text)):
        yield Finding(path, ln, "info", "maintainability", f"{m.group(1)} left in change.",
                      "Track it in an issue or resolve it before merging.", rule="todo")
    if on("bare-except"):
        if lang == "python" and BARE_EXCEPT.search(text):
            yield Finding(path, ln, "medium", "error-handling",
                          "Bare `except:` also swallows KeyboardInterrupt/SystemExit and hides bugs.",
                          "Catch specific exceptions, e.g. `except ValueError:`.", rule="bare-except")
        elif lang in ("js", "java", "csharp", "php") and EMPTY_CATCH.search(text):
            yield Finding(path, ln, "medium", "error-handling", "Empty catch block silently swallows errors.",
                          "Handle, log, or rethrow the error.", rule="bare-except")
    if on("sql-concat") and SQL_KW.search(text) and SQL_DYNAMIC.search(text):
        yield Finding(path, ln, "high", "security", "SQL built by string concatenation/formatting (SQL injection risk).",
                      "Use parameterized queries / bound parameters.", rule="sql-concat")
    if on("eval-exec") and lang in EVAL and EVAL[lang].search(text) and not text.lstrip().startswith(("#", "//")):
        yield Finding(path, ln, "high", "security", "Dynamic code execution (eval/exec) on a changed line.",
                      "Avoid eval/exec; parse data explicitly (e.g. json.loads / ast.literal_eval).", rule="eval-exec")
    if on("shell-injection") and lang == "python" and SHELL_TRUE.search(text):
        yield Finding(path, ln, "medium", "security", "subprocess call with shell=True.",
                      "Pass an argument list and drop shell=True.", rule="shell-injection")


# --- Python AST ------------------------------------------------------------
def python_ast_checks(fd: FileDiff, source: str, cfg: Config) -> list[Finding]:
    out: list[Finding] = []
    changed = set(fd.added)
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        if cfg.rule_on("syntax-error"):
            out.append(Finding(fd.path, e.lineno or 1, "critical", "correctness",
                               f"File no longer parses: {e.msg}.", "Fix the syntax error.", rule="syntax-error"))
        return out
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            span = range(node.lineno, (node.end_lineno or node.lineno) + 1)
            touched = changed.intersection(span)
            if not touched:
                continue
            length = len(span)
            if cfg.rule_on("huge-function") and length > cfg.max_function_lines:
                out.append(Finding(fd.path, node.lineno, "medium", "maintainability",
                                   f"`{node.name}` is {length} lines (limit {cfg.max_function_lines}).",
                                   "Split it into smaller functions.", rule="huge-function"))
            if cfg.rule_on("mutable-default"):
                for d in node.args.defaults + [d for d in node.args.kw_defaults if d is not None]:
                    if isinstance(d, (ast.List, ast.Dict, ast.Set)) or (
                        isinstance(d, ast.Call) and getattr(d.func, "id", "") in ("list", "dict", "set")
                    ):
                        out.append(Finding(fd.path, d.lineno, "medium", "correctness",
                                           f"Mutable default argument in `{node.name}` is shared between calls.",
                                           "Default to None and create the object inside the function.",
                                           rule="mutable-default"))
        elif isinstance(node, ast.Compare) and node.lineno in changed and cfg.rule_on("is-literal"):
            for op, right in zip(node.ops, node.comparators):
                if isinstance(op, (ast.Is, ast.IsNot)) and isinstance(right, ast.Constant) and \
                        right.value is not None and not isinstance(right.value, bool) and right.value is not ...:
                    out.append(Finding(fd.path, node.lineno, "medium", "correctness",
                                       "`is` comparison with a literal compares identity, not value.",
                                       "Use `==` / `!=`.", rule="is-literal"))
    return out


# --- driver ----------------------------------------------------------------
def run_static(files: list[FileDiff], cfg: Config, get_source: SourceGetter | None = None) -> list[Finding]:
    findings: list[Finding] = []
    for fd in files:
        if fd.is_deleted or fd.is_binary or cfg.ignored(fd.path):
            continue
        lang = lang_of(fd.path)
        for ln, text in fd.added.items():
            findings.extend(scan_line(fd.path, lang, ln, text, cfg))
        if lang == "python" and get_source:
            src = get_source(fd.path)
            if src is not None:
                findings.extend(python_ast_checks(fd, src, cfg))
    if cfg.rule_on("missing-tests"):
        findings.extend(_missing_tests(files, cfg))
    return dedupe(findings)


def _missing_tests(files: list[FileDiff], cfg: Config) -> list[Finding]:
    touched_tests = any(is_test_path(f.path) for f in files if not f.is_deleted)
    if touched_tests:
        return []
    out = []
    for f in files:
        if f.is_deleted or f.is_binary or cfg.ignored(f.path) or not f.added:
            continue
        if PurePosixPath(f.path).suffix.lower() in CODE_EXTS and not is_test_path(f.path):
            out.append(Finding(f.path, min(f.added), "low", "testing",
                               "Source changed but no test files were added or modified in this diff.",
                               "Add or update tests covering this change.", rule="missing-tests"))
    return out


def dedupe(findings: list[Finding]) -> list[Finding]:
    seen, out = set(), []
    for f in findings:
        key = (f.file, f.line, f.rule or f.category)
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out
