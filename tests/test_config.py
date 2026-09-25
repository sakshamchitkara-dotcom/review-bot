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
