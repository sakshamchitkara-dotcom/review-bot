import json

import pytest

from review_bot import github
from review_bot.github import GitHubError, assert_can_post, parse_pr_ref


def test_parse_pr_ref():
    assert parse_pr_ref("octo/cat#12") == ("octo", "cat", 12)
    assert parse_pr_ref("https://github.com/octo/cat.js/pull/7") == ("octo", "cat.js", 7)
    with pytest.raises(ValueError):
        parse_pr_ref("octo/cat")


def _fake_api(owner, admin, viewer):
    def api(path, token, **kw):
        if path == "/user":
            return json.dumps({"login": viewer})
        return json.dumps({"owner": {"login": owner}, "permissions": {"admin": admin}})
    return api


@pytest.fixture(autouse=True)
def no_actions_env(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)


def test_posting_allowed_for_owner(monkeypatch):
    monkeypatch.setattr(github, "_api", _fake_api("me", False, "me"))
    assert_can_post("me", "r", "tok")


def test_posting_refused_for_foreign_repo(monkeypatch):
    monkeypatch.setattr(github, "_api", _fake_api("someone", False, "me"))
    with pytest.raises(GitHubError, match="refusing"):
        assert_can_post("someone", "r", "tok")


def test_posting_allowed_for_admin(monkeypatch):
    monkeypatch.setattr(github, "_api", _fake_api("org", True, "me"))
    assert_can_post("org", "r", "tok")


def test_posting_allowed_inside_own_actions_run(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_REPOSITORY", "org/r")
    monkeypatch.setattr(github, "_api", lambda *a, **k: pytest.fail("no API call expected"))
    assert_can_post("org", "r", "tok")


def test_posting_requires_token():
    with pytest.raises(GitHubError):
        assert_can_post("me", "r", None)
