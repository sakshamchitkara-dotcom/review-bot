import json

from review_bot.findings import Finding
from review_bot.report import summary, to_markdown, to_sarif, to_terminal

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
