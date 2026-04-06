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
BARE_EXCEPT_SUB = re.compile(r"^(\s*)except\s*:")
EMPTY_CATCH = re.compile(r"catch\s*(?:\([^)]*\))?\s*\{\s*\}")
SQL_KW = re.compile(r"""(?i)["'`][^"'`]*\b(SELECT\b.+\bFROM|INSERT\s+INTO|UPDATE\b.+\bSET|DELETE\s+FROM)\b""")
SQL_DYNAMIC = re.compile(r"""["'`]\s*\+|\+\s*["'`]|\bf["']|\.format\(|["']\s*%\s*[\w(]|\$\{""")
EVAL = {
    "python": re.compile(r"(?<![\w.])(?:eval|exec)\s*\("),
    "js": re.compile(r"(?<![\w.])eval\s*\(|new\s+Function\s*\(|setTimeout\(\s*[\"'`]"),
    "ruby": re.compile(r"(?<![\w.])(?:eval|instance_eval|class_eval)\b"),
    "php": re.compile(r"(?<![\w>])eval\s*\("),
}
SHELL_TRUE = re.compile(r",\s*shell\s*=\s*True\b")  # kwarg in a call, not prose

# --- JS/TS -----------------------------------------------------------------
_JS_STR = re.compile(r"""(["'`])(?:\\.|(?!\1).)*\1""")
LOOSE_EQ = re.compile(r"(?<![=!<>])(==|!=)(?!=)")


def js_code_mask(text: str) -> str:
    """Blank out string contents and // comments, keeping column positions (so fixes map back)."""
    masked = _JS_STR.sub(lambda m: m.group(1) + " " * (len(m.group(0)) - 2) + m.group(1), text)
    i = masked.find("//")
    return masked if i < 0 else masked[:i] + " " * (len(masked) - i)


UNSAFE_HTML = re.compile(r"\bdangerouslySetInnerHTML\b|\.(?:inner|outer)HTML\s*\+?=(?!=)")
SANITIZED = re.compile(r"(?i)sanitize|DOMPurify|escapeHtml")
TS_ANY_EXPORT = re.compile(r"^\s*export\b.*(?:[:<|,]\s*|\bas\s+)any\b")


def _loose_eq(text: str) -> tuple[list[re.Match], str]:
    """Loose (in)equality operators outside strings/comments, ignoring the `== null` idiom."""
    code = js_code_mask(text)
    hits = [m for m in LOOSE_EQ.finditer(code) if not re.match(r"\s*(?:null|undefined)\b", code[m.end():])
            and not re.search(r"\b(?:null|undefined)\s*$", code[:m.start()])]
    fixed = text
    for m in reversed(hits):
        fixed = fixed[:m.start()] + m.group(1) + "=" + fixed[m.end():]
    return hits, fixed


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
            if "EXAMPLE" in val or (name == "Hardcoded credential" and PLACEHOLDER.search(val)):
                continue  # vendor-documented example keys / obvious placeholders
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
    if on("debug-print") and not test and lang in DEBUG and DEBUG[lang].search(text) \
            and "file=sys.stderr" not in text:
        yield Finding(path, ln, "low", "debug", "Debug output / breakpoint left in code.",
                      "Remove it or use the project's logger.", rule="debug-print")
    if on("todo") and (m := TODO.search(text)):
        yield Finding(path, ln, "info", "maintainability", f"{m.group(1)} left in change.",
                      "Track it in an issue or resolve it before merging.", rule="todo")
    if on("bare-except"):
        if lang == "python" and BARE_EXCEPT.search(text):
            yield Finding(path, ln, "medium", "error-handling",
                          "Bare `except:` also swallows KeyboardInterrupt/SystemExit and hides bugs.",
                          "Catch specific exceptions, e.g. `except ValueError:`.", rule="bare-except",
                          fix=BARE_EXCEPT_SUB.sub(r"\1except Exception:", text, count=1))
        elif lang in ("js", "java", "csharp", "php") and EMPTY_CATCH.search(text):
            yield Finding(path, ln, "medium", "error-handling", "Empty catch block silently swallows errors.",
                          "Handle, log, or rethrow the error.", rule="bare-except")
    if on("loose-equality") and lang == "js":
        hits, fixed = _loose_eq(text)
        if hits:
            yield Finding(path, ln, "medium", "correctness",
                          "Loose equality (`==`/`!=`) coerces types, e.g. `0 == \"\"` is true.",
                          "Use `===` / `!==` (`== null` is left alone as the null-or-undefined idiom).",
                          rule="loose-equality", fix=fixed)
    if on("unsafe-html") and lang == "js" and UNSAFE_HTML.search(js_code_mask(text)) and not SANITIZED.search(text):
        yield Finding(path, ln, "high", "security",
                      "Raw HTML injection (dangerouslySetInnerHTML / innerHTML) is an XSS sink.",
                      "Render text normally, or sanitize first (e.g. DOMPurify.sanitize).", rule="unsafe-html")
    if on("ts-any-export") and path.endswith((".ts", ".tsx")) and TS_ANY_EXPORT.search(js_code_mask(text)):
        yield Finding(path, ln, "low", "maintainability",
                      "Exported API typed as `any` turns off type checking for every caller.",
                      "Use a concrete type, a generic, or `unknown` and narrow it.", rule="ts-any-export")
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
                                       "Use `==` / `!=`.", rule="is-literal", fix=_is_literal_fix(node, fd)))
    return out


