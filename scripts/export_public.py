#!/usr/bin/env python3
"""Build a publication-ready tree from this checkout.

Why this exists: the working repository is a *lab notebook*. It carries the author's absolute paths,
their GitHub account, a witness journal in a private repository, and a study corpus collected from a
paid course. None of that can be published, and hand-cleaning a clone before every release is how private
strings leak — so the cleaning is a command:

    python3 scripts/export_public.py --out dist/public
    python3 scripts/export_public.py --out dist/public --git-init   # fresh single-commit history

What it guarantees:
* **Only tracked files** are considered — gitignored state (`artifacts/`, `handoff/`, an editor's backup)
  cannot leak by accident.
* **Excluded paths stay out** (`course/`, the course build driver, the macOS OCR tool, and this
  configuration, which necessarily contains the strings it removes).
* **Identities are rewritten**, not merely deleted: a profile without a location is a profile the product
  gate cannot use, so the example files it lands are valid and runnable.
* **Fail-closed:** after building, every text file is scanned for the forbidden strings. If one survives,
  the exporter prints ``PUBLIC_EXPORT_STOP_0001`` with ``file:line`` and exits 1 — the tree is left on disk
  for inspection, but it is not a release.

The exporter never rewrites git history; the working repository is untouched apart from `--out`.
"""

from __future__ import annotations

import argparse
import json
import fnmatch
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_REL = "configs/public_sanitize.json"
PASS_MARKER = "PUBLIC_EXPORT_PASS_0001"
STOP_MARKER = "PUBLIC_EXPORT_STOP_0001"

TEXT_EXT = {".md", ".py", ".json", ".yml", ".yaml", ".toml", ".sh", ".txt", ".cfg", ".ini",
            ".html", ".css", ".swift", ".gitignore", ""}
# Dimensions that depend on loop state (rounds, handoff, a signed anchor) legitimately redden in a tree
# that has never been run; the export asserts the ones that depend on the code only.
CODE_DIMS = ["D01", "D02", "D06", "D07", "D10", "D11", "D13"]


def tracked(root: str) -> list[str]:
    """Every file that would be committed: tracked plus new-but-not-ignored.

    Tracked-only would silently drop files that are written but not yet committed, which is exactly the
    state a release branch is in — the first export missed LICENSE, the CI workflow and the CLI package.
    Ignored state (`artifacts/`, `handoff/`, `dist/`) stays out, so nothing private leaks by accident.
    """
    proc = subprocess.run(["git", "-C", root, "ls-files", "-z", "--cached", "--others",
                           "--exclude-standard"], capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"git ls-files failed: {proc.stderr.strip()}")
    return [entry for entry in proc.stdout.split("\0") if entry.strip()]


def excluded(rel: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if pattern.endswith("/") and (rel == pattern[:-1] or rel.startswith(pattern)):
            return True
        if rel == pattern:
            return True
    return False


def is_text(rel: str) -> bool:
    return os.path.splitext(rel)[1].lower() in TEXT_EXT


def sanitize(text: str, substitutions: list[dict]) -> tuple[str, dict[str, int]]:
    counts: dict[str, int] = {}
    for sub in substitutions:
        find, replace = sub["find"], sub["replace"]
        n = text.count(find)
        if n:
            text = text.replace(find, replace)
            counts[find] = n
    return text, counts


def redact(text: str, rel: str, rules: list[dict]) -> tuple[str, dict[str, int]]:
    """Pattern redactions, limited by scope — because the same string means two different things.

    `~/.hermes/kanban.db` inside scripts/guard_card.py is the default path of an integration and must stay
    working; the same string in a journal is a description of somebody's machine. Scope ("*.md") is what
    keeps the two apart instead of a clever pattern.
    """
    counts: dict[str, int] = {}
    for rule in rules:
        scope = rule.get("scope") or ["*.md"]
        if not any(fnmatch.fnmatch(rel, pattern) for pattern in scope):
            continue
        text, n = re.subn(rule["pattern"], rule["replace"], text)
        if n:
            counts[rule["pattern"]] = counts.get(rule["pattern"], 0) + n
    return text, counts


def build(cfg: dict, out: str, root: str) -> tuple[list[str], dict[str, int]]:
    replace_files = cfg.get("replace_files") or {}
    extras = cfg.get("extras") or []
    subs = cfg.get("substitutions") or []
    rules = cfg.get("redact") or []
    wrote: list[str] = []
    totals: dict[str, int] = {}

    for rel in tracked(root):
        if excluded(rel, cfg.get("exclude") or []):
            continue
        source = replace_files.get(rel, rel)
        src_path = os.path.join(root, source)
        if not os.path.exists(src_path):
            print(f"  missing source for {rel} <- {source}", file=sys.stderr)
            continue
        dest_rel = rel
        dest = os.path.join(out, dest_rel)
        os.makedirs(os.path.dirname(dest) or out, exist_ok=True)
        if is_text(rel):
            text = open(src_path, encoding="utf-8").read()
            text, counts = sanitize(text, subs)
            text, redacted = redact(text, rel, rules)
            counts.update({f"redact:{k}": v for k, v in redacted.items()})
            for k, v in counts.items():
                totals[k] = totals.get(k, 0) + v
            open(dest, "w", encoding="utf-8").write(text)
        else:
            shutil.copy2(src_path, dest)
        wrote.append(dest_rel)

    for extra in extras:
        src_path = os.path.join(root, extra["from"])
        if not os.path.exists(src_path):
            continue
        text = open(src_path, encoding="utf-8").read()
        text, counts = sanitize(text, subs)
        for k, v in counts.items():
            totals[k] = totals.get(k, 0) + v
        dest = os.path.join(out, extra["to"])
        os.makedirs(os.path.dirname(dest) or out, exist_ok=True)
        open(dest, "w", encoding="utf-8").write(text)
        wrote.append(extra["to"])
    return wrote, totals


def scan(cfg: dict, out: str) -> list[str]:
    forbidden = cfg.get("forbidden") or []
    allow = set(cfg.get("forbidden_allow") or [])
    offenders: list[str] = []
    for base, _dirs, files in os.walk(out):
        if ".git" in base.split(os.sep):
            continue
        for name in files:
            path = os.path.join(base, name)
            rel = os.path.relpath(path, out)
            if rel in allow or not is_text(rel):
                continue
            try:
                text = open(path, encoding="utf-8").read()
            except (OSError, UnicodeDecodeError):
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                for needle in forbidden:
                    if needle in line:
                        offenders.append(f"{rel}:{lineno}: {needle!r}")
    return offenders


def gate_dims(out: str) -> tuple[dict[str, str], str]:
    gate = os.path.join(out, "scripts", "check_loop.py")
    if not os.path.exists(gate):
        return {}, "no gate in the exported tree"
    proc = subprocess.run([sys.executable, gate], capture_output=True, text=True, cwd=out)
    dims: dict[str, str] = {}
    for line in (proc.stdout + proc.stderr).splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0].startswith("D") and parts[0][1:].isdigit():
            status = parts[2].strip("[]")
            dims[parts[0]] = status
    return dims, (proc.stdout + proc.stderr).strip().splitlines()[-1] if proc.stdout or proc.stderr else ""


