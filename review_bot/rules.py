"""What every rule id means: used by `review-bot explain` and the SARIF rule metadata."""
from __future__ import annotations

from typing import NamedTuple


class Rule(NamedTuple):
    severity: str
    languages: str
    summary: str
    why: str
    bad: str = ""
    good: str = ""


RULES: dict[str, Rule] = {
    "secret": Rule(
        "critical/high", "all files", "Credential or private key committed to the repository.",
        "Anything pushed to git is effectively public and permanent: history, forks and clones keep it "
        "after the line is deleted. The only fix for a leaked key is rotating it. AWS's documented "
        "`AKIA…EXAMPLE` keys and obvious placeholders are ignored.",
        'API_TOKEN = "AKIAZ7Q4XKR2MB3TLW9P"', 'API_TOKEN = os.environ["API_TOKEN"]'),
    "sql-concat": Rule(
        "high", "code", "SQL built by string concatenation, formatting or interpolation.",
        "Splicing values into SQL text lets input change the query itself (SQL injection). Bound "
        "parameters send values separately, so they can never be parsed as SQL.",
        'conn.execute(f"SELECT * FROM t WHERE name = \'{name}\'")',
        'conn.execute("SELECT * FROM t WHERE name = ?", (name,))'),
    "eval-exec": Rule(
        "high", "py, js/ts, rb, php, shell", "Dynamic code execution (eval / exec / new Function).",
        "Evaluating a string runs whatever it contains with the program's privileges; if any part of it "
        "comes from input, that is remote code execution. It also defeats linters and type checkers.",
        "eval(request.args['expr'])", "ast.literal_eval(s)  # or json.loads, or a dispatch table"),
    "shell-injection": Rule(
        "medium/high", "py, rb", "Shell command built from a string (`shell=True`, Ruby `system(\"#{x}\")`).",
        "The shell re-parses the string, so spaces, quotes, `;` or `$(…)` in a value run extra commands. "
        "Passing an argument list skips the shell entirely.",
        'subprocess.call(f"convert {path} out.png", shell=True)',
        'subprocess.call(["convert", path, "out.png"])'),
    "unsafe-html": Rule(
        "high", "js/ts, rb", "Raw HTML sink: dangerouslySetInnerHTML, innerHTML =, .html_safe, raw(.",
        "These bypass the framework's escaping, so user-controlled text becomes markup and script (XSS). "
        "Lines that visibly sanitize (DOMPurify, sanitize, escapeHtml) are not flagged.",
        "<div dangerouslySetInnerHTML={{ __html: user.bio }} />", "<div>{user.bio}</div>"),
    "bare-except": Rule(
        "medium", "py, js/ts, java, kotlin, c#, php, rb",
        "Catch-all or empty exception handler (`except:`, `catch {}`, `rescue Exception`, `rescue nil`).",
        "Catching everything also catches KeyboardInterrupt/SystemExit (Python) or interrupts (Ruby), and "
        "an empty handler hides the bug that raised. Catch what you expect and handle it.",
        "except:\n    pass", "except ValueError:\n    log.warning(...)"),
    "loose-equality": Rule(
        "medium", "js/ts", "`==` / `!=` instead of `===` / `!==`.",
        "Loose equality coerces types (`0 == \"\"`, `\"1\" == 1` are true). `== null` is allowed as the "
        "null-or-undefined idiom.", "if (id == 0)", "if (id === 0)"),
    "missing-await": Rule(
        "medium", "js/ts", "Promise-returning call used as a bare statement.",
        "Without `await` the caller continues before the work finishes and a rejection becomes an "
        "unhandled promise. Heuristic: only functions declared `async` in the same file, plus `fetch`.",
        "save(user);", "await save(user);"),
    "ts-any-export": Rule(
        "low", "ts/tsx", "Exported API typed as `any`.",
        "`any` switches off type checking for every caller of the export, not just this file.",
        "export function load(x: any)", "export function load(x: unknown)"),
    "string-equality": Rule(
        "medium", "java", "String compared to a literal with `==` / `!=`.",
        "In Java `==` compares object references; equal strings built at runtime are different objects.",
        'if (role == "admin")', 'if ("admin".equals(role))'),
    "not-null-assertion": Rule(
        "low", "kotlin", "`!!` non-null assertion outside tests.",
        "`!!` turns a nullable type back into a NullPointerException at runtime.",
        "val n = user!!.name", "val n = user?.name ?: return"),
    "ignored-error": Rule(
        "medium", "go", "Returned error discarded with `_`.",
        "Go reports failure through the error value; dropping it means continuing with zero values.",
        "b, _ := os.ReadFile(p)", "b, err := os.ReadFile(p)\nif err != nil { return err }"),
    "panic": Rule(
        "low", "go", "`panic(` outside tests.",
        "A panic takes down the whole program unless recovered; callers can't handle it like an error.",
        'panic("empty config")', 'return fmt.Errorf("empty config")'),
    "unsafe-block": Rule(
        "medium", "rust", "New `unsafe` block, fn or impl.",
        "`unsafe` code is where memory-safety bugs live; each block should state the invariant it relies on.",
        "unsafe { ptr.read() }", "// SAFETY: ptr is non-null and aligned (checked above)\nunsafe { ptr.read() }"),
    "unwrap": Rule(
        "low", "rust", "`.unwrap()` outside tests and `#[cfg(test)]` modules.",
        "`unwrap` panics on None/Err with no context. `?` propagates; `.expect(\"why\")` documents the "
        "invariant and is not flagged.", "s.parse().unwrap()", "s.parse()?"),
    "curl-pipe-shell": Rule(
        "high", "shell", "Remote script piped straight into a shell.",
        "Whatever the server (or anyone in the middle) returns runs immediately, and a dropped connection "
        "can run half a script.", "curl -fsSL https://x/install.sh | sh",
        "curl -fsSLo install.sh https://x/install.sh && sha256sum -c install.sh.sha256 && sh install.sh"),
    "unquoted-rm": Rule(
        "high", "shell", "Recursive `rm` with an unquoted variable (SC2086/SC2115).",
        "An empty variable turns `rm -rf $DIR/` into `rm -rf /`; spaces split it into several paths. "
        "`${VAR:?}` aborts when it is unset or empty.", "rm -rf $BUILD/", 'rm -rf "${BUILD:?}"/'),
    "debug-print": Rule(
        "low", "many", "Debug output or breakpoint left in non-test code.",
        "print/console.log/dbg!/set -x/binding.pry and friends leak data into logs and slow hot paths. "
        "Python prints to `sys.stderr` are allowed.", 'print("got", user)', 'log.debug("got %s", user)'),
    "todo": Rule("info", "all", "TODO / FIXME / XXX / HACK in a changed line.",
                 "Fine to leave, but it should be tracked somewhere people look."),
    "missing-tests": Rule(
        "low", "code", "Source changed but no test file touched in the same diff.",
        "One finding per diff, on the first changed source file, listing the others; silence it with "
        "`missing-tests = false` in [rules]."),
    "huge-function": Rule(
        "medium", "py (AST)", "Function longer than `max_function_lines` (default 80) touching the change.",
        "Long functions are hard to review and test; the limit is configurable in [review]."),
    "mutable-default": Rule(
        "medium", "py (AST)", "Mutable default argument (`def f(x=[])`).",
        "The default is created once and shared by every call, so state leaks between calls.",
        "def add(x, seen=[]):", "def add(x, seen=None):\n    seen = [] if seen is None else seen"),
    "is-literal": Rule(
        "medium", "py (AST)", "`is` / `is not` against a literal.",
        "`is` compares identity; whether two equal ints or strings are the same object is an "
        "implementation detail (Python warns about it).", 'if x is "a":', 'if x == "a":'),
    "syntax-error": Rule("critical", "py (AST)", "The changed file no longer parses.",
                         "Nothing else in the file can run."),
    "llm": Rule("varies", "all", "Found by Claude and kept by the verification pass.",
                "A second call re-reads the diff and drops findings below --min-confidence."),
}


def explain(rule: str) -> str:
    r = RULES[rule]
    out = [f"{rule} ({r.severity}; {r.languages})", "", r.summary, "", r.why]
    if r.bad:
        out += ["", "Flagged:", *("    " + ln for ln in r.bad.splitlines())]
    if r.good:
        out += ["", "Instead:", *("    " + ln for ln in r.good.splitlines())]
    out += ["", f"Disable: `{rule} = false` under [rules] in .reviewbot.toml, "
                f"or `reviewbot: ignore[{rule}]` on the line."]
    return "\n".join(out)
