"""Where a gate looks for the project it judges, and where its own templates live.

Two different trees are involved and they must not be confused:

* the *tool* — this package, installed by pip, which contains the gates and the templates;
* the *project* — the repository being judged, which owns AGENTS.md, configs/, artifacts/, handoff/.

Gates therefore resolve the project root from the working directory (or an explicit ``--root``), never
from their own ``__file__``: an installed copy lives in site-packages and has no project next to it.
"""

from __future__ import annotations

import os

PACKAGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gates")
TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
# A project is recognised by one of these; the nearest ancestor of the working directory wins.
PROJECT_MARKERS = (".git", "AGENTS.md")


def project_root(start: str | None = None) -> str:
    """Nearest ancestor of ``start`` (default: cwd) that looks like a project root; else ``start`` itself."""
    here = os.path.realpath(start or os.getcwd())
    cur = here
    while True:
        if any(os.path.exists(os.path.join(cur, m)) for m in PROJECT_MARKERS):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return here
        cur = parent


def gate_module(name: str) -> str:
    """Dotted module path of a gate, for ``python -m`` invocation."""
    return f"loopgate.gates.{name}"


def gate_file(name: str) -> str:
    """Absolute path of a gate source file inside the installed package."""
    return os.path.join(GATES_DIR, f"{name}.py")
