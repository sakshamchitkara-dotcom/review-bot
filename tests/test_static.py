from pathlib import Path

from review_bot.config import Config
from review_bot.diff import parse_diff
from review_bot.static import is_test_path, run_static

FIX = Path(__file__).parent / "fixtures"


def _source_from_added(files):
    """For new files the added lines are the whole file."""
    by = {f.path: f for f in files}

    def get(path):
        f = by[path]
        return "\n".join(f.added[i] for i in sorted(f.added)) + "\n"

    return get


def _run(cfg=None):
    files = parse_diff((FIX / "buggy.diff").read_text())
    return run_static(files, cfg or Config(), _source_from_added(files))


def rules_at(findings):
    return {(f.file, f.line, f.rule) for f in findings}


def test_detects_expected_python_issues():
    got = rules_at(_run())
    py = "src/service.py"
    for expected in [
        (py, 3, "secret"),
        (py, 4, "secret"),
        (py, 7, "mutable-default"),
        (py, 8, "debug-print"),
        (py, 9, "sql-concat"),
        (py, 12, "bare-except"),
        (py, 17, "todo"),
        (py, 18, "is-literal"),
        (py, 20, "shell-injection"),
        (py, 21, "eval-exec"),
    ]:
        assert expected in got, expected


def test_detects_js_issues_and_missing_tests():
    got = rules_at(_run())
    assert ("web/app.js", 2, "debug-print") in got
    assert ("web/app.js", 3, "bare-except") in got
    assert ("src/service.py", 1, "missing-tests") in got


def test_secret_value_is_masked():
    sec = [f for f in _run() if f.rule == "secret"]
    assert sec and all("AKIAZ7Q4XKR2MB3TLW9P" not in f.message for f in sec)


def test_rules_can_be_disabled_and_paths_ignored():
    cfg = Config(rules={"debug-print": False, "missing-tests": False}, ignore=["web/*"])
    got = _run(cfg)
    assert not any(f.rule in ("debug-print", "missing-tests") for f in got)
    assert not any(f.file.startswith("web/") for f in got)


def test_huge_function_threshold():
    body = "\n".join(f"+    x{i} = {i}" for i in range(10))
    diff = f"--- /dev/null\n+++ b/m.py\n@@ -0,0 +1,11 @@\n+def big():\n{body}\n"
    files = parse_diff(diff)
    src = "def big():\n" + "\n".join(f"    x{i} = {i}" for i in range(10)) + "\n"
    got = run_static(files, Config(max_function_lines=5), lambda p: src)
    assert any(f.rule == "huge-function" for f in got)


def test_syntax_error_reported():
    files = parse_diff("--- /dev/null\n+++ b/m.py\n@@ -0,0 +1 @@\n+def (:\n")
    got = run_static(files, Config(), lambda p: "def (:\n")
    assert any(f.rule == "syntax-error" and f.severity == "critical" for f in got)


def test_placeholder_credentials_ignored():
    files = parse_diff('--- /dev/null\n+++ b/c.py\n@@ -0,0 +1 @@\n+password = "changeme-please"\n')
    assert not any(f.rule == "secret" for f in run_static(files, Config()))


def test_no_missing_tests_when_tests_touched():
    diff = "--- a/a.py\n+++ b/a.py\n@@ -1 +1,2 @@\n x\n+y = 1\n--- a/tests/test_a.py\n+++ b/tests/test_a.py\n@@ -1 +1,2 @@\n x\n+z = 2\n"
    assert not any(f.rule == "missing-tests" for f in run_static(parse_diff(diff), Config()))


def test_is_test_path():
    assert is_test_path("tests/test_x.py") and is_test_path("src/a.test.ts") and is_test_path("pkg/a_test.go")
    assert not is_test_path("src/latest.py")


def test_low_noise_cases():
    diff = (
        "--- /dev/null\n+++ b/n.py\n@@ -0,0 +1,3 @@\n"
        '+KEY = "AKIAIOSFODNN7EXAMPLE"\n'
        '+print("oops", file=sys.stderr)\n'
        '+MSG = "never use shell=True"\n'
    )
    assert run_static(parse_diff(diff), Config(rules={"missing-tests": False})) == []


def test_python_fixes():
    by = {(f.line, f.rule): f.fix for f in _run()}
    assert by[(12, "bare-except")] == "    except Exception:"
    assert by[(18, "is-literal")] == '    if expr == "":'
    files = parse_diff("--- /dev/null\n+++ b/m.py\n@@ -0,0 +1 @@\n+ok = (x is not 3) or y\n")
    (f,) = [f for f in run_static(files, Config(), lambda p: "ok = (x is not 3) or y\n") if f.rule == "is-literal"]
    assert f.fix == "ok = (x != 3) or y"


def _js(lines, path="web/x.ts"):
    body = "".join(f"+{ln}\n" for ln in lines)
    diff = f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n{body}"
    return run_static(parse_diff(diff), Config(rules={"missing-tests": False}))


def test_loose_equality_with_fix():
    (f,) = _js(['if (a == b && c != "x == y") {  // a == b'])
    assert f.rule == "loose-equality" and f.fix == 'if (a === b && c !== "x == y") {  // a == b'
    assert _js(["if (x == null || y != undefined) {}", "a === b; c !== d; e <= f;", "const s = 'a == b';"]) == []


def test_ts_any_exports():
    got = _js(["export function load(x: any): string {", "export const cfg = {} as any;",
               "export type M = Map<any, string>;", "function inner(y: any) {}",
               'export const note = "any: thing";', "export const company = 1;"])
    assert [f.line for f in got if f.rule == "ts-any-export"] == [1, 2, 3]
    assert not [f for f in _js(["export function f(x: any) {}"], path="web/x.js") if f.rule == "ts-any-export"]


def test_unsafe_html():
    got = _js(["<div dangerouslySetInnerHTML={{ __html: bio }} />", "el.innerHTML = msg;",
               "<p dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(bio) }} />",
               "if (el.innerHTML === '') {}", "// dangerouslySetInnerHTML is banned"], path="ui/Bio.tsx")
    assert [f.line for f in got if f.rule == "unsafe-html"] == [1, 2]


def test_missing_await():
    src = [
        "async function save(x) { return db.put(x); }",
        "export async function handler(req) {",
        "  save(req.body);",
        "  await save(req.body);",
        "  const p = save(req.body);",
        "  fetch('/ping');",
        "  return save(req.body);",
        "}",
        "function sync() {",
        "  this.save(1);",
        "  void save(2);",
        "}",
    ]
    files = parse_diff("--- /dev/null\n+++ b/api.js\n@@ -0,0 +1,12 @@\n" + "".join(f"+{s}\n" for s in src))
    got = {f.line: f.fix for f in run_static(files, Config(), lambda p: "\n".join(src) + "\n")
           if f.rule == "missing-await"}
    assert got == {3: "  await save(req.body);", 6: "  await fetch('/ping');", 10: None}
