#!/usr/bin/env python3
"""Single entry point for every gate and tool.

The gates are packaged under ``loopgate.gates``; this module only gives them one discoverable name and
runs them as modules. It never re-implements a check: it forwards the arguments and returns the exit code.

Usage:
    loopgate                       # list the commands
    loopgate init                  # add the contract, the skill and cold-start state to this project
    loopgate gate --strict         # technical gate   -> LOOP_ENGINEERING_PASS_0001
    loopgate product               # product verdict  -> PRODUCT_ACCEPTANCE_PASS_0001
    loopgate regress               # the gate's own regression suite
    loopgate --repo ../other gate  # judge another project instead of the working directory

``--repo`` is global and must come *before* the command name. Without it the project is the nearest
ancestor of the working directory that holds ``.git`` or ``AGENTS.md`` (``loopgate.paths.project_root``).
Unknown arguments are forwarded verbatim, so ``loopgate gate --help`` is the gate's own help.
"""

from __future__ import annotations

import os
import subprocess
import sys

from loopgate import __version__
from loopgate.paths import gate_module, project_root

# command -> (module under loopgate.gates, one-line description)
COMMANDS: dict[str, tuple[str, str]] = {
    "gate": ("check_loop",
             "technical gate: is the loop disciplined? -> LOOP_ENGINEERING_PASS_0001 / _STOP_0001"),
    "product": ("check_product",
                "product gate: can a user do their job? -> PRODUCT_ACCEPTANCE_PASS_0001 / _STOP_0001"),
    "visual": ("check_visual",
               "visual gate: headless screenshot + DOM rules -> VISUAL_CHECK_PASS_0001"),
    "regress": ("regressions",
                "inject defects into a copy: the gate must redden on the expected dimension"),
    "brief": ("worker_brief",
              "render/verify a worker brief (the evidence bundle is inside it)"),
    "bridge": ("sync_issues_to_kanban",
               "issues -> queue: --target kanban (dispatcher) or --target backlog (markdown ledger)"),
    "estimate": ("estimate_task",
                 "measured p50/p90 -> a per-role runtime cap"),
    "status": ("render_status_page",
               "render both verdicts and the queue into one operator page"),
    "witness": ("anchor_witness",
                "sign/verify/publish the verdict anchor (external witness)"),
    "watch": ("loop_watch",
              "fingerprint the gate; stay silent unless it changed (cron-friendly)"),
    "dims": ("assert_dims",
             "assert that named dimensions are OK in a gate run (used by CI)"),
    "doctor": ("doctor",
               "report what this interpreter can run: jsonschema, Chrome, gh, git"),
    "guard": ("guard_card",
              "report (or --enforce) cards that exceed their token/run/progress budget"),
    "ledger": ("render_task_ledger",
               "render board cards into backlog/<task-id>.md (git is the memory)"),
    "health": ("change_health",
               "third verdict: compare countable metrics against the baseline or a git ref"),
    "review": ("review_pr",
               "second-level review of a pull request through ywcrew (--post to leave it on the PR)"),
    "parity": ("tree_parity",
               "two checkouts must not drift -> TREE_PARITY_PASS_0001 (--policy <json>)"),
    "export": ("export_public",
               "build a publication-ready tree with the private parts removed"),
}


def usage() -> str:
    width = max(len(name) for name in list(COMMANDS) + ["init"])
    lines = [f"loopgate {__version__} — gates that accept work by proof, not by confidence", "",
             "usage: loopgate [--repo PATH] <command> [args...]", "",
             f"  {'init':<{width}}  add the contract, the skill and cold-start state to this project"]
    for name, (_, doc) in sorted(COMMANDS.items()):
        lines.append(f"  {name:<{width}}  {doc}")
    lines += ["",
              "Each command forwards every extra argument to the underlying gate.",
              "--repo PATH judges that project instead of the one found from the working directory;",
              "it goes before the command name (after it, --repo belongs to the gate)."]
    return "\n".join(lines)


def split_repo_flag(args: list[str]) -> tuple[str | None, list[str], str | None]:
    """Consume a leading ``--repo <path>`` / ``--repo=<path>``.

    Only the global position is recognised: ``worker_brief`` declares its own ``--repo`` (the repository
    slug for the issue URL), so scanning the forwarded tail would silently steal that argument.
    """
    repo: str | None = None
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--repo":
            if index + 1 >= len(args):
                return None, args, "--repo needs a path"
            repo = args[index + 1]
            index += 2
            continue
        if arg.startswith("--repo="):
            repo = arg.split("=", 1)[1]
            index += 1
            continue
        break
    if repo is not None and not repo.strip():
        return None, args, "--repo needs a path"
    return repo, args[index:], None


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    repo, args, repo_error = split_repo_flag(args)
    if repo_error:
        print(repo_error, file=sys.stderr)
        return 2
    if not args or args[0] in {"-h", "--help", "help"}:
        print(usage())
        return 0 if args else 2
    if args[0] in {"--version", "version"}:
        print(__version__)
        return 0
    name, rest = args[0], args[1:]

    if repo is not None:
        root = os.path.abspath(os.path.expanduser(repo))
        if not os.path.isdir(root):
            print(f"--repo is not a directory: {root}", file=sys.stderr)
            return 2
    else:
        root = project_root()

    if name == "init":
        from loopgate.init import init_project
        return init_project(root, rest)

    entry = COMMANDS.get(name)
    if entry is None:
        print(f"unknown command: {name}", file=sys.stderr)
        print(usage(), file=sys.stderr)
        return 2
    # The gate code comes from this installed package (`-m` resolves it from the interpreter); the
    # project's own files (configs/, artifacts/, handoff/, AGENTS.md) are read from `root`, the cwd.
    return subprocess.call([sys.executable, "-m", gate_module(entry[0]), *rest], cwd=root)


if __name__ == "__main__":
    sys.exit(main())
