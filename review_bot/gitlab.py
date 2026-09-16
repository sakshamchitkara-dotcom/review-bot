"""Minimal GitLab REST client (stdlib only): read merge requests."""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

from .diff import file_patch

DEFAULT_HOST = "https://gitlab.com"


class GitLabError(RuntimeError):
    def __init__(self, msg: str, status: int | None = None):
        super().__init__(msg)
        self.status = status


def get_token() -> str | None:
    return os.environ.get("GITLAB_TOKEN") or None


def parse_mr_ref(ref: str) -> tuple[str, str, int]:
    """(host, project path, iid) from group/project!N (host: $GITLAB_URL or gitlab.com) or an MR URL."""
    ref = ref.strip()
    if ref.startswith(("https://", "http://")):
        m = re.fullmatch(r"(https?://[^/]+)/(.+?)/-/merge_requests/(\d+)"
                         r"(?:/(?:diffs|commits|pipelines)(?:/[^\s]*)?)?/?", re.split(r"[?#]", ref, maxsplit=1)[0])
        if m:
            return m.group(1), m.group(2), int(m.group(3))
    elif m := re.fullmatch(r"([\w.-]+(?:/[\w.-]+)+)!(\d+)", ref):
        return os.environ.get("GITLAB_URL", DEFAULT_HOST).rstrip("/"), m.group(1), int(m.group(2))
    raise ValueError(f"expected group/project!N or a GitLab merge request URL, got {ref!r}")


def _pid(project: str | int) -> str:
    return urllib.parse.quote(str(project), safe="")


def _api(host: str, path: str, token: str | None, *, method: str = "GET", body: dict | None = None) -> str:
    req = urllib.request.Request(host + "/api/v4" + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"User-Agent": "review-bot"})
    if token:
        req.add_header("PRIVATE-TOKEN", token)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise GitLabError(f"{method} {path} -> HTTP {e.code}: {detail}", e.code) from None


def _paged(host: str, path: str, token: str | None) -> list[dict]:
    out, page = [], 1
    while True:
        batch = json.loads(_api(host, f"{path}?per_page=100&page={page}", token))
        out += batch
        if len(batch) < 100:
            return out
        page += 1


def fetch_mr(host: str, project: str, iid: int, token: str | None) -> tuple[dict, str]:
    """MR metadata and a unified diff rebuilt from `merge_requests/:iid/diffs` (GitLab 15.7+)."""
    base = f"/projects/{_pid(project)}/merge_requests/{iid}"
    meta = json.loads(_api(host, base, token))
    out, skipped = [], []
    for d in _paged(host, f"{base}/diffs", token):
        if not d.get("diff") or d.get("too_large"):  # binary, collapsed or over GitLab's per-file limit
            skipped.append(d["new_path"])
            continue
        out.append(file_patch(d["old_path"], d["new_path"], d["diff"],
                              added=d.get("new_file", False), deleted=d.get("deleted_file", False)))
    if skipped:
        print(f"review-bot: skipped {len(skipped)} file(s) GitLab returned no diff for (binary or too large): "
              + ", ".join(skipped[:5]) + (" …" if len(skipped) > 5 else ""), file=sys.stderr)
    return meta, "\n".join(out) + "\n"


def head_sha(meta: dict) -> str:
    return (meta.get("diff_refs") or {}).get("head_sha") or meta["sha"]


def fetch_file(host: str, project: str | int, path: str, ref: str, token: str | None) -> str | None:
    try:
        return _api(host, f"/projects/{_pid(project)}/repository/files/{_pid(path)}/raw?ref={ref}", token)
    except GitLabError:
        return None
