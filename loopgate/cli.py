#!/usr/bin/env python3
"""Single entry point for every gate and tool in this repository.

Why this exists: the gates are deliberately plain scripts (`scripts/check_loop.py`, ...) so that a
reader can run one without installing a framework. The cost of that choice is discoverability — nobody
knows which of ten scripts is *the* gate. This wrapper adds names, nothing else: it never re-implements
a check, it forwards to the script and returns its exit code.

Usage:
    loopgate                     # list the commands
    loopgate gate --strict       # technical gate   -> LOOP_ENGINEERING_PASS_0001
    loopgate product             # product verdict  -> PRODUCT_ACCEPTANCE_PASS_0001
    loopgate regress             # the gate's own regression suite
    loopgate brief --profile X --issue 12 --format ywcrew-json --out /tmp/t.json

Every command forwards unknown arguments verbatim, so `loopgate gate --help` is the script's help.
"""

from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

COMMANDS: dict[str, tuple[str, str]] = {
    "gate": ("scripts/check_loop.py",
             "technical gate: is the loop disciplined? -> LOOP_ENGINEERING_PASS_0001 / _STOP_0001"),
    "product": ("scripts/check_product.py",
                "product gate: can a user do their job? -> PRODUCT_ACCEPTANCE_PASS_0001 / _STOP_0001"),
    "visual": ("scripts/check_visual.py",
               "visual gate: headless screenshot + DOM rules -> VISUAL_CHECK_PASS_0001"),
    "regress": ("scripts/regressions.py",
                "inject defects into a copy: the gate must redden on the expected dimension"),
    "brief": ("scripts/worker_brief.py",
              "render/verify a worker brief (the evidence bundle is inside it)"),
    "bridge": ("scripts/sync_issues_to_kanban.py",
               "issues -> queue: --target kanban (dispatcher) or --target backlog (markdown ledger)"),
    "estimate": ("scripts/estimate_task.py",
                 "measured p50/p90 -> a per-role runtime cap"),
    "status": ("scripts/render_status_page.py",
               "render both verdicts and the queue into one operator page"),
    "witness": ("scripts/anchor_witness.py",
                "sign/verify/publish the verdict anchor (external witness)"),
    "watch": ("scripts/loop_watch.py",
              "fingerprint the gate; stay silent unless it changed (cron-friendly)"),
    "dims": ("scripts/assert_dims.py",
             "assert that named dimensions are OK in a gate run (used by CI)"),
    "doctor": ("scripts/doctor.py",
               "report what this interpreter can run: jsonschema, Chrome, gh, git"),
    "guard": ("scripts/guard_card.py",
              "report (or --enforce) cards that exceed their token/run/progress budget"),
    "ledger": ("scripts/render_task_ledger.py",
               "render board cards into backlog/<task-id>.md (git is the memory)"),
    "health": ("scripts/change_health.py",
               "third verdict: compare countable metrics against the baseline or a git ref"),
    "review": ("scripts/review_pr.py",
               "second-level review of a pull request through ywcrew (--post to leave it on the PR)"),
}


def usage() -> str:
    width = max(len(name) for name in COMMANDS)
    lines = ["loopgate — gates that accept work by proof, not by confidence", "",
             "usage: loopgate <command> [args...]", ""]
    for name, (_, doc) in sorted(COMMANDS.items()):
        lines.append(f"  {name:<{width}}  {doc}")
    lines += ["", "Each command forwards every extra argument to the underlying script."]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help", "help"}:
        print(usage())
        return 0 if args else 2
    if args[0] in {"--version", "version"}:
        from loopgate import __version__
        print(__version__)
        return 0
    name, rest = args[0], args[1:]
    entry = COMMANDS.get(name)
    if entry is None:
        print(f"unknown command: {name}", file=sys.stderr)
        print(usage(), file=sys.stderr)
        return 2
    script = os.path.join(ROOT, entry[0])
    if not os.path.exists(script):
        print(f"missing script for '{name}': {script}", file=sys.stderr)
        return 2
    return subprocess.call([sys.executable, script, *rest])


if __name__ == "__main__":
    sys.exit(main())
