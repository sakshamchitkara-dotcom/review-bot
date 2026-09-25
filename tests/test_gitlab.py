import json

import pytest

from review_bot import gitlab
from review_bot.cli import main
from review_bot.gitlab import parse_mr_ref


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ("GITLAB_URL", "GITLAB_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(k, raising=False)


@pytest.mark.parametrize("ref,want", [
    ("grp/proj!12", ("https://gitlab.com", "grp/proj", 12)),
    ("grp/sub/proj.js!3", ("https://gitlab.com", "grp/sub/proj.js", 3)),
    ("https://gitlab.com/grp/sub/proj/-/merge_requests/7", ("https://gitlab.com", "grp/sub/proj", 7)),
    ("https://git.example.org/a/b/-/merge_requests/7/diffs?commit_id=abc#note_1",
     ("https://git.example.org", "a/b", 7)),
])
def test_parse_mr_ref(ref, want):
    assert parse_mr_ref(ref) == want


def test_parse_mr_ref_uses_gitlab_url_and_rejects_github_refs(monkeypatch):
    monkeypatch.setenv("GITLAB_URL", "https://git.corp/")
    assert parse_mr_ref("a/b!1")[0] == "https://git.corp"
    for bad in ("a/b#1", "https://github.com/a/b/pull/1", "proj!1"):
        with pytest.raises(ValueError):
            parse_mr_ref(bad)


META = {"iid": 4, "sha": "old", "source_project_id": 99, "web_url": "https://gitlab.com/g/p/-/merge_requests/4",
        "diff_refs": {"base_sha": "b", "head_sha": "h3ad", "start_sha": "s"}}
DIFFS = [
    {"old_path": "app.py", "new_path": "app.py", "diff": "@@ -1,2 +1,3 @@\n import os\n+eval(x)\n y\n",
     "new_file": False, "deleted_file": False, "too_large": False},
    {"old_path": "m.py", "new_path": "m.py", "diff": "@@ -0,0 +1,2 @@\n+def f(a=[]):\n+    return a\n",
     "new_file": True, "deleted_file": False, "too_large": False},
    {"old_path": "logo.png", "new_path": "logo.png", "diff": "", "new_file": True, "deleted_file": False},
    {"old_path": "big.sql", "new_path": "big.sql", "diff": "", "too_large": True},
]


@pytest.fixture
def fake_gitlab(monkeypatch):
    calls = []

    def api(host, path, token, method="GET", body=None):
        calls.append((host, method, path, token))
        if path.startswith("/projects/g%2Fp/merge_requests/4/diffs"):
            return json.dumps(DIFFS)
        if path == "/projects/g%2Fp/merge_requests/4":
            return json.dumps(META)
        if path.startswith("/projects/99/repository/files/m.py/raw"):
            return "def f(a=[]):\n    return a\n"
        raise gitlab.GitLabError(f"{method} {path} -> HTTP 404", 404)

    monkeypatch.setattr(gitlab, "_api", api)
    return calls


def test_mr_review_reads_diffs_and_sources(fake_gitlab, capsys, monkeypatch):
    monkeypatch.setenv("GITLAB_TOKEN", "glpat-test")
    assert main(["mr", "https://gitlab.com/g/p/-/merge_requests/4", "--no-llm", "--no-baseline",
                 "--format", "json", "--fail-on", "high"]) == 1
    out = capsys.readouterr()
    got = {(f["file"], f["line"], f["rule"]) for f in json.loads(out.out)}
    assert {("app.py", 2, "eval-exec"), ("m.py", 1, "mutable-default")} <= got
    assert "skipped 2 file(s)" in out.err and "logo.png, big.sql" in out.err
    # sources come from the source project at the head sha, fetched once each
    raw = [c for c in fake_gitlab if "/repository/files/" in c[2]]
    assert raw and all("/projects/99/" in c[2] and c[2].endswith("?ref=h3ad") for c in raw)
    assert len(raw) == len({c[2] for c in raw})
    assert {c[3] for c in fake_gitlab} == {"glpat-test"} and all(c[1] == "GET" for c in fake_gitlab)


def test_mr_http_errors_exit_2(monkeypatch, capsys):
    def api(*a, **k):
        raise gitlab.GitLabError("GET /projects/g%2Fp/merge_requests/4 -> HTTP 404: Not Found", 404)

    monkeypatch.setattr(gitlab, "_api", api)
    assert main(["mr", "g/p!4", "--no-llm"]) == 2
    assert "HTTP 404" in capsys.readouterr().err
