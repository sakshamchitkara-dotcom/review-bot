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
            ("svc/store.go", 2, "ignored-error"), ("svc/store.go", 3, "panic"),
            ("core/src/lib.rs", 2, "unwrap")} <= set(got)
    assert ("ui/Profile.tsx", 2, "missing-await") not in got  # awaited call is fine


def test_post_skips_comments_already_on_the_pr(monkeypatch, capsys):
    from review_bot import github

    diff = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1,3 @@\n a\n+import pdb\n+eval(y)\n"
    meta = {"head": {"sha": "abc", "repo": {"full_name": "me/r"}}}
    posted = []
    monkeypatch.setattr(github, "get_token", lambda: "tok")
    monkeypatch.setattr(github, "assert_can_post", lambda *a: None)
    monkeypatch.setattr(github, "fetch_pr", lambda *a: (meta, diff))
    monkeypatch.setattr(github, "fetch_file", lambda *a: None)
    monkeypatch.setattr(github, "post_review", lambda o, r, n, sha, body, comments, tok: posted.append(comments) or "u")

    already = set()
    monkeypatch.setattr(github, "existing_feedback", lambda *a: (already, set()))
    main(["pr", "me/r#1", "--post", "--no-llm", "--no-baseline", "--threshold", "medium"])
    assert [c["line"] for c in posted[0]] == [3]
    already.add(("x.py", 3, posted[0][0]["body"].split("\n")[0]))  # same headline; advice text may differ
    main(["pr", "me/r#1", "--post", "--no-llm", "--no-baseline", "--threshold", "medium"])
    assert len(posted) == 1 and "nothing new to post" in capsys.readouterr().err


