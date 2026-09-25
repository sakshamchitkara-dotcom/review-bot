from pathlib import Path

from review_bot.diff import parse_diff

FIX = Path(__file__).parent / "fixtures"


def test_multi_file_line_mapping():
    files = {f.path: f for f in parse_diff((FIX / "multi.diff").read_text())}
    assert set(files) == {"app/db.py", "README.md", "old.txt", "img.png"}
    db = files["app/db.py"]
    assert db.added == {
        2: "import os",
        5: "    q = \"SELECT * FROM users WHERE name = '\" + name + \"'\"",
        6: "    return conn.execute(q)",
        25: "# TODO: add pooling",
    }
    assert len(db.hunks) == 2


def test_new_deleted_binary_flags():
    files = {f.path: f for f in parse_diff((FIX / "multi.diff").read_text())}
    assert files["README.md"].is_new
    # a "+-- ..." line inside a hunk must be content, not a file header
    assert files["README.md"].added[2].startswith("-- not a header")
    assert files["old.txt"].is_deleted and not files["old.txt"].added
    assert files["img.png"].is_binary


def test_plain_unified_diff_without_git_header():
    text = "--- a/x.py\n+++ b/x.py\n@@ -1 +1,2 @@\n a = 1\n+b = 2\n"
    (f,) = parse_diff(text)
    assert f.path == "x.py" and f.added == {2: "b = 2"}


def test_render_roundtrip_contains_hunks():
    (f,) = parse_diff("--- a/x.py\n+++ b/x.py\n@@ -1 +1,2 @@\n a = 1\n+b = 2\n")
    assert "+b = 2" in f.text() and f.text().startswith("--- a/x.py")


def test_visible_lines_excludes_removed():
    (f,) = parse_diff("--- a/x.py\n+++ b/x.py\n@@ -1,3 +1,3 @@\n a\n-b\n+c\n d\n")
    assert f.visible_lines() == {1, 2, 3} and f.added == {2: "c"}
