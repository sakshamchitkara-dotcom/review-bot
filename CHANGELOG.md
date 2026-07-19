# Changelog

## 0.3.0 - 2026-09-25

### Added
- Shell rules: `curl-pipe-shell`, `unquoted-rm` (with a `"${VAR:?}"` fix); `eval` and `set -x`
  in shell; `.bash`/`.zsh` recognised.
- Java `string-equality` (with a `"lit".equals(x)` fix) and Kotlin `not-null-assertion` (`!!`);
  Kotlin is its own language, so `println(` counts as debug output.
- Ruby: `shell-injection` (`system`/backticks/`Open3` with `#{}`), ActiveRecord `sql-concat`,
  `unsafe-html` (`.html_safe`, `raw(`), `rescue Exception` / `rescue nil`.
- Inline suppressions: `reviewbot: ignore[rule, ...]` or bare `reviewbot: ignore`, on the line or
  a comment line above it.
- `review-bot explain [rule]`, backed by a registry that documents every rule.
- SARIF rule metadata (descriptions, default level, tags, `security-severity`).
- Action input `upload-sarif` uploads the SARIF to GitHub code scanning.
- PR review body now leads with the summary line and a severity-count table.
- Terminal report prints one-line fixes.

### Fixed
- Rust `#[cfg(test)]` modules inside `src/` are treated as test code.
- `missing-tests` is one finding per diff instead of one per changed file.
- A read-only token (fork PRs) no longer fails the job when posting; it warns instead.
- The Action prints a notice when `post` and `upload-sarif` would both comment on the PR.
- CI and the Action use Node 24 majors of checkout, setup-python and cache.

### Tests
- The Action's review step script is run under GitHub's bash flags with a stub review-bot;
  `pr` is tested with a checked-out baseline and with `REVIEW_BOT_CACHE` reuse.

## 0.2.0 - 2026-09-25

### Added
- JS/TS rules: `loose-equality` (`==`/`!=`, with a `===` fix), `ts-any-export`,
  `unsafe-html` (`dangerouslySetInnerHTML`, `innerHTML =`), and a `missing-await`
  heuristic for un-awaited calls to async functions and `fetch`.
- Go rules: `ignored-error` (`v, _ := f()`), `panic`. Rust rules: `unwrap`, `unsafe-block`,
  and `dbg!`/`println!` under `debug-print`.
- One-line fixes rendered as GitHub ```` ```suggestion ```` blocks in PR comments and a
  "Suggested fixes" section in the Markdown report. Static fixes for `bare-except`,
  `is-literal`, `loose-equality`, `missing-await`; the LLM returns a `fix` per finding.
- `review-bot baseline` and `--baseline` / `--no-baseline`: record current findings in
  `.reviewbot-baseline.json` and report only new ones (line-number-free fingerprints).
- LLM result cache keyed by a hash of each file's diff (`--no-cache`, `--cache-dir`,
  `$REVIEW_BOT_CACHE`); the Action persists it per PR with `actions/cache`.

### Fixed
- `--post` no longer reposts comments already on the PR; nothing is posted when there is
  nothing new (Action re-runs on every push used to duplicate the whole review). Matching is
  on path, line and the finding's headline, so reworded advice doesn't repost either.
- `missing-await` offers its fix for calls inside `for`/`if` blocks.
- Go `panic(` is caught mid-line (`if err != nil { panic(err) }`).
- `eval` advice for JS/TS no longer suggests Python's `json.loads`.

## 0.1.0 - 2026-09-25

- Initial release: diff parser, static pass (secrets, SQL concat, eval/exec, shell=True, bare
  except, debug output, TODOs, missing tests, Python AST checks), Claude review + verify pass,
  terminal/Markdown/SARIF/JSON output, GitHub PR fetch and opt-in `--post`, composite Action.
