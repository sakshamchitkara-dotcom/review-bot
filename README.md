# review-bot

An autonomous AI code reviewer for local git diffs and GitHub pull requests.

It runs two passes over the changed lines:

1. **Static pass (no LLM, always on)** – language-aware heuristics on added lines plus Python AST checks.
2. **LLM pass (Claude, optional)** – each file's diff is sent to `claude-opus-5-5`, which returns
   schema-constrained JSON findings (`file, line, severity, category, message, suggestion`).
   A second **verify** call re-reads the diff and drops findings it judges speculative or below a
   confidence threshold. Without `ANTHROPIC_API_KEY` it falls back to static-only.

Output as a terminal report, Markdown, SARIF 2.1.0 or JSON. Optionally post a PR review
(`--post`, off by default, and only on repos the token owner controls). Findings with a
one-line fix carry a GitHub ```` ```suggestion ```` block, so the fix is one click to apply.

- **Baseline**: `review-bot baseline` records today's findings; later runs report only new ones.
- **Cache**: LLM results are cached by a hash of each file's diff, so re-runs don't re-call Claude.

## Install

```bash
pip install git+https://github.com/sakshamchitkara-dotcom/review-bot
# or, from a checkout
pip install -e ".[dev]"
```

Python 3.10+.

## Usage

```bash
review-bot diff                    # working tree vs HEAD
review-bot diff main               # working tree vs main
review-bot diff --staged           # staged changes only
review-bot diff --file x.diff      # any unified diff ('-' for stdin)

review-bot pr owner/repo#123       # fetch a PR via the GitHub REST API (read-only)
review-bot pr https://github.com/owner/repo/pull/123 --post   # post a review (opt-in)

review-bot baseline                # record findings in every tracked file -> .reviewbot-baseline.json
review-bot baseline main           # ...or only those in the diff against a ref / --staged / --file

review-bot explain                 # list every rule
review-bot explain unquoted-rm     # what a rule flags, why, and the fix
```

Common flags:

| Flag | Meaning |
|---|---|
| `--format terminal\|markdown\|sarif\|json` | main report format (default terminal) |
| `-o FILE` | write the main report to a file |
| `--sarif FILE`, `--markdown FILE` | also write these reports |
| `--threshold SEV` | hide findings below `info\|low\|medium\|high\|critical` |
| `--fail-on SEV` | exit 1 if any finding is at/above SEV (CI gating) |
| `--no-llm` / `--no-verify` | static only / skip the verification pass |
| `--min-confidence 0.6` | verify-pass keep threshold |
| `--config PATH` | config file (default `./.reviewbot.toml` if present) |
| `--baseline FILE` / `--no-baseline` | baseline to subtract (default `./.reviewbot-baseline.json` if present) / ignore it |
| `--no-cache` / `--cache-dir DIR` | always call the LLM / cache location (default `$REVIEW_BOT_CACHE` or `~/.cache/review-bot`) |

Credentials:

- `ANTHROPIC_API_KEY` – enables the LLM pass.
- `GITHUB_TOKEN` / `GH_TOKEN`, else `gh auth token` – used for `pr`.

### Posting safety

`--post` is never on by default. Before any LLM call, review-bot checks that the token's user
owns the repo or has admin on it, or that it is running inside that same repo's GitHub Actions
workflow; otherwise it refuses. Findings on lines visible in the diff become inline comments;
the rest go in the review body, under a summary line and a severity-count table for the whole
run. Reviews are posted with `event: COMMENT` (never approve/block).
Comments already on the PR (same path, line and headline, i.e. severity, category and message)
are not posted again, even if a newer version words the advice or fix differently, so the Action can
run on every push without piling up duplicates; if nothing is new, nothing is posted.

## Checks

`review-bot explain <rule>` prints the reasoning and a flagged/fixed example for each:

```
$ review-bot explain curl-pipe-shell
curl-pipe-shell (high; shell)

Remote script piped straight into a shell.

Whatever the server (or anyone in the middle) returns runs immediately, and a dropped connection can run half a script.

Flagged:
    curl -fsSL https://x/install.sh | sh

Instead:
    curl -fsSLo install.sh https://x/install.sh && sha256sum -c install.sh.sha256 && sh install.sh

