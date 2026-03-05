"""review-bot command line."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from . import __version__
from .config import Config, load_config
from .diff import FileDiff, parse_diff
from .findings import SEVERITIES, Finding, severity_rank, suggestion_block
from .llm import make_client, run_llm, warn
from .report import to_markdown, to_sarif, to_terminal
from .static import run_static


def git_diff(base: str | None, staged: bool) -> str:
    cmd = ["git", "diff", "--no-color", "--no-ext-diff", "-U3"]
    if staged:
        cmd.append("--cached")
    cmd.append(base or "HEAD")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"review-bot: git diff failed: {r.stderr.strip()}")
    return r.stdout


def git_root() -> Path:
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return Path(r.stdout.strip()) if r.returncode == 0 else Path.cwd()


def review(files: list[FileDiff], cfg: Config, get_source, *, use_llm: bool, verify: bool,
           min_conf: float) -> tuple[list[Finding], str]:
    files = [f for f in files if not cfg.ignored(f.path)]
    findings = run_static(files, cfg, get_source)
    mode = "static only"
    if use_llm and cfg.llm:
        client = make_client()
        if client is None:
            warn("ANTHROPIC_API_KEY not set; running static checks only")
        else:
            import anthropic

            try:
                findings += run_llm(client, cfg.model, files, max_files=cfg.max_llm_files,
                                    min_conf=min_conf, verify=verify, known=findings)
                mode = f"static + {cfg.model}"
            except anthropic.AuthenticationError:
                warn("Anthropic authentication failed; falling back to static checks only")
    floor = severity_rank(cfg.severity_threshold)
    return [f for f in findings if severity_rank(f.severity) >= floor], mode


def emit(findings: list[Finding], args, title: str) -> None:
    fmt = args.format
    if fmt == "terminal":
        text = to_terminal(findings, color=sys.stdout.isatty() and not args.output)
    elif fmt == "markdown":
        text = to_markdown(findings, title)
    elif fmt == "sarif":
        text = to_sarif(findings)
    else:
        text = json.dumps([f.to_dict() for f in findings], indent=2)
    if args.output:
        Path(args.output).write_text(text + "\n")
    else:
        print(text)
    if args.sarif:
        Path(args.sarif).write_text(to_sarif(findings) + "\n")
    if args.markdown:
        Path(args.markdown).write_text(to_markdown(findings, title))


def cmd_diff(args, cfg: Config) -> list[Finding]:
    if args.file:
        text = sys.stdin.read() if args.file == "-" else Path(args.file).read_text()
        root = Path.cwd()
    else:
        text = git_diff(args.base, args.staged)
        root = git_root()

    files = parse_diff(text)
    new_files = {f.path: f for f in files if f.is_new}

    def get_source(path: str) -> str | None:
        if args.file and path in new_files:  # a new file's added lines are the whole file
            fd = new_files[path]
            return "\n".join(fd.added[i] for i in sorted(fd.added)) + "\n"
        p = root / path
        return p.read_text(errors="replace") if p.is_file() else None

    findings, mode = review(files, cfg, get_source, use_llm=not args.no_llm,
                            verify=not args.no_verify, min_conf=args.min_confidence)
    emit(findings, args, f"review-bot ({mode})")
    return findings


def cmd_pr(args, cfg: Config) -> list[Finding]:
    from . import github

    owner, repo, number = github.parse_pr_ref(args.ref)
    token = github.get_token()
    if args.post:
        github.assert_can_post(owner, repo, token)  # fail fast, before spending LLM tokens
    meta, text = github.fetch_pr(owner, repo, number, token)
    head_sha = meta["head"]["sha"]
    head_repo = (meta["head"].get("repo") or {}).get("full_name", f"{owner}/{repo}")
    h_owner, h_repo = head_repo.split("/", 1)

    def get_source(path: str) -> str | None:
        return github.fetch_file(h_owner, h_repo, path, head_sha, token)

    files = parse_diff(text)
    findings, mode = review(files, cfg, get_source, use_llm=not args.no_llm,
                            verify=not args.no_verify, min_conf=args.min_confidence)
    title = f"review-bot ({mode}) on {owner}/{repo}#{number}"
    emit(findings, args, title)
    if args.post:
        comments, body_extra = build_review_comments(files, findings)
        body = to_markdown(body_extra, title) if body_extra else f"## {title}\n\n{len(comments)} inline comment(s)."
        url = github.post_review(owner, repo, number, head_sha, body, comments, token)
        print(f"review-bot: posted review {url}", file=sys.stderr)
    return findings


def build_review_comments(files: list[FileDiff], findings: list[Finding]) -> tuple[list[dict], list[Finding]]:
    """Split findings into inline comments (line visible in the diff) and leftovers for the review body."""
    visible = {f.path: f.visible_lines() for f in files}
    comments, rest = [], []
    for f in findings:
        if f.line in visible.get(f.file, ()):
            body = f"**{f.severity.upper()}** ({f.category}) {f.message}"
            if f.suggestion:
                body += f"\n\n_Suggestion:_ {f.suggestion}"
            if f.fix is not None:
                body += "\n\n" + suggestion_block(f.fix)
            comments.append({"path": f.file, "line": f.line, "side": "RIGHT", "body": body})
        else:
            rest.append(f)
    return comments, rest


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="path to .reviewbot.toml (default: ./.reviewbot.toml if present)")
    common.add_argument("--format", choices=["terminal", "markdown", "sarif", "json"], default="terminal")
    common.add_argument("-o", "--output", help="write the main report to this file instead of stdout")
    common.add_argument("--sarif", metavar="FILE", help="also write a SARIF report")
    common.add_argument("--markdown", metavar="FILE", help="also write a Markdown report")
    common.add_argument("--no-llm", action="store_true", help="static checks only")
    common.add_argument("--no-verify", action="store_true", help="skip the LLM verification pass")
    common.add_argument("--min-confidence", type=float, default=0.6, help="verify-pass keep threshold (0-1)")
    common.add_argument("--threshold", choices=SEVERITIES, help="override severity_threshold")
    common.add_argument("--fail-on", choices=SEVERITIES, help="exit 1 if any finding is at/above this severity")

    p = argparse.ArgumentParser(prog="review-bot", description="Autonomous AI code reviewer.")
    p.add_argument("--version", action="version", version=f"review-bot {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("diff", parents=[common], help="review a local git diff")
    d.add_argument("base", nargs="?", help="ref to diff the working tree against (default: HEAD)")
    d.add_argument("--staged", action="store_true", help="review staged changes only")
    d.add_argument("--file", help="read a unified diff from FILE ('-' for stdin) instead of running git")
    r = sub.add_parser("pr", parents=[common], help="review a GitHub pull request")
    r.add_argument("ref", help="owner/repo#N or PR URL")
    r.add_argument("--post", action="store_true",
                   help="post findings as a PR review (off by default; only on repos you own/admin)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config)
    if args.threshold:
        cfg.severity_threshold = args.threshold
    try:
        findings = cmd_diff(args, cfg) if args.cmd == "diff" else cmd_pr(args, cfg)
    except Exception as e:  # noqa: BLE001 - top-level: report cleanly, non-zero exit
        from .github import GitHubError

        if isinstance(e, (GitHubError, ValueError, FileNotFoundError)):
            print(f"review-bot: {e}", file=sys.stderr)
            return 2
        raise
    if args.fail_on and any(severity_rank(f.severity) >= severity_rank(args.fail_on) for f in findings):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
