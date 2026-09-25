#!/usr/bin/env bash
# Reproduce the README example: a throwaway repo with one intentionally buggy commit.
set -euo pipefail
dir=$(mktemp -d)
cd "$dir"
git init -q -b main
g() { git -c user.name=demo -c user.email=demo@example.com "$@"; }
cat > inventory.py <<'PY'
import sqlite3


def get_item(conn, item_id):
    return conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
PY
g add . && g commit -qm init
cat > inventory.py <<'PY'
import sqlite3
import subprocess

API_TOKEN = "AKIAZ7Q4XKR2MB3TLW9P"


def get_item(conn, item_id):
    return conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()


def search(conn, term, seen=[]):
    print("searching", term)
    sql = f"SELECT * FROM items WHERE name LIKE '%{term}%'"
    try:
        rows = conn.execute(sql).fetchall()
    except:
        rows = []
    seen.append(term)
    return rows


def restock(cmd):
    # FIXME: validate input
    subprocess.call(cmd, shell=True)
    return eval(cmd)
PY
cat > web.js <<'JS'
export function total(xs) {
  console.log("total", xs);
  try { return xs.reduce((a, b) => a + b, 0); } catch (e) {}
}
JS
g add . && g commit -qm "add search and restock"
review-bot diff HEAD~1 "$@"
