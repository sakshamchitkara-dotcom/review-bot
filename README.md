# review-bot

An autonomous AI code reviewer for local git diffs and GitHub pull requests.

It runs two passes over the changed lines:

1. **Static pass (no LLM, always on)** – language-aware heuristics on added lines plus Python AST checks.
2. **LLM pass (Claude, optional)** – each file's diff is sent to `claude-opus-5-5`, which returns
   schema-constrained JSON findings (`file, line, severity, category, message, suggestion`).
   A second **verify** call re-reads the diff and drops findings it judges speculative or below a
   confidence threshold. Without `ANTHROPIC_API_KEY` it falls back to static-only.

Output as a terminal report, Markdown, SARIF 2.1.0 or JSON. Optionally post a PR review
(`--post`, off by default, and only on repos the token owner controls).

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

Credentials:

- `ANTHROPIC_API_KEY` – enables the LLM pass.
- `GITHUB_TOKEN` / `GH_TOKEN`, else `gh auth token` – used for `pr`.

### Posting safety

`--post` is never on by default. Before any LLM call, review-bot checks that the token's user
owns the repo or has admin on it, or that it is running inside that same repo's GitHub Actions
workflow; otherwise it refuses. Findings on lines visible in the diff become inline comments;
the rest go in the review body. Reviews are posted with `event: COMMENT` (never approve/block).

## Checks

| Rule | Languages | Severity |
|---|---|---|
| `secret` – AWS/GitHub/Anthropic/OpenAI/Slack/Google keys, private keys, hardcoded passwords | all files | critical/high |
| `sql-concat` – SQL built with `+`, f-strings, `.format`, `%`, `${}` | code | high |
| `eval-exec` – `eval`/`exec`/`new Function` | py, js/ts, rb, php | high |
| `shell-injection` – `shell=True` | py | medium |
| `bare-except` – `except:` / empty `catch {}` | py, js/ts, java, c#, php | medium |
| `debug-print` – `print`, `breakpoint`, `console.log`, `debugger`, `binding.pry`, … (non-test files) | many | low |
| `todo` – TODO/FIXME/XXX/HACK | all | info |
| `missing-tests` – source changed but no test file touched | code | low |
| `huge-function` – function over `max_function_lines` touching the change | py (AST) | medium |
| `mutable-default` – `def f(x=[])` | py (AST) | medium |
| `is-literal` – `x is "a"` | py (AST) | medium |
| `syntax-error` – changed file no longer parses | py (AST) | critical |
| `llm` – anything Claude finds and the verify pass keeps | all | varies |

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
      - uses: actions/checkout@v4
      - uses: sakshamchitkara-dotcom/review-bot@main
        with:
          anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
          post: "false"
          fail-on: "high"
```

The report is added to the job summary and `review-bot.sarif` is written for
`github/codeql-action/upload-sarif`. See [`examples/review-bot.yml`](examples/review-bot.yml).
Fork PRs don't receive secrets, so they get the static pass only.

## Example (real output)

A throwaway repo with one intentionally buggy commit, reviewed with `review-bot diff HEAD~1`
(no API key set, so static only):

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
inventory.py:24: MEDIUM [security/shell-injection] subprocess call with shell=True.
    -> Pass an argument list and drop shell=True.
web.js:3: MEDIUM [error-handling/bare-except] Empty catch block silently swallows errors.
    -> Handle, log, or rethrow the error.
inventory.py:2: LOW [testing/missing-tests] Source changed but no test files were added or modified in this diff.
    -> Add or update tests covering this change.
inventory.py:12: LOW [debug/debug-print] Debug output / breakpoint left in code.
    -> Remove it or use the project's logger.
web.js:1: LOW [testing/missing-tests] Source changed but no test files were added or modified in this diff.
    -> Add or update tests covering this change.
web.js:2: LOW [debug/debug-print] Debug output / breakpoint left in code.
    -> Remove it or use the project's logger.
11 finding(s): 1 critical, 2 high, 4 medium, 4 low
```

## How the LLM pass works

- Each file's hunks are rendered with new-side line numbers and split into chunks (~60k chars).
- Request: `client.messages.create(model="claude-opus-5-5", output_config={"effort": "high", "format": {"type": "json_schema", ...}})`.
  Adaptive thinking is always on for this model, so no `thinking` param is sent.
- Findings on lines not visible in the diff are dropped (they can't be anchored).
- Static findings for the file are included so the model doesn't repeat them.
- Verify: a second call (`effort: "medium"`) returns `{id, keep, confidence, reason}` per
  candidate; only `keep && confidence >= --min-confidence` survive. If verification fails, the
  unverified findings are kept rather than lost.
- Refusals, API errors and unparseable output skip that chunk with a warning; an auth failure
  drops back to static-only.
- The diff is treated as untrusted input; the system prompts tell the model not to follow
  instructions inside it.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
```

## License

MIT
