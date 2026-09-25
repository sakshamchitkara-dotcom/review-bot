import pytest

from review_bot.config import Config, load_config


def test_defaults_when_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = load_config()
    assert cfg.severity_threshold == "low" and cfg.rule_on("anything") and cfg.llm


def test_parse_file(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text(
        '[review]\nseverity_threshold="high"\nignore=["docs/*"]\n'
        "[rules]\ntodo=false\n[llm]\nenabled=false\n"
    )
    cfg = load_config(p)
    assert cfg.severity_threshold == "high"
    assert cfg.ignored("docs/a.md") and not cfg.ignored("src/a.py")
    assert not cfg.rule_on("todo") and cfg.rule_on("secret")
    assert not cfg.llm


def test_bad_threshold(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[review]\nseverity_threshold="huge"\n')
    with pytest.raises(ValueError):
        load_config(p)


def test_explicit_missing_path_errors(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.toml")


OVERRIDES = """
[review]
severity_threshold = "low"
[rules]
todo = false
[[overrides]]
paths = ["legacy/*", "scripts/*.sh"]
severity_threshold = "high"
max_function_lines = 200
[overrides.rules]
debug-print = false
[[overrides]]
paths = ["legacy/keep/*"]
[overrides.rules]
todo = true
"""


def test_overrides_apply_per_path_in_order(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text(OVERRIDES)
    cfg = load_config(p)
    top, legacy, keep = cfg.for_path("app/x.py"), cfg.for_path("legacy/x.py"), cfg.for_path("legacy/keep/x.py")
    assert top.severity_threshold == "low" and top.rule_on("debug-print") and not top.rule_on("todo")
    assert legacy.severity_threshold == "high" and legacy.max_function_lines == 200
    assert not legacy.rule_on("debug-print") and not legacy.rule_on("todo")
    assert keep.rule_on("todo") and not keep.rule_on("debug-print")  # both blocks match, later wins
    cfg.set_threshold("info")  # --threshold on the command line beats per-path thresholds
    assert cfg.for_path("legacy/x.py").severity_threshold == "info"


def test_override_validation(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[[overrides]]\npaths = "legacy/*"\n')
    with pytest.raises(ValueError, match="paths"):
        load_config(p)
    p.write_text('[[overrides]]\npaths = ["a/*"]\nseverity_threshold = "loud"\n')
    with pytest.raises(ValueError, match="loud"):
        load_config(p)
