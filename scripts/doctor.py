#!/usr/bin/env python3
"""Report what this machine can and cannot run, before a failure is mistaken for a code defect.

Why: the gates silently degrade when an input is missing — a product scenario SKIPs without jsonschema, a
visual check fails without Chrome, the queue needs git and (optionally) `gh`. Every one of those is a
one-line answer to "why is this yellow?", and without this script the answer is an afternoon of reading.
It also pins the interpreter question: `#!/usr/bin/env python3` resolves to whatever the PATH holds, so
two interpreters on one machine can give two verdicts for the same tree.

    python3 scripts/doctor.py
    python3 scripts/doctor.py --json

Exit 0 always: this is a report, not a gate. The gates decide.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARKER = "DOCTOR_REPORT_0001"


def probe_jsonschema() -> tuple[str, str]:
    """('ok' | 'missing' | 'damaged', detail) — the distinction the product gate makes."""
    try:
        import jsonschema  # noqa: F401
        import referencing  # noqa: F401
        import referencing.jsonschema  # noqa: F401
    except ImportError as exc:
        detail = str(exc)
        if "rpds" in detail or "referencing" in detail:
            return "damaged", detail
        return "missing", detail
    return "ok", ""


def which(binary: str) -> str | None:
    return shutil.which(binary)


def git_status() -> str:
    try:
        proc = subprocess.run(["git", "-C", ROOT, "status", "--porcelain"], capture_output=True, text=True)
    except OSError as exc:
        return f"unavailable ({exc})"
    if proc.returncode != 0:
        return f"error: {proc.stderr.strip()}"
    changed = len([line for line in proc.stdout.splitlines() if line.strip()])
    return "clean" if changed == 0 else f"{changed} changed path(s)"


def gh_auth() -> str:
    if not which("gh"):
        return "gh not installed (issue bridge unavailable; --issues-file and --target backlog still work)"
    try:
        proc = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
    except OSError as exc:
        return f"unavailable ({exc})"
    return "authenticated" if proc.returncode == 0 else "not authenticated (run `gh auth login`)"


def chrome() -> str:
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = which(name)
        if found:
            return found
    return "not found (visual checks will fail closed)"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Report interpreter and optional inputs.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    jsonschema_state, jsonschema_detail = probe_jsonschema()
    report = {
        "marker": MARKER,
        "python": {"version": sys.version.split()[0], "executable": sys.executable,
                   "meets_floor_3_9": sys.version_info >= (3, 9)},
        "optional": {
            "jsonschema": {"state": jsonschema_state, "detail": jsonschema_detail,
                           "repair": ("python3 -m pip install --force-reinstall rpds-py referencing"
                                      if jsonschema_state == "damaged"
                                      else "python3 -m pip install jsonschema")},
            "chrome": chrome(),
            "gh": gh_auth(),
        },
        "repo": {"root": ROOT, "git": which("git") or "missing", "working_tree": git_status()},
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print("doctor — what this machine can run")
        print()
        print(f"  python       {report['python']['version']}  ({report['python']['executable']})")
        if not report["python"]["meets_floor_3_9"]:
            print("               WARNING: below the supported floor (3.9)")
        state = jsonschema_state
        detail = f" — {jsonschema_detail}" if jsonschema_detail else ""
        print(f"  jsonschema   {state}{detail}")
        if state != "ok":
            print(f"               repair: {report['optional']['jsonschema']['repair']}")
            print("               effect: product scenario P08 (published contracts) reports SKIP")
        print(f"  chrome       {report['optional']['chrome']}")
        print(f"  gh           {report['optional']['gh']}")
        print(f"  git          {report['repo']['git']}; working tree {report['repo']['working_tree']}")
        print()
        print("Interpreters disagree: `python3` on PATH is what `make gate` uses, so if this report is "
              "not the verdict you expected, run with `make gate PYTHON=<path>`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
