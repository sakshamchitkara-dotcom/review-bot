"""Unified diff parser: maps every added line to (file, new-side line number)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass
class Hunk:
    old_start: int
    new_start: int
    header: str
    lines: list[str] = field(default_factory=list)  # raw lines incl. +/-/space prefix


@dataclass
class FileDiff:
    path: str
    old_path: str | None = None
    is_new: bool = False
    is_deleted: bool = False
    is_binary: bool = False
    hunks: list[Hunk] = field(default_factory=list)
    added: dict[int, str] = field(default_factory=dict)  # new line no -> content

    def visible_lines(self) -> set[int]:
        """New-side line numbers shown in the diff (added + context): valid PR comment anchors."""
        vis = set()
        for h in self.hunks:
            ln = h.new_start
            for raw in h.lines:
                if raw[:1] not in ("-", "\\"):
                    vis.add(ln)
                    ln += 1
        return vis

    def new_lines(self) -> dict[int, str]:
        """New-side line number -> text for every line shown in the diff (added + context)."""
        out = {}
        for h in self.hunks:
            ln = h.new_start
            for raw in h.lines:
                if raw[:1] not in ("-", "\\"):
                    out[ln] = raw[1:]
                    ln += 1
        return out

    def text(self) -> str:
        """Re-render this file's diff (used as LLM input)."""
        out = [f"--- a/{self.old_path or self.path}", f"+++ b/{self.path}"]
        for h in self.hunks:
            out.append(h.header)
            out.extend(h.lines)
        return "\n".join(out)


def _strip_prefix(p: str) -> str | None:
    p = p.strip().split("\t")[0]
    if p == "/dev/null":
        return None
    if p.startswith('"') and p.endswith('"'):
        p = p[1:-1]
    return p[2:] if p[:2] in ("a/", "b/") else p


def parse_diff(text: str) -> list[FileDiff]:
    files: list[FileDiff] = []
    cur: FileDiff | None = None
    hunk: Hunk | None = None
    new_ln = old_left = new_left = 0
    for raw in text.splitlines():
        in_hunk = hunk is not None and (old_left > 0 or new_left > 0)
        if in_hunk:
            tag = raw[:1]
            if tag == "+":
                cur.added[new_ln] = raw[1:]
                new_ln += 1
                new_left -= 1
            elif tag == "-":
                old_left -= 1
            elif tag == " " or raw == "":
                new_ln += 1
                old_left -= 1
                new_left -= 1
            hunk.lines.append(raw)
            continue
        if raw.startswith("\\") and hunk is not None:  # "\ No newline at end of file"
            hunk.lines.append(raw)
            continue
        hunk = None
        if raw.startswith("diff --git "):
            m = re.match(r"diff --git a/(.*) b/(.*)$", raw)
            cur = FileDiff(path=m.group(2) if m else raw.split()[-1])
            files.append(cur)
            continue
        if raw.startswith("--- "):
            if cur is None or cur.hunks:  # plain `diff -u` with no git header
                cur = FileDiff(path="")
                files.append(cur)
            old = _strip_prefix(raw[4:])
            cur.old_path = old
            cur.is_new = cur.is_new or old is None
            continue
        if cur is None:
            continue
        if raw.startswith("+++ "):
            new = _strip_prefix(raw[4:])
            if new is None:
                cur.is_deleted = True
            else:
                cur.path = new
        elif raw.startswith("new file mode"):
            cur.is_new = True
        elif raw.startswith("deleted file mode"):
            cur.is_deleted = True
        elif raw.startswith(("rename from ", "copy from ")):
            cur.old_path = raw.split(" ", 2)[2]
        elif raw.startswith(("Binary files", "GIT binary patch")):
            cur.is_binary = True
        elif m := _HUNK.match(raw):
            hunk = Hunk(old_start=int(m.group(1)), new_start=int(m.group(3)), header=raw)
            cur.hunks.append(hunk)
            new_ln = hunk.new_start
            old_left = int(m.group(2) or 1)
            new_left = int(m.group(4) or 1)
    return [f for f in files if f.path]
