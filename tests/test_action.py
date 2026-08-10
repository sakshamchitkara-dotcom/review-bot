"""The composite Action's review step, and the baseline/cache behaviour it relies on."""
import json
import os
import subprocess
from pathlib import Path

import pytest

from review_bot.cli import main

ROOT = Path(__file__).parent.parent


def review_step_script() -> str:
    """The `run: |` body of the "Review pull request" step in action.yml (no YAML dependency)."""
    lines = (ROOT / "action.yml").read_text().splitlines()
    i = lines.index("    - name: Review pull request")
    i = next(j for j in range(i, len(lines)) if lines[j].strip() == "run: |")
    body = []
    for ln in lines[i + 1:]:
        if ln.strip() and not ln.startswith(" " * 8):
            break
        body.append(ln[8:])
    return "\n".join(body) + "\n"


STUB = """#!/usr/bin/env bash
printf '%s\\n' "$@" > argv.txt
echo "cache=$REVIEW_BOT_CACHE" > env.txt
echo "## review-bot (static only)" > review-bot.md
exit "${STUB_RC:-0}"
"""


def run_step(tmp_path, **env):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "review-bot"
    stub.write_text(STUB)
    stub.chmod(0o755)
    summary = tmp_path / "summary.md"
    full = {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "GITHUB_STEP_SUMMARY": str(summary),
            "REVIEW_BOT_CACHE": "/runner/temp/review-bot-cache", "RB_PR": "me/r#7", "RB_POST": "false",
            "RB_FAIL_ON": "", "RB_CONFIG": ".reviewbot.toml", "RB_EXTRA": "", **env}
    # the same shell GitHub uses for `shell: bash`
    r = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", review_step_script()],
                       cwd=tmp_path, env=full, capture_output=True, text=True)
    argv = (tmp_path / "argv.txt").read_text().split("\n")[:-1]
    return r.returncode, argv, summary.read_text() if summary.exists() else None, r.stdout


def test_action_step_minimal_args(tmp_path):
    rc, argv, summary, _ = run_step(tmp_path)
    assert rc == 0
    assert argv == ["pr", "me/r#7", "--sarif", "review-bot.sarif", "--markdown", "review-bot.md"]
    assert summary == "## review-bot (static only)\n"
    assert (tmp_path / "env.txt").read_text() == "cache=/runner/temp/review-bot-cache\n"


def test_action_step_all_inputs_and_exit_code(tmp_path):
    (tmp_path / ".reviewbot.toml").write_text("")
    rc, argv, summary, _ = run_step(tmp_path, RB_POST="true", RB_FAIL_ON="high", RB_EXTRA="--no-llm --threshold medium",
                                 STUB_RC="1")
    assert rc == 1  # fail-on result propagates...
    assert summary is not None  # ...after the report reached the job summary
    assert argv[6:] == ["--config", ".reviewbot.toml", "--post", "--fail-on", "high", "--no-llm",
                        "--threshold", "medium"]


def test_action_step_annotations_input(tmp_path):
    _, argv, _, _ = run_step(tmp_path, RB_ANNOTATIONS="true")
    assert argv[6:] == ["--format", "github"]
    assert "--format" not in run_step(tmp_path, RB_ANNOTATIONS="false")[1]


def test_action_step_notices_duplicate_feedback(tmp_path):
    rc, _, _, stdout = run_step(tmp_path, RB_POST="true", RB_UPLOAD="true")
    assert rc == 0 and "::notice title=review-bot::post and upload-sarif are both on" in stdout
    assert "::notice" not in run_step(tmp_path, RB_POST="true", RB_UPLOAD="false")[3]


@pytest.fixture
def pr_env(tmp_path, monkeypatch):
    """A checked-out workspace (cwd) and a PR served from fakes, as inside the Action."""
    from review_bot import github

    diff = ("diff --git a/app.js b/app.js\n--- a/app.js\n+++ b/app.js\n@@ -1 +1,3 @@\n a\n"
            "+console.log(a);\n+if (a == 1) run();\n")
    monkeypatch.setattr(github, "get_token", lambda: "tok")
    monkeypatch.setattr(github, "fetch_pr", lambda *a: ({"head": {"sha": "abc", "repo": None}}, diff))
    monkeypatch.setattr(github, "fetch_file", lambda *a: None)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_checked_out_baseline_is_applied_to_pr_reviews(pr_env, monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    (pr_env / "app.js").write_text("a\nconsole.log(a);\n")  # the known finding, as committed on main
    subprocess.run(["git", "init", "-q"], cwd=pr_env, check=True)
    subprocess.run(["git", "add", "app.js"], cwd=pr_env, check=True)
    assert main(["baseline", "--no-llm"]) == 0
    capsys.readouterr()

    main(["pr", "me/r#7", "--format", "json"])
    out = capsys.readouterr()
    assert [(f["line"], f["rule"]) for f in json.loads(out.out)] == [(3, "loose-equality")]
    assert "baseline: suppressed 2 known finding(s)" in out.err  # debug-print + missing-tests


def test_pr_reviews_reuse_the_cache_dir_from_the_environment(pr_env, monkeypatch, capsys):
    from test_llm import FakeClient

    from review_bot import cli

    cache = pr_env / "runner-temp" / "review-bot-cache"
    monkeypatch.setenv("REVIEW_BOT_CACHE", str(cache))  # what the Action sets for actions/cache
    review = {"findings": [{"line": 3, "severity": "high", "category": "bug", "message": "runs twice",
                            "suggestion": "", "fix": ""}]}
    verify = {"verdicts": [{"id": 0, "keep": True, "confidence": 0.9, "reason": "real"}]}
    clients = []
    monkeypatch.setattr(cli, "make_client", lambda: clients.append(FakeClient(review, verify)) or clients[-1])

    for _ in range(2):
        main(["pr", "me/r#7", "--format", "json", "--no-baseline"])
        assert "runs twice" in capsys.readouterr().out
    assert len(clients[0].calls) == 2 and clients[1].calls == []
    assert len(list(cache.glob("*.json"))) == 1
