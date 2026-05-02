import json
import subprocess
from pathlib import Path

import pytest

from review_bot.cli import build_review_comments, main
from review_bot.diff import parse_diff
from review_bot.findings import Finding

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def no_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)


def test_diff_file_json_output_and_fail_on(capsys):
    rc = main(["diff", "--file", str(FIX / "buggy.diff"), "--format", "json", "--fail-on", "high"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert {"secret", "sql-concat", "eval-exec"} <= {f["rule"] for f in out}


def test_threshold_filters(capsys):
    main(["diff", "--file", str(FIX / "buggy.diff"), "--format", "json", "--threshold", "critical"])
    out = json.loads(capsys.readouterr().out)
    assert out and all(f["severity"] == "critical" for f in out)


def test_writes_sarif_and_markdown(tmp_path):
    sarif, md = tmp_path / "r.sarif", tmp_path / "r.md"
    main(["diff", "--file", str(FIX / "buggy.diff"), "--sarif", str(sarif), "--markdown", str(md)])
    assert json.loads(sarif.read_text())["version"] == "2.1.0"
    assert "static only" in md.read_text()


def test_local_git_diff_with_ast(tmp_path, monkeypatch, capsys):
    run = lambda *a: subprocess.run(a, cwd=tmp_path, check=True, capture_output=True)  # noqa: E731
    run("git", "init", "-q")
    run("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init")
    (tmp_path / "m.py").write_text("def f(a, b=[]):\n    return a is 5\n")
    run("git", "add", "m.py")
    monkeypatch.chdir(tmp_path)
    main(["diff", "--format", "json"])
    rules = {f["rule"] for f in json.loads(capsys.readouterr().out)}
    assert {"mutable-default", "is-literal", "missing-tests"} <= rules


def test_review_comments_split_by_visibility():
    files = parse_diff("--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,3 @@\n a\n+b\n c\n")
    fs = [Finding("x.py", 2, "high", "bug", "m"), Finding("x.py", 40, "high", "bug", "far"),
          Finding("y.py", 1, "low", "t", "other file")]
    comments, rest = build_review_comments(files, fs)
    assert [(c["path"], c["line"], c["side"]) for c in comments] == [("x.py", 2, "RIGHT")]
    assert [f.message for f in rest] == ["far", "other file"]


def test_bad_pr_ref_exits_2(capsys):
    assert main(["pr", "not-a-ref"]) == 2


def test_diff_file_runs_ast_checks_on_new_files(capsys):
    main(["diff", "--file", str(FIX / "buggy.diff"), "--format", "json"])
    rules = {f["rule"] for f in json.loads(capsys.readouterr().out)}
    assert {"mutable-default", "is-literal"} <= rules


def test_review_comment_carries_suggestion_block():
    files = parse_diff("--- a/x.py\n+++ b/x.py\n@@ -1 +1,2 @@\n a\n+if x is 5:\n")
    (c,), _ = build_review_comments(files, [Finding("x.py", 2, "medium", "c", "m", fix="if x == 5:")])
    assert c["body"].endswith("```suggestion\nif x == 5:\n```")


def test_baseline_suppresses_known_findings_only(tmp_path, monkeypatch, capsys):
    run = lambda *a: subprocess.run(a, cwd=tmp_path, check=True, capture_output=True)  # noqa: E731
    run("git", "init", "-q")
    (tmp_path / "app.js").write_text("function f(a) {\n  console.log(a);\n  return a == 1;\n}\n")
    run("git", "add", "app.js")
    run("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    monkeypatch.chdir(tmp_path)

    assert main(["baseline", "--no-llm"]) == 0
    assert "recorded 3 finding(s) in .reviewbot-baseline.json" in capsys.readouterr().out
    # the known lines move and get re-indented; one genuinely new issue is added
    (tmp_path / "app.js").write_text(
        "// header\nfunction f(a) {\n    console.log(a);\n    return a == 1;\n}\nconsole.log(2);\n")
    main(["diff", "--no-llm", "--format", "json"])
    out = capsys.readouterr()
    got = [(f["line"], f["rule"]) for f in json.loads(out.out)]
    assert got == [(6, "debug-print")] and "suppressed 3 known" in out.err  # 2 moved + missing-tests
    main(["diff", "--no-llm", "--format", "json", "--no-baseline"])
    assert len(json.loads(capsys.readouterr().out)) == 4


def test_explicit_missing_baseline_is_an_error(tmp_path, capsys):
    assert main(["diff", "--file", str(FIX / "buggy.diff"), "--baseline", str(tmp_path / "nope.json")]) == 2


def test_polyglot_fixture_end_to_end(capsys):
    main(["diff", "--file", str(FIX / "polyglot.diff"), "--format", "json", "--no-baseline"])
    got = {(f["file"], f["line"], f["rule"]): f["fix"] for f in json.loads(capsys.readouterr().out)}
    assert got[("ui/Profile.tsx", 6, "loose-equality")] == "  if (user.id === 0) return null;"
    assert got[("ui/Profile.tsx", 11, "missing-await")] == "  await saveProfile(p);"
    assert {("ui/Profile.tsx", 5, "ts-any-export"), ("ui/Profile.tsx", 7, "unsafe-html"),
            ("svc/store.go", 2, "ignored-error"), ("core/src/lib.rs", 2, "unwrap")} <= set(got)
    assert ("ui/Profile.tsx", 2, "missing-await") not in got  # awaited call is fine
