#!/usr/bin/env python3
"""Tree parity — two checkouts of the same project must not drift silently.

Why this exists
---------------
Development happens in one tree and publication happens from another. Round 05 of this project found the
two had drifted in both directions and *nobody had a command for it*: five scripts differed, one by 258
lines, and the only reason it was discovered is that an audit ran ``diff -rq`` by hand. A rule that lives in
a reviewer's habit is not a rule — the same lesson the regression suite already learned about prose.

So this script turns drift into a measurement with three outcomes, and the third one is the point:

* a path covered by ``must_match`` and different            -> drift, named with the first differing line;
* a path covered by ``may_differ`` **with a non-empty reason** -> allowed (an exception without a reason is
  reported as such, because an unexplained exception is drift wearing a hat);
* anything else that differs, or exists in one tree only  -> **unclassified drift**, also a stop. This is
  what keeps the check honest over time: a file added to one tree and forgotten in the other cannot hide
  behind a stale policy.

Usage
-----
    python3 scripts/tree_parity.py --selftest
    python3 scripts/tree_parity.py --policy configs/tree_parity.json
    python3 scripts/tree_parity.py --a /path/lab --b /path/published \\
        --must-match 'scripts/*.py' --must-match Makefile --may-differ 'scripts/private.py=lab-only tool'

Markers: ``TREE_PARITY_PASS_0001`` (exit 0) / ``TREE_PARITY_STOP_0001`` (exit 1).
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PASS_MARKER = "TREE_PARITY_PASS_0001"
STOP_MARKER = "TREE_PARITY_STOP_0001"

# Directories whose contents are state, caches or build output: comparing them would drown the signal in
# noise, and none of them is a source of truth for either tree.
SKIP_DIRS = {".git", "artifacts", "handoff", "dist", "course", "backlog", "__pycache__", ".venv",
             "node_modules", ".backport_backup", ".mypy_cache", ".pytest_cache", ".hypothesis",
             ".ruff_cache"}
SKIP_SUFFIXES = (".pyc", ".pyo")


def walk(root: str) -> dict:
    """Relative path -> absolute path for every comparable file under ``root``."""
    found = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name.endswith(SKIP_SUFFIXES):
                continue
            full = os.path.join(dirpath, name)
            found[os.path.relpath(full, root)] = full
    return found


def read_bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def matches(rel: str, patterns) -> bool:
    return any(fnmatch.fnmatch(rel, pattern) for pattern in patterns)


def first_difference(a_path: str, b_path: str) -> str:
    """A one-line, quotable account of where two files part company."""
    a_lines = read_bytes(a_path).decode("utf-8", "replace").splitlines()
    b_lines = read_bytes(b_path).decode("utf-8", "replace").splitlines()
    for index, (left, right) in enumerate(zip(a_lines, b_lines), start=1):
        if left != right:
            return f"line {index}: {left.strip()[:60]!r} vs {right.strip()[:60]!r}"
    return f"identical prefix, {len(a_lines)} vs {len(b_lines)} line(s)"


def reason_for(rel: str, may_differ: dict) -> str:
    """The declared reason for ``rel``, whether it was named exactly or through a glob.

    A blank reason is not a reason: such an entry does not cover the path at all, so the path is
    classified normally *and* the empty declaration is reported on its own. Returning ``"  "`` here would
    let a whitespace-only entry hide a real difference behind a finding about the pattern instead of the
    file — the reviewer of round 05's pull request named exactly that.
    """
    if rel in may_differ:
        return may_differ[rel] if may_differ[rel].strip() else ""
    for pattern, reason in may_differ.items():
        if fnmatch.fnmatch(rel, pattern) and reason.strip():
            return reason
    return ""


def compare(a_root: str, b_root: str, must_match, may_differ: dict) -> list:
    """Every finding between the two trees: drift, missing, unclassified, unexplained.

    ``may_differ`` deliberately covers **presence** as well as content: a development-only script exists in
    one checkout and not the other, and that is the normal shape of such a policy rather than an oversight.
    A policy that wants additions and deletions policed says so by putting the path under ``must_match``
    only, where a file present in one tree is a ``missing`` finding.
    """
    a_files, b_files = walk(a_root), walk(b_root)
    findings = []
    for rel in sorted(set(a_files) | set(b_files)):
        in_a, in_b = rel in a_files, rel in b_files
        if in_a and in_b and read_bytes(a_files[rel]) == read_bytes(b_files[rel]):
            continue
        reason = reason_for(rel, may_differ)
        declared_must = matches(rel, must_match)
        if reason:
            # An explicit, reasoned exception outranks a broad must-match glob: policies that cover a
            # directory need a way to carve out one file, and the reason is what makes it reviewable.
            continue
        if not declared_must:
            findings.append(("unclassified", rel, "differs or is missing in one tree and no rule covers it"))
        elif not (in_a and in_b):
            findings.append(("missing", rel, f"present only in {'A' if in_a else 'B'}"))
        else:
            findings.append(("drift", rel, first_difference(a_files[rel], b_files[rel])))
    for pattern, reason in may_differ.items():
        if not reason.strip():
            findings.append(("unexplained-exception", pattern,
                             "declared as may-differ without a reason"))
    return findings


def report(findings: list, a_root: str, b_root: str, must_match, may_differ: dict) -> int:
    if findings:
        print(f"tree parity: {len(findings)} finding(s) between")
        print(f"  A: {a_root}")
        print(f"  B: {b_root}")
        for kind, path, detail in findings:
            print(f"  [{kind}] {path}: {detail}")
        print(STOP_MARKER)
        return 1
    print(f"tree parity: must-match paths agree ({len(must_match)} rule(s), "
          f"{len(may_differ) or 'no'} explained exception(s))")
    print(PASS_MARKER)
    return 0


def _write(root: str, rel: str, body: str) -> None:
    path = os.path.join(root, rel)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)


def selftest() -> int:
    """Prove the three outcomes on throwaway trees, and the five ways the check must say no."""

    cases = []

    def case(name, files_a, files_b, must_match, may_differ, expect_pass):
        tmp = tempfile.mkdtemp(prefix="parity-")
        try:
            a, b = os.path.join(tmp, "a"), os.path.join(tmp, "b")
            for root, files in ((a, files_a), (b, files_b)):
                for rel, body in files.items():
                    _write(root, rel, body)
            findings = compare(a, b, must_match, may_differ)
            cases.append((name, (not findings) == expect_pass, expect_pass, findings))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    case("identical trees pass",
         {"scripts/x.py": "a\n"}, {"scripts/x.py": "a\n"}, ["scripts/*.py"], {}, True)
    case("must-match drift stops",
         {"scripts/x.py": "a\n"}, {"scripts/x.py": "b\n"}, ["scripts/*.py"], {}, False)
    case("file missing in one tree stops",
         {"scripts/x.py": "a\n"}, {}, ["scripts/*.py"], {}, False)
    case("explained exception passes",
         {"scripts/x.py": "a\n", "scripts/private.py": "lab\n"}, {"scripts/x.py": "a\n"},
         ["scripts/*.py"], {"scripts/private.py": "lab-only tooling"}, True)
    case("exception without a reason stops",
         {"scripts/x.py": "a\n", "scripts/private.py": "lab\n"}, {"scripts/x.py": "a\n"},
         ["scripts/*.py"], {"scripts/private.py": "  "}, False)
    case("unclassified drift stops",
         {"scripts/x.py": "a\n", "README.md": "one\n"}, {"scripts/x.py": "a\n", "README.md": "two\n"},
         ["scripts/*.py"], {}, False)
    # Policy loading is a second surface, and a file that parses but says nothing is worse than no file.
    tmp = tempfile.mkdtemp(prefix="parity-policy-")
    try:
        dict_policy = os.path.join(tmp, "dict.json")
        with open(dict_policy, "w", encoding="utf-8") as fh:
            json.dump({"a": "/x", "b": "/y", "must_match": ["*.py"],
                       "may_differ": {"p.py": "a written reason", "q.py": "   "}}, fh)
        loaded = load_policy(dict_policy)
        cases.append(("policy file: roots, rules and reasons load",
                      loaded["a"] == "/x" and loaded["b"] == "/y"
                      and loaded["must_match"] == ["*.py"]
                      and loaded["may_differ"]["p.py"] == "a written reason", True, loaded))
        cases.append(("policy file: a blank reason stays blank so it can be reported",
                      loaded["may_differ"]["q.py"] == "", True, loaded["may_differ"]))
        list_policy = os.path.join(tmp, "list.json")
        with open(list_policy, "w", encoding="utf-8") as fh:
            json.dump({"a": "/x", "b": "/y", "must_match": [],
                       "may_differ": [{"path": "r.py", "reason": "why"}]}, fh)
        loaded_list = load_policy(list_policy)
        cases.append(("policy file: the list form loads too",
                      loaded_list["may_differ"] == {"r.py": "why"}, True, loaded_list["may_differ"]))
        a2, b2 = os.path.join(tmp, "a2"), os.path.join(tmp, "b2")
        _write(a2, "scripts/x.py", "one\n")
        _write(b2, "scripts/x.py", "two\n")
        hidden = compare(a2, b2, ["scripts/*.py"], {"scripts/*.py": "  "})
        kinds = [kind for kind, _, _ in hidden]
        cases.append(("blank-reason exception still reports the file itself, not only the pattern",
                      "drift" in kinds and "unexplained-exception" in kinds, True, hidden))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    example = os.path.join(HERE, "configs", "tree_parity.example.json")
    if os.path.exists(example):
        shipped = load_policy(example)
        cases.append(("the shipped example policy parses and explains every exception",
                      bool(shipped["must_match"])
                      and all(reason.strip() for reason in shipped["may_differ"].values()),
                      True, shipped["may_differ"]))
    else:
        cases.append(("the shipped example policy parses and explains every exception", False, True,
                      "configs/tree_parity.example.json is missing"))

    failed = [name for name, ok, _, _ in cases if not ok]
    for name, ok, _, findings in cases:
        print(f"[{'ok  ' if ok else 'FAIL'}] {name}" + ("" if ok else f"  -> {findings}"))
    if failed:
        print(f"selftest: {len(failed)} of {len(cases)} case(s) failed: {', '.join(failed)}")
        print(STOP_MARKER)
        return 1
    falsifications = sum(1 for _, _, expect_pass, _ in cases if not expect_pass)
    print(f"selftest: {len(cases)} case(s), {falsifications} of them asserting a stop")
    print(PASS_MARKER)
    return 0


def load_policy(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        policy = json.load(fh)
    may_differ = {}
    raw = policy.get("may_differ") or {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            may_differ[key] = value.strip() if isinstance(value, str) else ""
    else:
        for item in raw:
            may_differ[str(item.get("path", ""))] = str(item.get("reason", "")).strip()
    return {"a": policy.get("a"), "b": policy.get("b"),
            "must_match": list(policy.get("must_match") or []), "may_differ": may_differ}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="compare two checkouts and name any drift")
    ap.add_argument("--a", help="tree A (a policy file may name it instead)")
    ap.add_argument("--b", help="tree B")
    ap.add_argument("--policy", help="JSON policy: {a, b, must_match, may_differ}")
    ap.add_argument("--must-match", action="append", default=[],
                    help="glob that must be identical in both trees (repeatable)")
    ap.add_argument("--may-differ", action="append", default=[],
                    help="path or glob allowed to differ, written as 'path=reason' (repeatable)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    must_match = list(args.must_match)
    may_differ = {}
    for item in args.may_differ:
        path, _, reason = item.partition("=")
        may_differ[path.strip()] = reason.strip()

    a_root, b_root = args.a, args.b
    if args.policy:
        policy = load_policy(args.policy)
        a_root = a_root or policy["a"]
        b_root = b_root or policy["b"]
        must_match += policy["must_match"]
        for key, value in policy["may_differ"].items():
            may_differ.setdefault(key, value)

    if not a_root or not b_root:
        print("both roots are required (--a/--b or a policy file carrying them)", file=sys.stderr)
        print(STOP_MARKER)
        return 1

    a_root, b_root = os.path.expanduser(a_root), os.path.expanduser(b_root)
    for label, root in (("A", a_root), ("B", b_root)):
        if not os.path.isdir(root):
            print(f"{label} root is not a directory: {root}", file=sys.stderr)
            print(STOP_MARKER)
            return 1
    return report(compare(a_root, b_root, must_match, may_differ), a_root, b_root, must_match, may_differ)


if __name__ == "__main__":
    sys.exit(main())
