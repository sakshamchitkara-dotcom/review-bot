"""review-bot command line."""
from __future__ import annotations

import argparse
import functools
import json
import subprocess
import sys
from pathlib import Path

from . import __version__
from . import baseline as bl
from .config import Config, load_config
from .diff import FileDiff, parse_diff
from .findings import SEVERITIES, Finding, severity_rank, suggestion_block
from .llm import default_cache_dir, make_client, run_llm, warn
from .report import severity_table, summary, to_github, to_markdown, to_sarif, to_terminal
from .static import apply_suppressions, run_static


def git_diff(base: str | None, staged: bool) -> str:
    cmd = ["git", "diff", "--no-color", "--no-ext-diff", "-U3"]
    if staged:
        cmd.append("--cached")
    if not base and subprocess.run(["git", "rev-parse", "-q", "--verify", "HEAD"], capture_output=True).returncode:
        base = EMPTY_TREE  # no commits yet (e.g. the pre-commit hook on a repo's first commit)
    cmd.append(base or "HEAD")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"review-bot: git diff failed: {r.stderr.strip()}")
    return r.stdout


def git_root() -> Path:
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return Path(r.stdout.strip()) if r.returncode == 0 else Path.cwd()


def review(files: list[FileDiff], cfg: Config, get_source, *, use_llm: bool, verify: bool,
           min_conf: float, cache_dir: Path | None = None, known=None) -> tuple[list[Finding], str]:
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
                                    min_conf=min_conf, verify=verify, known=findings, cache_dir=cache_dir)
                mode = f"static + {cfg.model}"
            except anthropic.AuthenticationError:
                warn("Anthropic authentication failed; falling back to static checks only")
    findings, n = apply_suppressions(findings, files, get_source)
    if n:
        warn(f"inline ignore: suppressed {n} finding(s)")
    findings = [f for f in findings if (c := cfg.for_path(f.file)).rule_on(f.rule or f.category)
                and severity_rank(f.severity) >= severity_rank(c.severity_threshold)]
    if known:
        before = len(findings)
        findings = bl.new_only(findings, files, known)
        warn(f"baseline: suppressed {before - len(findings)} known finding(s)")
    return findings, mode


def _cache_dir(args) -> Path | None:
    return None if args.no_cache else Path(args.cache_dir) if args.cache_dir else default_cache_dir()


def emit(findings: list[Finding], args, title: str) -> None:
    fmt = args.format
    if fmt == "terminal":
        text = to_terminal(findings, color=sys.stdout.isatty() and not args.output)
    elif fmt == "markdown":
        text = to_markdown(findings, title)
    elif fmt == "sarif":
        text = to_sarif(findings)
    elif fmt == "github":
        text = to_github(findings)
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


def _baseline(args):
    """Counter of known fingerprints, or None. --baseline FILE must exist; the default is optional."""
    if args.no_baseline:
        return None
    path = args.baseline or bl.DEFAULT_PATH
    if args.baseline or Path(path).is_file():
        return bl.load(path)
    return None


def local_diff(args) -> tuple[list[FileDiff], object]:
    """Parse the requested local diff and return (files, get_source)."""
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

    return files, get_source


def cmd_diff(args, cfg: Config) -> list[Finding]:
    files, get_source = local_diff(args)
    findings, mode = review(files, cfg, get_source, use_llm=not args.no_llm, verify=not args.no_verify,
                            min_conf=args.min_confidence, cache_dir=_cache_dir(args), known=_baseline(args))
    emit(findings, args, f"review-bot ({mode})")
    return findings


EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # git's well-known empty tree


def cmd_baseline(args, cfg: Config) -> list[Finding]:
    if not (args.file or args.base or args.staged):
        args.base = EMPTY_TREE  # default: every tracked file as it is now
    if not args.threshold:
        cfg.set_threshold("info")  # record everything so raising/lowering the threshold later still works
    files, get_source = local_diff(args)
    findings, _ = review(files, cfg, get_source, use_llm=not args.no_llm, verify=not args.no_verify,
                         min_conf=args.min_confidence, cache_dir=_cache_dir(args))
    path = args.baseline or bl.DEFAULT_PATH
    n = bl.save(path, findings, files)
    print(f"review-bot: recorded {n} finding(s) in {path}")
    return []


