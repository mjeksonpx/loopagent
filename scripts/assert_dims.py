#!/usr/bin/env python3
"""Assert that named dimensions are OK in a technical-gate run.

Why this exists: the technical gate's own verdict is `LOOP_ENGINEERING_PASS_0001` only when every
mandatory dimension is green — and several dimensions read *state* (`artifacts/`, `handoff/`, the signed
anchor) that a fresh clone does not have by design. CI therefore cannot honestly demand the PASS marker
from a clone; it can honestly demand that the dimensions which depend only on the code are green.

This script makes that distinction executable instead of a sentence in a README:

    python3 scripts/assert_dims.py --require D01 D02 D06 D07 D08 D10 D11 D13
    python3 scripts/assert_dims.py --require-all      # only where the loop state exists

Exit 0 prints ``CI_DIMS_PASS_0001``; a missing or red dimension prints ``CI_DIMS_STOP_0001`` naming it.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = "scripts/check_loop.py"
PASS_MARKER = "CI_DIMS_PASS_0001"
STOP_MARKER = "CI_DIMS_STOP_0001"
LINE_RE = re.compile(r"^(D\d\d)\s+\S+\s+\[(OK|FAIL|SKIP)\]")


def gate_output(root: str, extra: list[str]) -> tuple[int, str]:
    proc = subprocess.run([sys.executable, os.path.join(root, GATE), *extra],
                          capture_output=True, text=True, cwd=root)
    return proc.returncode, proc.stdout + proc.stderr


def parse(output: str) -> dict[str, str]:
    seen: dict[str, str] = {}
    for line in output.splitlines():
        m = LINE_RE.match(line.strip())
        if m:
            seen[m.group(1)] = m.group(2)
    return seen


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Assert that named gate dimensions are OK.")
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--require", nargs="*", default=[], metavar="DIM")
    ap.add_argument("--require-all", action="store_true",
                    help="require every dimension the gate prints (only where loop state exists)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    code, output = gate_output(args.root, ["--strict"])
    dims = parse(output)
    if not dims:
        print("the gate printed no dimensions at all — the gate itself is broken", file=sys.stderr)
        print(output, file=sys.stderr)
        print(STOP_MARKER)
        return 1

    required = sorted(dims) if args.require_all else list(args.require)
    if not required:
        print("nothing required: pass --require D01 ... or --require-all", file=sys.stderr)
        print(STOP_MARKER)
        return 2

    bad = [(d, dims.get(d, "MISSING")) for d in required if dims.get(d) != "OK"]
    if not args.quiet:
        print(f"gate exit={code}; dimensions seen: {len(dims)}; required: {len(required)}")
        for d in required:
            print(f"  {d} [{dims.get(d, 'MISSING')}]")
    if bad:
        for d, status in bad:
            print(f"required dimension not OK: {d} [{status}]", file=sys.stderr)
        print(STOP_MARKER)
        return 1
    print(PASS_MARKER)
    return 0


if __name__ == "__main__":
    sys.exit(main())