def _is_literal_fix(node: ast.Compare, fd: FileDiff) -> str | None:
    """Rewrite `a is 5` -> `a == 5` using AST columns; only for simple one-line, one-op ASCII compares."""
    line = fd.added.get(node.lineno)
    right = node.comparators[0]
    if line is None or len(node.ops) != 1 or not line.isascii() or \
            node.left.end_lineno != node.lineno or right.lineno != node.lineno:
        return None
    a, b = node.left.end_col_offset, right.col_offset
    op = "!=" if isinstance(node.ops[0], ast.IsNot) else "=="
    return line[:a] + re.sub(r"\bis(\s+not)?\b", op, line[a:b], count=1) + line[b:]


# --- JS/TS: floating promises ---------------------------------------------
ASYNC_DEF = re.compile(
    r"\basync\s+function\s*\*?\s*([\w$]+)"                       # async function f(
    r"|\b([\w$]+)\s*[:=]\s*async\b"                              # const f = async / f: async
    r"|^\s*(?:(?:public|private|protected|static|override)\s+)*async\s+([\w$]+)\s*\(")  # async m() {
FUNC_HEAD = re.compile(r"\bfunction\b|=>|^\s*(?:(?:public|private|protected|static|async)\s+)*[\w$]+\s*\([^)]*\)\s*(?::[^{]*)?\{\s*$")
BARE_CALL = r"^\s*(?:this\.|[\w$]+\.)?({names})\s*\(.*\)\s*;?\s*$"
KNOWN_ASYNC = {"fetch"}


def js_missing_await(fd: FileDiff, source: str | None, cfg: Config) -> list[Finding]:
    """Heuristic: a bare statement calling a function declared `async` in this file (or fetch)."""
    lines = source.splitlines() if source is not None else []
    text = source if source is not None else "\n".join(fd.added.values())
    names = set(KNOWN_ASYNC)
    for ln in text.splitlines():
        for m in ASYNC_DEF.finditer(js_code_mask(ln)):
            names.add(next(g for g in m.groups() if g))
    call = re.compile(BARE_CALL.format(names="|".join(map(re.escape, sorted(names)))))
    out = []
    for ln, raw in fd.added.items():
        m = call.match(js_code_mask(raw))
        if not m or ASYNC_DEF.search(raw):
            continue
        # ponytail: nearest preceding function header decides whether `await` is legal there;
        # nested/one-line functions can fool it, so the fix is only offered when it looks async.
        head = next((lines[i] for i in range(min(ln, len(lines)) - 2, -1, -1) if FUNC_HEAD.search(lines[i])), "")
        fix = re.sub(r"^(\s*)", r"\1await ", raw, count=1) if "async" in head else None
        out.append(Finding(fd.path, ln, "medium", "correctness",
                           f"`{m.group(1)}(...)` returns a promise that is never awaited; errors are lost "
                           "and ordering is not guaranteed.",
                           "`await` it, return it, or mark it intentional with `void`.",
                           rule="missing-await", fix=fix))
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
        if lang == "js" and cfg.rule_on("missing-await"):
            findings.extend(js_missing_await(fd, get_source(fd.path) if get_source else None, cfg))
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