def test_explain_lists_and_describes_rules(capsys):
    assert main(["explain"]) == 0
    assert "unquoted-rm" in capsys.readouterr().out
    assert main(["explain", "unquoted-rm"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("unquoted-rm (high; shell)") and '"${BUILD:?}"' in out
    assert main(["explain", "unwrapp"]) == 2
    assert "Did you mean: unwrap" in capsys.readouterr().err


def test_every_emitted_rule_is_documented():
    import re

    from review_bot import static
    from review_bot.rules import RULES

    emitted = set(re.findall(r'rule="([\w-]+)"', Path(static.__file__).read_text()))
    assert emitted and emitted | {"llm"} == set(RULES)


def test_review_body_has_severity_counts_and_leftovers_are_not_reposted(monkeypatch, capsys):
    from review_bot import github

    # line 3 is visible (inline); missing-tests is anchored to line 2, also visible; the LLM-style
    # finding on line 50 can't be anchored so it goes in the body
    diff = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1,3 @@\n a\n+import pdb\n+eval(y)\n"
    meta = {"head": {"sha": "abc", "repo": {"full_name": "me/r"}}}
    posted, bodies = [], set()
    monkeypatch.setattr(github, "get_token", lambda: "tok")
    monkeypatch.setattr(github, "assert_can_post", lambda *a: None)
    monkeypatch.setattr(github, "fetch_pr", lambda *a: (meta, diff))
    monkeypatch.setattr(github, "fetch_file", lambda *a: None)
    monkeypatch.setattr(github, "existing_feedback",
                        lambda *a: ({(c["path"], c["line"], c["body"].split("\n")[0]) for p in posted for c in p[1]},
                                    bodies))

    def post(o, r, n, sha, body, comments, tok):
        posted.append((body, comments))
        bodies.add(body)
        return "u"

    monkeypatch.setattr(github, "post_review", post)
    import review_bot.cli as cli
    real_review = cli.review
    monkeypatch.setattr(cli, "review", lambda *a, **k: (
        real_review(*a, **k)[0] + [Finding("x.py", 50, "high", "bug", "far away")], "static only"))

    main(["pr", "me/r#1", "--post", "--no-llm", "--no-baseline"])
    body, comments = posted[0]
    assert "4 finding(s): 2 high, 2 low" in body
    assert "| critical | high | medium | low | info |\n|---|---|---|---|---|\n| 0 | 2 | 0 | 2 | 0 |" in body
    assert "3 new inline comment(s); 0 already posted." in body
    assert "### Not on a line in the diff" in body and "far away" in body
    main(["pr", "me/r#1", "--post", "--no-llm", "--no-baseline"])
    assert len(posted) == 1 and "nothing new to post" in capsys.readouterr().err


def _fake_pr(monkeypatch, post):
    from review_bot import github

    diff = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1,2 @@\n a\n+eval(y)\n"
    monkeypatch.setattr(github, "get_token", lambda: "tok")
    monkeypatch.setattr(github, "assert_can_post", lambda *a: None)
    monkeypatch.setattr(github, "fetch_pr", lambda *a: ({"head": {"sha": "abc", "repo": None}}, diff))
    monkeypatch.setattr(github, "fetch_file", lambda *a: None)
    monkeypatch.setattr(github, "existing_feedback", lambda *a: (set(), set()))
    monkeypatch.setattr(github, "post_review", post)


def test_read_only_token_warns_instead_of_failing(monkeypatch, tmp_path, capsys):
    from review_bot.github import GitHubError

    def forbidden(*a):
        raise GitHubError("POST /repos/me/r/pulls/1/reviews -> HTTP 403: Resource not accessible by integration", 403)

    _fake_pr(monkeypatch, forbidden)
    sarif = tmp_path / "r.sarif"
    assert main(["pr", "me/r#1", "--post", "--no-llm", "--sarif", str(sarif), "--fail-on", "critical"]) == 0
    assert "could not post the review (HTTP 403" in capsys.readouterr().err and sarif.is_file()
    assert main(["pr", "me/r#1", "--post", "--no-llm", "--fail-on", "high"]) == 1  # gating still applies

    def broken(*a):
        raise GitHubError("POST … -> HTTP 422: bad line", 422)

    _fake_pr(monkeypatch, broken)
    assert main(["pr", "me/r#1", "--post", "--no-llm"]) == 2  # other API errors still fail loudly


def test_per_path_overrides_end_to_end(tmp_path, capsys):
    cfg = tmp_path / "c.toml"
    cfg.write_text('[[overrides]]\npaths = ["legacy/*"]\nseverity_threshold = "high"\n'
                   '[overrides.rules]\nsql-concat = false\n[[overrides]]\npaths = ["app/*"]\n'
                   '[overrides.rules]\nmissing-tests = false\n')
    diff = ("diff --git a/legacy/a.py b/legacy/a.py\n--- a/legacy/a.py\n+++ b/legacy/a.py\n@@ -1 +1,4 @@\n a\n"
            "+print(x)\n+q = \"SELECT * FROM t WHERE id=\" + i\n+eval(y)\n"
            "diff --git a/app/b.py b/app/b.py\n--- a/app/b.py\n+++ b/app/b.py\n@@ -1 +1,2 @@\n a\n+print(x)\n")
    d = tmp_path / "x.diff"
    d.write_text(diff)
    main(["diff", "--file", str(d), "--config", str(cfg), "--format", "json", "--no-baseline"])
    got = {(f["file"], f["rule"]) for f in json.loads(capsys.readouterr().out)}
    # legacy/: only high and up, no sql-concat, and the diff's missing-tests (anchored in legacy/) is low
    assert got == {("legacy/a.py", "eval-exec"), ("app/b.py", "debug-print")}


def test_fork_pr_reads_head_repo_and_survives_read_only_token(monkeypatch, capsys):
    """Fork PR inside the base repo's Actions run: posting is allowed by the guard, sources come from
    the fork at the head sha, and the read-only GITHUB_TOKEN's 403 on POST is a warning, not a failure."""
    from review_bot import github

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_REPOSITORY", "me/r")
    diff = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1,2 @@\n a\n+def f(a=[]): pass\n"
    meta = {"head": {"sha": "f00d", "repo": {"full_name": "forker/r-fork"}}}
    fetched, calls = [], []

    def api(path, token, method="GET", **kw):
        calls.append((method, path))
        if method == "POST":
            raise github.GitHubError(f"POST {path} -> HTTP 403: Resource not accessible by integration", 403)
        return "[]"

    monkeypatch.setattr(github, "get_token", lambda: "read-only")
    monkeypatch.setattr(github, "_api", api)
    monkeypatch.setattr(github, "fetch_pr", lambda *a: (meta, diff))
    monkeypatch.setattr(github, "fetch_file", lambda *a: fetched.append(a[:4]) or "a\ndef f(a=[]): pass\n")
    assert main(["pr", "me/r#9", "--post", "--no-llm", "--no-baseline", "--format", "json"]) == 0
    out = capsys.readouterr()
    assert "mutable-default" in out.out  # AST check ran on the fork's file
    assert set(fetched) == {("forker", "r-fork", "x.py", "f00d")}  # fetched once, from the fork
    assert ("POST", "/repos/me/r/pulls/9/reviews") in calls
    assert "could not post the review (HTTP 403" in out.err