def git_fresh_history(out: str) -> str:
    run = lambda *a: subprocess.run(["git", "-C", out, *a], capture_output=True, text=True)
    if os.path.isdir(os.path.join(out, ".git")):
        shutil.rmtree(os.path.join(out, ".git"))
    if run("init", "-q", "-b", "main").returncode != 0:
        run("init", "-q")
    run("add", "-A")
    proc = subprocess.run(
        ["git", "-C", out, "-c", "user.name=loopgate export", "-c", "user.email=export@example.org",
         "commit", "-q", "-m",
         "loopgate: gates that accept agent work by proof, not by confidence\n\n"
         "Public tree assembled by scripts/export_public.py: private material and local identities are\n"
         "removed, example profiles are in place of the author's working ones."],
        capture_output=True, text=True)
    if proc.returncode != 0:
        return f"git commit failed: {proc.stderr.strip()}"
    return run("rev-parse", "--short", "HEAD").stdout.strip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Assemble a publication-ready tree (fail-closed).")
    ap.add_argument("--out", required=True, help="destination directory (removed and rebuilt)")
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--git-init", action="store_true", help="init a fresh single-commit history")
    ap.add_argument("--no-gate", action="store_true", help="skip running the gate inside the export")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    out = os.path.abspath(args.out)
    if os.path.commonpath([out, os.path.abspath(args.root)]) == os.path.abspath(args.root) and \
            out == os.path.abspath(args.root):
        print("refusing to export over the working repository", file=sys.stderr)
        print(STOP_MARKER)
        return 1
    cfg_path = os.path.join(args.root, CONFIG_REL)
    try:
        cfg = json.load(open(cfg_path, encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read {CONFIG_REL}: {exc}", file=sys.stderr)
        print(STOP_MARKER)
        return 1

    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out)

    print(f"exporting {args.root} -> {out}")
    wrote, totals = build(cfg, out, args.root)
    print(f"  files written: {len(wrote)}")
    for find, count in sorted(totals.items(), key=lambda kv: -kv[1]):
        print(f"  rewritten: {find!r} x{count}")

    offenders = scan(cfg, out)
    dims: dict[str, str] = {}
    if not args.no_gate:
        dims, last_line = gate_dims(out)
        bad = [(d, dims.get(d, "MISSING")) for d in CODE_DIMS if dims.get(d) != "OK"]
        print(f"  gate in export: {last_line}")
        print("  code dimensions: " + ", ".join(f"{d}={dims.get(d, 'MISSING')}" for d in CODE_DIMS))
        if bad:
            for dim, status in bad:
                print(f"  code dimension not OK in the export: {dim} [{status}]", file=sys.stderr)

    # The change-health baseline is a claim about a specific tree. Shipping the working tree's numbers
    # would describe files the export does not contain, so the published tree records its own baseline
    # before it is committed — that is how the verdict stays honest instead of merely favourable.
    if not args.no_gate:
        recorded = subprocess.run([sys.executable, os.path.join(out, "scripts", "change_health.py"),
                                   "--root", out, "--write-baseline"],
                                  capture_output=True, text=True)
        if recorded.returncode == 0:
            print("  change-health baseline: " + recorded.stdout.strip().splitlines()[0])
        else:
            print(f"  change-health baseline not recorded: {recorded.stderr.strip()[:200]}", file=sys.stderr)

    revision = git_fresh_history(out) if args.git_init else None
    if revision:
        print(f"  fresh history: {revision}")

    ok = not offenders and (args.no_gate or not [d for d in CODE_DIMS if dims.get(d) != "OK"])
    if offenders:
        print(f"forbidden strings survived ({len(offenders)}):", file=sys.stderr)
        for line in offenders[:40]:
            print(f"  {line}", file=sys.stderr)

    summary = {"marker": PASS_MARKER if ok else STOP_MARKER, "out": out, "files": len(wrote),
               "rewritten": totals, "offenders": offenders[:200], "dims": dims,
               "revision": revision}
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    print(PASS_MARKER if ok else STOP_MARKER)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
