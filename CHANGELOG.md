# Changelog

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
  nothing new (Action re-runs on every push used to duplicate the whole review).
- `missing-await` offers its fix for calls inside `for`/`if` blocks.
- Go `panic(` is caught mid-line (`if err != nil { panic(err) }`).
- `eval` advice for JS/TS no longer suggests Python's `json.loads`.

## 0.1.0 - 2026-09-25

- Initial release: diff parser, static pass (secrets, SQL concat, eval/exec, shell=True, bare
  except, debug output, TODOs, missing tests, Python AST checks), Claude review + verify pass,
  terminal/Markdown/SARIF/JSON output, GitHub PR fetch and opt-in `--post`, composite Action.
