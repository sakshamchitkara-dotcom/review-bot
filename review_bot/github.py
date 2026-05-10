"""Minimal GitHub REST client (stdlib only): fetch PR diffs, post PR reviews."""
from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request

API = os.environ.get("GITHUB_API_URL", "https://api.github.com")


class GitHubError(RuntimeError):
    pass


def get_token() -> str | None:
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        return tok
    try:
        out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None


def parse_pr_ref(ref: str) -> tuple[str, str, int]:
    m = re.fullmatch(r"(?:https://github\.com/)?([\w.-]+)/([\w.-]+)(?:#|/pull/)(\d+)/?", ref.strip())
    if not m:
        raise ValueError(f"expected owner/repo#N or a PR URL, got {ref!r}")
    return m.group(1), m.group(2), int(m.group(3))


def _api(path: str, token: str | None, *, accept: str = "application/vnd.github+json",
         method: str = "GET", body: dict | None = None) -> str:
    req = urllib.request.Request(
        API + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Accept": accept, "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "review-bot"},
    )
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise GitHubError(f"{method} {path} -> HTTP {e.code}: {detail}") from None


def fetch_pr(owner: str, repo: str, number: int, token: str | None) -> tuple[dict, str]:
    base = f"/repos/{owner}/{repo}/pulls/{number}"
    meta = json.loads(_api(base, token))
    diff = _api(base, token, accept="application/vnd.github.diff")
    return meta, diff


def fetch_file(owner: str, repo: str, path: str, ref: str, token: str | None) -> str | None:
    q = urllib.parse.quote(path)
    try:
        return _api(f"/repos/{owner}/{repo}/contents/{q}?ref={ref}", token, accept="application/vnd.github.raw")
    except GitHubError:
        return None


def assert_can_post(owner: str, repo: str, token: str | None) -> None:
    """Refuse to post unless the token's owner controls the repo.

    Allowed: the authenticated user owns the repo or has admin on it, or we're running
    inside that same repo's GitHub Actions workflow (its GITHUB_TOKEN is scoped to it).
    """
    if not token:
        raise GitHubError("posting requires a GitHub token")
    full = f"{owner}/{repo}"
    if os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("GITHUB_REPOSITORY", "").lower() == full.lower():
        return
    info = json.loads(_api(f"/repos/{full}", token))
    viewer = json.loads(_api("/user", token)).get("login", "")
    if info.get("owner", {}).get("login", "").lower() == viewer.lower() or info.get("permissions", {}).get("admin"):
        return
    raise GitHubError(f"refusing to post: {viewer or 'token owner'} does not own or administer {full}")


def _paged(path: str, token: str | None) -> list[dict]:
    out, page = [], 1
    while True:
        batch = json.loads(_api(f"{path}?per_page=100&page={page}", token))
        out += batch
        if len(batch) < 100:
            return out
        page += 1


def existing_feedback(owner: str, repo: str, number: int, token: str | None) -> tuple[set, set]:
    """(path, line, body) of inline comments and bodies of reviews already on the PR."""
    base = f"/repos/{owner}/{repo}/pulls/{number}"
    comments = {(c["path"], c.get("line"), c["body"]) for c in _paged(f"{base}/comments", token)}
    bodies = {r.get("body") or "" for r in _paged(f"{base}/reviews", token)}
    return comments, bodies


def post_review(owner: str, repo: str, number: int, commit_id: str, body: str,
                comments: list[dict], token: str) -> str:
    payload = {"commit_id": commit_id, "body": body, "event": "COMMENT", "comments": comments}
    res = json.loads(_api(f"/repos/{owner}/{repo}/pulls/{number}/reviews", token, method="POST", body=payload))
    return res.get("html_url", "")