def cmd_stats(args, cfg: Config) -> list[Finding]:
    """Counts by rule and by file for the whole tree (or a ref / --staged / --file diff)."""
    from collections import Counter

    if not (args.file or args.base or args.staged):
        args.base = EMPTY_TREE
    if not args.threshold:
        cfg.set_threshold("info")
    files, get_source = local_diff(args)
    findings, _ = review(files, cfg, get_source, use_llm=not args.no_llm, verify=not args.no_verify,
                         min_conf=args.min_confidence, cache_dir=_cache_dir(args))
    by_rule = Counter(f.rule or f.category for f in findings)
    by_file = Counter(f.file for f in findings)
    worst = {}
    for f in findings:
        r = f.rule or f.category
        if severity_rank(f.severity) >= severity_rank(worst.get(r, "info")):
            worst[r] = f.severity
    if args.format == "json":
        text = json.dumps({"total": len(findings), "files_reviewed": len(files),
                           "by_severity": dict(Counter(f.severity for f in findings)),
                           "by_rule": dict(by_rule.most_common()), "by_file": dict(by_file.most_common())}, indent=2)
    else:
        w = max(map(len, by_rule), default=4)
        rows = [f"{'rule':<{w}}  {'count':>5}  {'files':>5}  worst"]
        rows += [f"{r:<{w}}  {n:>5}  {len({f.file for f in findings if (f.rule or f.category) == r}):>5}  {worst[r]}"
                 for r, n in by_rule.most_common()]
        rows += ["", "top files:"] + [f"{n:>5}  {p}" for p, n in by_file.most_common(10)]
        text = "\n".join(rows + ["", f"{len(files)} file(s) reviewed; " + summary(findings)])
    if args.output:
        Path(args.output).write_text(text + "\n")
    else:
        print(text)
    return []


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

    @functools.lru_cache(maxsize=None)  # static checks and suppressions both ask; fetch once
    def get_source(path: str) -> str | None:
        return github.fetch_file(h_owner, h_repo, path, head_sha, token)

    files = parse_diff(text)
    findings, mode = review(files, cfg, get_source, use_llm=not args.no_llm, verify=not args.no_verify,
                            min_conf=args.min_confidence, cache_dir=_cache_dir(args), known=_baseline(args))
    title = f"review-bot ({mode}) on {owner}/{repo}#{number}"
    emit(findings, args, title)
    if args.post:
        comments, body_extra = build_review_comments(files, findings)
        seen_comments, seen_bodies = github.existing_feedback(owner, repo, number, token)
        fresh = [c for c in comments if (c["path"], c["line"], github.headline(c["body"])) not in seen_comments]
        extra = to_markdown(body_extra, "Not on a line in the diff") if body_extra else ""
        body = review_body(title, findings, len(fresh), len(comments) - len(fresh), extra)
        if not fresh and (not extra or any(extra in b for b in seen_bodies)):
            print("review-bot: every finding is already on the PR; nothing new to post", file=sys.stderr)
        else:
            try:
                url = github.post_review(owner, repo, number, head_sha, body, fresh, token)
                print(f"review-bot: posted review {url}", file=sys.stderr)
            except github.GitHubError as e:
                if e.status != 403:
                    raise
                # e.g. a fork PR: Actions hands it a read-only GITHUB_TOKEN. The reports are
                # already written, so warn instead of failing the job over the comment.
                warn(f"could not post the review (HTTP 403: token lacks write access, e.g. a fork PR); "
                     f"findings are in the report only. {e}")
    return findings


def review_body(title: str, findings: list[Finding], new: int, old: int, extra: str) -> str:
    """Review summary: severity counts for the whole run, what was posted, and findings with no diff line."""
    parts = [f"## {title}", "", summary(findings), "", severity_table(findings), "",
             f"{new} new inline comment(s); {old} already posted."]
    if extra:
        parts += ["", "#" + extra]  # "## Not on…" -> "### Not on…" under the review heading
    return "\n".join(parts) + "\n"


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


