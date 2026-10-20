#!/usr/bin/env python3
"""Doc drift check — runs in `make check` and CI. Exit 1 with a list on any miss.

For AGENTS.md, README.md, docs/, etl/, reference/, serving/, build/, deploy/ Markdown:
  1. every relative Markdown link resolves to a file or directory;
  2. every `make <target>` in a code span / fenced block exists in the Makefile;
  3. every backticked path that looks like a file (a/b.c) exists in the repo.
  4. every doc is reachable from AGENTS.md / README.md by following links (a doc
     nothing links to doesn't exist — link it or delete it).
Also: every Makefile target is documented (a `##` comment — `make help` is the one
home for the target list) unless it is internal (leading underscore).
"""
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC_GLOBS = ["AGENTS.md", "README.md", "docs/*.md", "etl/*.md", "reference/*.md",
             "serving/*.md", "build/*.md", "deploy/*.md"]
# Generated / gitignored / container-side locations a doc may legitimately name.
GENERATED = ("data/", "data-test/", "data-local/", "db/snapshots/", "/app/", "~/", ".tmp/",
             "node_modules/", ".venv/", ".claude/settings.local.json")

LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
FENCE = re.compile(r"^\s*```")
SPAN = re.compile(r"`([^`\n]+)`")
MAKE = re.compile(r"\bmake\s+([a-z][a-z0-9_-]*)")
PATH = re.compile(r"^[\w.-]+(?:/[\w.-]+)+\.[A-Za-z]\w{0,4}$")  # a/b.ext
DOMAIN = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)*\.(gov|com|org|net|io)(/|$)")


def make_targets():
    plain, documented = set(), set()
    for line in open(os.path.join(ROOT, "Makefile")):
        m = re.match(r"^([a-zA-Z0-9_-]+):(.*)$", line)
        if m:
            plain.add(m.group(1))
            if "## " in m.group(2):
                documented.add(m.group(1))
    return plain, documented


def code_chunks(text):
    """Yield (lineno, code text) for inline spans and fenced-block lines."""
    in_fence = False
    for n, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line):
            in_fence = not in_fence
        elif in_fence:
            yield n, line
        else:
            for m in SPAN.finditer(line):
                yield n, m.group(1)


def main():
    targets, documented = make_targets()
    misses = []
    for name in sorted(targets - documented):
        if not name.startswith("_"):
            misses.append(f"Makefile: target `{name}` has no `##` help comment")

    files = sorted({f for g in DOC_GLOBS for f in glob.glob(os.path.join(ROOT, g))})
    for path in files:
        rel = os.path.relpath(path, ROOT)
        base = os.path.dirname(path)
        text = open(path).read()
        in_fence = False
        for n, line in enumerate(text.splitlines(), 1):
            if FENCE.match(line):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            for m in LINK.finditer(line):
                url = m.group(1).split("#")[0]
                if not url or re.match(r"^[a-z]+:", url):
                    continue
                if not os.path.exists(os.path.normpath(os.path.join(base, url))):
                    misses.append(f"{rel}:{n}: broken link `{m.group(1)}`")
        for n, code in code_chunks(text):
            for m in MAKE.finditer(code):
                if m.group(1) not in targets:
                    misses.append(f"{rel}:{n}: `make {m.group(1)}` is not a Makefile target")
            p = re.sub(r":\d+$", "", code)
            if not PATH.match(p) or DOMAIN.match(p):
                continue
            q = re.sub(r"^(\.{1,2}/)+", "", p)
            if q.startswith(GENERATED) or re.search(r"[<>{}*$]", p):
                continue
            if not (os.path.exists(os.path.join(ROOT, q)) or os.path.exists(os.path.join(base, p))):
                misses.append(f"{rel}:{n}: path `{p}` does not exist")

    reached, queue = set(), [os.path.join(ROOT, "AGENTS.md"), os.path.join(ROOT, "README.md")]
    while queue:
        cur = queue.pop()
        if cur in reached or not os.path.isfile(cur):
            continue
        reached.add(cur)
        for m in LINK.finditer(open(cur).read()):
            url = m.group(1).split("#")[0]
            if url.endswith(".md"):
                queue.append(os.path.normpath(os.path.join(os.path.dirname(cur), url)))
    for path in files:
        if path not in reached:
            misses.append(f"{os.path.relpath(path, ROOT)}: not reachable from AGENTS.md or README.md")

    if misses:
        print(f"check_docs: {len(misses)} problem(s)")
        print("\n".join(misses))
        return 1
    print("check_docs: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
