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