def cmd_explain(rule: str | None) -> int:
    from .rules import RULES, explain

    if not rule:
        width = max(map(len, RULES))
        print("\n".join(f"{r:<{width}}  {v.severity:<13} {v.summary}" for r, v in RULES.items()))
        return 0
    if rule not in RULES:
        import difflib

        close = difflib.get_close_matches(rule, RULES, n=3)
        hint = f" Did you mean: {', '.join(close)}?" if close else " Run `review-bot explain` for the list."
        print(f"review-bot: unknown rule {rule!r}.{hint}", file=sys.stderr)
        return 2
    print(explain(rule))
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="path to .reviewbot.toml (default: ./.reviewbot.toml if present)")
    common.add_argument("--format", choices=["terminal", "markdown", "sarif", "json", "github"], default="terminal",
                        help="main report format; github = Actions annotations (::error file=…,line=…::…)")
    common.add_argument("-o", "--output", help="write the main report to this file instead of stdout")
    common.add_argument("--sarif", metavar="FILE", help="also write a SARIF report")
    common.add_argument("--markdown", metavar="FILE", help="also write a Markdown report")
    common.add_argument("--no-llm", action="store_true", help="static checks only")
    common.add_argument("--no-verify", action="store_true", help="skip the LLM verification pass")
    common.add_argument("--no-cache", action="store_true", help="always call the LLM, ignoring cached results")
    common.add_argument("--cache-dir", help="LLM result cache (default: $REVIEW_BOT_CACHE or ~/.cache/review-bot)")
    common.add_argument("--min-confidence", type=float, default=0.6, help="verify-pass keep threshold (0-1)")
    common.add_argument("--threshold", choices=SEVERITIES, help="override severity_threshold")
    common.add_argument("--fail-on", choices=SEVERITIES, help="exit 1 if any finding is at/above this severity")

    common.add_argument("--baseline", metavar="FILE",
                        help=f"baseline file (default: ./{bl.DEFAULT_PATH} if present)")
    common.add_argument("--no-baseline", action="store_true", help="report every finding, ignoring the baseline")

    p = argparse.ArgumentParser(prog="review-bot", description="Autonomous AI code reviewer.")
    p.add_argument("--version", action="version", version=f"review-bot {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("diff", parents=[common], help="review a local git diff")
    d.add_argument("base", nargs="?", help="ref to diff the working tree against (default: HEAD)")
    d.add_argument("--staged", action="store_true", help="review staged changes only")
    d.add_argument("--file", help="read a unified diff from FILE ('-' for stdin) instead of running git")
    b = sub.add_parser("baseline", parents=[common],
                       help="record current findings so later runs report only new ones")
    b.add_argument("base", nargs="?", help="record findings in the diff against this ref (default: whole tree)")
    b.add_argument("--staged", action="store_true", help="record findings in staged changes only")
    b.add_argument("--file", help="record findings from a unified diff FILE ('-' for stdin)")
    s = sub.add_parser("stats", parents=[common],
                       help="count findings by rule and file (default: whole tree), e.g. to pick rules to tune")
    s.add_argument("base", nargs="?", help="count findings in the diff against this ref (default: whole tree)")
    s.add_argument("--staged", action="store_true", help="count findings in staged changes only")
    s.add_argument("--file", help="count findings in a unified diff FILE ('-' for stdin)")
    e = sub.add_parser("explain", help="describe a rule (no argument: list all rules)")
    e.add_argument("rule", nargs="?", help="rule id, as shown in [category/rule] in the report")
    r = sub.add_parser("pr", parents=[common], help="review a GitHub pull request")
    r.add_argument("ref", help="owner/repo#N or PR URL")
    r.add_argument("--post", action="store_true",
                   help="post findings as a PR review (off by default; only on repos you own/admin)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "explain":
        return cmd_explain(args.rule)
    cfg = load_config(args.config)
    if args.threshold:
        cfg.set_threshold(args.threshold)
    try:
        findings = {"diff": cmd_diff, "pr": cmd_pr, "baseline": cmd_baseline,
                    "stats": cmd_stats}[args.cmd](args, cfg)
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