Disable: `curl-pipe-shell = false` under [rules] in .reviewbot.toml, or `reviewbot: ignore[curl-pipe-shell]` on the line.
```

| Rule | Languages | Severity |
|---|---|---|
| `secret` – AWS/GitHub/Anthropic/OpenAI/Slack/Google keys, private keys, hardcoded passwords | all files | critical/high |
| `sql-concat` – SQL built with `+`, f-strings, `.format`, `%`, `${}`, `#{}`; Rails `where("…#{}")` | code | high |
| `eval-exec` – `eval`/`exec`/`new Function` | py, js/ts, rb, php, shell | high |
| `shell-injection` – `shell=True`; Ruby `system`/backticks/`Open3` with `#{}` | py, rb | medium/high |
| `unsafe-html` – `dangerouslySetInnerHTML`, `innerHTML =` / `outerHTML =` (unless visibly sanitized); `.html_safe`, `raw(` | js/ts, rb | high |
| `bare-except` – `except:` / empty `catch {}` / `rescue Exception`, `rescue nil` (fix: `except Exception:`) | py, js/ts, java, kotlin, c#, php, rb | medium |
| `loose-equality` – `==` / `!=` outside strings/comments; `== null` allowed (fix: `===` / `!==`) | js/ts | medium |
| `missing-await` – bare call to a function declared `async` in the file, or `fetch` (fix: `await …` when the enclosing function is async) | js/ts | medium |
| `ts-any-export` – `export` line typed `: any`, `<any>`, `as any` | ts/tsx | low |
| `ignored-error` – `v, _ := f()` / `_ = f()` | go | medium |
| `panic` – `panic(` outside tests | go | low |
| `unsafe-block` – new `unsafe { }` / `unsafe fn` / `unsafe impl` | rust | medium |
| `unwrap` – `.unwrap()` outside tests and `#[cfg(test)]` modules (`.expect("…")` is allowed) | rust | low |
| `string-equality` – `==` / `!=` against a string literal (fix: `"lit".equals(x)`) | java | medium |
| `not-null-assertion` – Kotlin `!!` outside tests | kotlin | low |
| `curl-pipe-shell` – `curl … \| sh` / `wget … \| bash` | shell | high |
| `unquoted-rm` – `rm -r` with an unquoted `$VAR` (fix: `"${VAR:?}"`) | shell | high |
| `debug-print` – `print`, `breakpoint`, `console.log`, `debugger`, `binding.pry`, `dbg!`, `println!`, `set -x`, … (non-test files) | many | low |
| `todo` – TODO/FIXME/XXX/HACK | all | info |
| `missing-tests` – source changed but no test file touched (one per diff) | code | low |
| `huge-function` – function over `max_function_lines` touching the change | py (AST) | medium |
| `mutable-default` – `def f(x=[])` | py (AST) | medium |
| `is-literal` – `x is "a"` (fix: `==` / `!=`) | py (AST) | medium |
| `syntax-error` – changed file no longer parses | py (AST) | critical |
| `llm` – anything Claude finds and the verify pass keeps (with a one-line `fix` when possible) | all | varies |

The JS/TS, Go and Rust rules are line heuristics, not a type checker: `missing-await` only knows
about async functions declared in the same file. Rust code from a `#[cfg(test)]` line to the end
of the file counts as test code (the usual trailing `mod tests`).

## Inline suppressions

Silence one finding where it happens, in any comment syntax, on the flagged line or on a
comment-only line directly above it:

```python
print(banner)  # reviewbot: ignore[debug-print]
# reviewbot: ignore[sql-concat, todo]
q = f"SELECT * FROM {table}"  # table comes from a fixed allow-list; TODO move to an enum
eval(expr)  # reviewbot: ignore     <- no brackets: every rule on this line
```

