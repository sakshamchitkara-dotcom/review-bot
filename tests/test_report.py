import json

from review_bot.findings import Finding
from review_bot.report import summary, to_github, to_markdown, to_sarif, to_terminal

FS = [
    Finding("a.py", 3, "low", "debug", "print left", "remove", rule="debug-print"),
    Finding("b.py", 1, "critical", "security", "key | leaked", "rotate", rule="secret"),
]


def test_terminal_sorted_by_severity():
    out = to_terminal(FS)
    assert out.index("b.py:1") < out.index("a.py:3")
    assert out.endswith("2 finding(s): 1 critical, 1 low")


def test_markdown_escapes_pipes():
    md = to_markdown(FS)
    assert "key \\| leaked" in md and "| critical | `b.py:1` |" in md


def test_sarif_shape():
    doc = json.loads(to_sarif(FS))
    assert doc["version"] == "2.1.0"
    res = doc["runs"][0]["results"]
    assert res[0]["level"] == "error" and res[0]["ruleId"] == "secret"
    assert res[0]["locations"][0]["physicalLocation"]["region"]["startLine"] == 1
    assert {r["id"] for r in doc["runs"][0]["tool"]["driver"]["rules"]} == {"secret", "debug-print"}


def test_empty():
    assert summary([]) == "No findings." and json.loads(to_sarif([]))["runs"][0]["results"] == []


def test_fixes_render_as_suggestion_blocks():
    from review_bot.findings import suggestion_block

    fs = [Finding("a.js", 4, "medium", "correctness", "loose eq", "use ===", rule="x", fix="if (a === b) {"),
          Finding("b.py", 1, "low", "debug", "no fix")]
    md = to_markdown(fs)
    assert "### Suggested fixes" in md
    assert "`a.js:4`: loose eq\n\n```suggestion\nif (a === b) {\n```" in md
    assert md.count("```suggestion") == 1
    assert suggestion_block("x = `a` ```b```") == "````suggestion\nx = `a` ```b```\n````"


def test_sarif_rules_carry_registry_metadata_for_code_scanning():
    rules = {r["id"]: r for r in json.loads(to_sarif(FS))["runs"][0]["tool"]["driver"]["rules"]}
    sec = rules["secret"]
    assert sec["shortDescription"]["text"].startswith("Credential")
    assert sec["defaultConfiguration"]["level"] == "error"
    assert sec["properties"]["security-severity"] == "9.5" and sec["properties"]["tags"] == ["security"]
    assert "security-severity" not in rules["debug-print"]["properties"]
    assert rules["debug-print"]["defaultConfiguration"]["level"] == "note"


def test_terminal_shows_one_line_fix():
    out = to_terminal([Finding("a.sh", 2, "high", "correctness", "unquoted", "quote it", rule="unquoted-rm",
                               fix='    rm -rf "${D:?}"/')])
    assert out.splitlines()[1:3] == ["    -> quote it", '    fix: rm -rf "${D:?}"/']


def test_github_annotations_escape_and_levels():
    fs = FS + [Finding("dir,x/c:1.js", 0, "medium", "correctness", "50% off\nline two", "use ===",
                       rule="loose-equality", fix="a === b")]
    lines = to_github(fs).split("\n")
    assert lines[0] == "::error file=b.py,line=1,title=review-bot critical%3A secret::key | leaked%0Arotate"
    assert lines[1] == ("::warning file=dir%2Cx/c%3A1.js,line=1,title=review-bot medium%3A loose-equality::"
                        "50%25 off%0Aline two%0Ause ===%0Afix: a === b")
    assert lines[2].startswith("::notice file=a.py,line=3,")
    assert lines[3] == "3 finding(s): 1 critical, 1 medium, 1 low"