Rule ids are the ones shown in brackets in the report (`llm` for Claude's findings). A trailing
ignore on a code line applies to that line only.

Real run on a diff adding `print(banner)  # reviewbot: ignore[debug-print]`, the two sql lines
above, and an unmarked `print(rows)`:

```
$ review-bot diff --file sup.diff --no-llm
review-bot: inline ignore: suppressed 2 finding(s)
tools/report.py:1: LOW [testing/missing-tests] Source changed but no test files were added or modified in this diff.
    -> Add or update tests covering this change (1 source file(s): `tools/report.py`).
tools/report.py:4: LOW [debug/debug-print] Debug output / breakpoint left in code.
    -> Remove it or use the project's logger.
2 finding(s): 2 low
```

## Configuration: `.reviewbot.toml`

```toml
[review]
severity_threshold = "low"          # info | low | medium | high | critical
ignore = ["docs/*", "*.lock", "vendor/*"]   # fnmatch globs
max_function_lines = 80

[rules]                              # all on by default
debug-print = false
missing-tests = false

[llm]
enabled = true
model = "claude-opus-5-5"
max_files = 25                       # biggest files first when a PR is larger
```

### Per-directory overrides

`[[overrides]]` blocks change `[rules]`, `severity_threshold` and `max_function_lines` for paths
matching their globs. Every matching block applies, in file order, so later blocks win.
`--threshold` on the command line beats per-path thresholds.

```toml
[[overrides]]
paths = ["legacy/*", "scripts/*.sh"]
severity_threshold = "high"          # only high/critical in old code
[overrides.rules]
debug-print = false

[[overrides]]
paths = ["tests/*"]
[overrides.rules]
secret = false                       # fixtures hold fake keys
```

Any rule id works under `[rules]` / `[overrides.rules]`, including `llm` for Claude's findings.

Real run (temp repo; `.reviewbot.toml` has one block, `legacy/*` at `high`; both files gained a
`print`, `legacy/old.py` also an `eval`):

```
$ review-bot diff --no-llm
legacy/old.py:3: HIGH [security/eval-exec] Dynamic code execution (eval/exec) on a changed line.
    -> Avoid eval/exec; parse data explicitly (e.g. json.loads / ast.literal_eval).
app/new.py:2: LOW [debug/debug-print] Debug output / breakpoint left in code.
    -> Remove it or use the project's logger.
app/new.py:2: LOW [testing/missing-tests] Source changed but no test files were added or modified in this diff.
    -> Add or update tests covering this change (2 source file(s): `app/new.py`, `legacy/old.py`).
3 finding(s): 1 high, 2 low
```

`legacy/old.py:2`'s `print` is hidden; `--threshold low` brings it back (4 findings).

## Suggested fixes

When a finding has a safe one-line rewrite (static rules marked "fix" above, or a `fix` returned
by Claude), PR inline comments end with a GitHub suggestion block and the Markdown report gets a
"Suggested fixes" section. Real output from the baseline demo below:

````
### Suggested fixes

`app.js:5`: `save(...)` returns a promise that is never awaited; errors are lost and ordering is not guaranteed.

```suggestion
  await save(a);
```
````

## Baseline

Adopting a reviewer on an existing codebase shouldn't mean re-litigating old code.
`review-bot baseline` reviews every tracked file (or a ref / `--staged` / `--file` diff) and writes
`.reviewbot-baseline.json`; commit it. `diff` and `pr` then drop findings that match it.

A fingerprint is `file + rule + category + whitespace-normalized line text` (no line number), so
known issues stay suppressed when code moves or is re-indented, and resurface when the flagged
line itself changes. Fingerprints are counted: if the baseline knows one `console.log(a)` and
you add a second, the second is reported. `missing-tests` is fingerprinted per file. Use
`--no-llm` for the baseline on large repos (the LLM pass is capped at `max_files`).

Real run (temp repo, static only):

```
$ review-bot baseline --no-llm
review-bot: recorded 3 finding(s) in .reviewbot-baseline.json
$ review-bot diff --no-llm        # after editing app.js
review-bot: baseline: suppressed 1 known finding(s)
app.js:5: MEDIUM [correctness/missing-await] `save(...)` returns a promise that is never awaited; errors are lost and ordering is not guaranteed.
    -> `await` it, return it, or mark it intentional with `void`.
1 finding(s): 1 medium
```

## LLM result cache

Each file's LLM result (after verification) is stored under a sha256 of its diff plus the model,
prompts, verify settings and the static findings shown to the model. Any change to those
re-queries; an unchanged file is served from disk. Failed, refused or unparseable calls are never
cached. Location: `--cache-dir`, else `$REVIEW_BOT_CACHE`, else `~/.cache/review-bot`;
`--no-cache` bypasses it. The Action persists it per PR with `actions/cache` when a key is set.

## GitHub Action

```yaml
# .github/workflows/review-bot.yml
on: pull_request
permissions:
  contents: read
  pull-requests: write     # only if post: "true"
jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: sakshamchitkara-dotcom/review-bot@main
        with:
          anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
          post: "false"
          fail-on: "high"
```

The report is added to the job summary and `review-bot.sarif` is written. With
`upload-sarif: "true"` (and `security-events: write`) the Action uploads it to code scanning
itself, even when `fail-on` fails the step; code scanning is free on public repos and needs
GitHub Advanced Security on private ones. Pick one of `post` and `upload-sarif` for PR feedback:
code scanning comments on the PR too, so with both on most findings show up twice (on
[sandbox PR #2](https://github.com/sakshamchitkara-dotcom/review-bot-sandbox/pull/2) review-bot
posted 17 inline comments and `github-advanced-security[bot]` 13 more for the same lines); the
Action prints a notice when both are set. See [`examples/review-bot.yml`](examples/review-bot.yml).
Fork PRs don't receive secrets, so they get the static pass only, and their `GITHUB_TOKEN` is
read-only: with `post: "true"` the review can't be posted, so review-bot warns and keeps the
job summary and SARIF instead of failing the job (`fail-on` still applies).
If `.reviewbot-baseline.json` is committed, the checked-out copy is applied automatically.

## Example (real output)

A throwaway repo with one intentionally buggy commit ([`examples/demo.sh`](examples/demo.sh)),
reviewed with `review-bot diff HEAD~1` (no API key set, so static only). The AWS key is fake;
AWS's documented `AKIA…EXAMPLE` key is deliberately ignored.

```
review-bot: ANTHROPIC_API_KEY not set; running static checks only
inventory.py:4: CRITICAL [security/secret] Possible AWS access key id committed (AKIA…).
    -> Remove it, rotate the credential, and load it from the environment or a secret store.
inventory.py:13: HIGH [security/sql-concat] SQL built by string concatenation/formatting (SQL injection risk).
    -> Use parameterized queries / bound parameters.
inventory.py:25: HIGH [security/eval-exec] Dynamic code execution (eval/exec) on a changed line.
    -> Avoid eval/exec; parse data explicitly (e.g. json.loads / ast.literal_eval).
inventory.py:11: MEDIUM [correctness/mutable-default] Mutable default argument in `search` is shared between calls.
    -> Default to None and create the object inside the function.
inventory.py:16: MEDIUM [error-handling/bare-except] Bare `except:` also swallows KeyboardInterrupt/SystemExit and hides bugs.
    -> Catch specific exceptions, e.g. `except ValueError:`.
    fix: except Exception:
inventory.py:24: MEDIUM [security/shell-injection] subprocess call with shell=True.
    -> Pass an argument list and drop shell=True.
web.js:3: MEDIUM [error-handling/bare-except] Empty catch block silently swallows errors.
    -> Handle, log, or rethrow the error.
inventory.py:2: LOW [testing/missing-tests] Source changed but no test files were added or modified in this diff.
    -> Add or update tests covering this change (2 source file(s): `inventory.py`, `web.js`).
inventory.py:12: LOW [debug/debug-print] Debug output / breakpoint left in code.
    -> Remove it or use the project's logger.
web.js:2: LOW [debug/debug-print] Debug output / breakpoint left in code.
    -> Remove it or use the project's logger.
10 finding(s): 1 critical, 2 high, 4 medium, 3 low
```

## End to end on a real PR

[`sakshamchitkara-dotcom/review-bot-sandbox`](https://github.com/sakshamchitkara-dotcom/review-bot-sandbox)
exists to exercise posting. [PR #1](https://github.com/sakshamchitkara-dotcom/review-bot-sandbox/pull/1)
adds intentionally buggy Python and TypeScript; the repo runs this Action with `post: "true"`.

Local run (static only, token from `gh auth token`; `->` suggestion lines and the middle trimmed):

```
$ review-bot pr sakshamchitkara-dotcom/review-bot-sandbox#1 --post --no-llm
review-bot: posted review https://github.com/sakshamchitkara-dotcom/review-bot-sandbox/pull/1#pullrequestreview-5315385718
api.ts:17: HIGH [security/unsafe-html] Raw HTML injection (dangerouslySetInnerHTML / innerHTML) is an XSS sink.
inventory.py:11: HIGH [security/sql-concat] SQL built by string concatenation/formatting (SQL injection risk).
api.ts:11: MEDIUM [correctness/loose-equality] Loose equality (`==`/`!=`) coerces types, e.g. `0 == ""` is true.
api.ts:12: MEDIUM [correctness/missing-await] `save(...)` returns a promise that is never awaited; ...
...
12 finding(s): 2 high, 6 medium, 4 low
```

Reviews on the PR from `gh api repos/.../pulls/1/reviews` (id, author, state, body excerpt; `#` notes added):

```
5315385718 sakshamchitkara-dotcom COMMENTED  12 inline comment(s).                      # local --post
5315387437 github-actions[bot]    COMMENTED  12 inline comment(s).                      # Action, on open
5315397072 sakshamchitkara-dotcom COMMENTED  1 new inline comment(s); 11 already posted. # after dedupe fix
5315401488 github-actions[bot]    COMMENTED  1 new inline comment(s); 12 already posted. # Action, on push
```

The first two show the duplicate-comment problem that the dedupe fix addresses; this sandbox
also surfaced the missing `await` suggestion inside loops and Python-only `eval` advice for TS.

### Shell, Java, Kotlin and Ruby on a real PR

[Sandbox PR #2](https://github.com/sakshamchitkara-dotcom/review-bot-sandbox/pull/2) adds a
deploy script and Java, Kotlin and Ruby files with known bugs, a `reviewbot: ignore[unsafe-html]`
comment in `banner.rb`, and a committed `.reviewbot-baseline.json` that knows `legacy.rb`. Its
workflow runs the Action with `post: "true"` and `upload-sarif: "true"`. From the job log:

```
review-bot: ANTHROPIC_API_KEY not set; running static checks only
review-bot: inline ignore: suppressed 1 finding(s)
review-bot: baseline: suppressed 2 known finding(s)
review-bot: posted review https://github.com/sakshamchitkara-dotcom/review-bot-sandbox/pull/2#pullrequestreview-5316328200
...
Successfully uploaded results
Analysis upload status is complete.
```

The posted review body:

```
## review-bot (static only) on sakshamchitkara-dotcom/review-bot-sandbox#2

17 finding(s): 6 high, 3 medium, 8 low

| critical | high | medium | low | info |
|---|---|---|---|---|
| 0 | 6 | 3 | 8 | 0 |

17 new inline comment(s); 0 already posted.
```

Code scanning alerts from the uploaded SARIF
(`gh api repos/…/code-scanning/alerts?ref=refs/pull/2/merge`, first 9 of 17: number, rule,
severity shown by GitHub, path, line):

```
1  sql-concat        high     app/models/report.rb           3
2  shell-injection   high     app/models/report.rb           4
3  unsafe-html       high     app/models/report.rb           5
4  curl-pipe-shell   high     scripts/deploy.sh              5
5  unquoted-rm       error    scripts/deploy.sh              6
6  eval-exec         high     scripts/deploy.sh              8
7  bare-except       warning  app/models/report.rb           6
8  string-equality   warning  src/main/java/shop/Auth.java   5
9  bare-except       warning  src/main/java/shop/Auth.java  10
```

The `unquoted-rm` inline comment ends with a one-click fix:

````
```suggestion
rm -rf "${BUILD_DIR:?}"/
```
````

That run also showed two things fixed afterwards: `missing-tests` was posted once per changed
file (five identical comments; now one per diff), and code scanning's own PR comments
duplicated review-bot's (now a notice; see [GitHub Action](#github-action)). After those fixes a
push to the PR re-ran the Action: `baseline: suppressed 1 known finding(s)` (`legacy.rb`'s
`missing-tests` is folded into the single per-diff finding) and
`every finding is already on the PR; nothing new to post`.

## How the LLM pass works

- Each file's hunks are rendered with new-side line numbers and split into chunks (~60k chars).
- Request: `client.messages.create(model="claude-opus-5-5", output_config={"effort": "high", "format": {"type": "json_schema", ...}})`.
  Adaptive thinking is always on for this model, so no `thinking` param is sent.
- Findings on lines not visible in the diff are dropped (they can't be anchored).
- Each finding also has a `fix`: the corrected text of that line, or `""`. Multi-line or no-op
  fixes are discarded so every posted suggestion block is safe to apply.
- Static findings for the file are included so the model doesn't repeat them.
- Verify: a second call (`effort: "medium"`) returns `{id, keep, confidence, reason}` per
  candidate; only `keep && confidence >= --min-confidence` survive. If verification fails, the
  unverified findings are kept rather than lost.
- Refusals, API errors and unparseable output skip that chunk with a warning; an auth failure
  drops back to static-only.
- The diff is treated as untrusted input; the system prompts tell the model not to follow
  instructions inside it.

## Known limits

- Static rules are per-line heuristics, not parsers (Python's AST checks aside): shell rules don't
  follow line continuations or heredocs, `string-equality` only sees compares against a literal,
  `missing-await` only knows `async` functions declared in the same file.
- Inline ignores are read from lines visible in the diff; with `git diff -U0` a comment on the
  unchanged line above a finding isn't seen.
- GitLab merge requests are not supported; `pr` is GitHub only.
- The Action's LLM cache step only runs when an Anthropic key is set; the sandbox has no key, so
  that step is covered by tests (`tests/test_action.py`), not by a real Actions run.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
```

## License

MIT
