"""``loopgate init`` — put the loop contract and cold-start state into a project.

What it writes, and why each piece exists:

* ``.agents/skills/loop-engineering/SKILL.md`` — the contract skill (D01), copied from the installed package.
* ``AGENTS.md`` / ``PLANS.md`` / ``README.md`` — created only if absent, from the package templates (D02).
  Existing files are never overwritten: a project's own rules win.
* ``configs/`` defaults the gates read when present — created only if absent.
* ``artifacts/loop_state.json`` — a *cold* declaration (``bootstrap_state --init``), never a fabricated PASS.

Expected first verdict: STOP, not COLD. COLD requires that every failing dimension is a history dimension;
an unconfigured project also fails D10–D14 (its own facts), and those are never waved through.

What it deliberately does NOT write: round artifacts, a handoff, or an anchor. Those are evidence of a run;
writing them here would make the gate pass on a history that never happened.

The contract text in AGENTS.md is a template with placeholders. The gate only checks that a field is
non-empty, so a placeholder would pass D01/D02 while proving nothing. ``init`` therefore reports that the
contract still needs the project's own Goal / Sources / Constraints / Done-when, and does not pretend otherwise.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from loopgate.paths import TEMPLATES_DIR

SKILL_REL = os.path.join(".agents", "skills", "loop-engineering", "SKILL.md")
SKILL_TEMPLATE = os.path.join(TEMPLATES_DIR, "skills", "loop-engineering", "SKILL.md")

# Files the gates require at the project root. Created only when absent.
ROOT_TEMPLATES = {
    "AGENTS.md": (
        "# AGENTS.md\n\n"
        "## Контракт петли\n\n"
        "1. **Goal** — что должно стать правдой.\n"
        "2. **Sources of truth** — конкретные файлы/команды, которым верим. Не выдумывать evidence.\n"
        "3. **Constraints** — границы правок: что можно менять, что нельзя.\n"
        "4. **Done-when** — воспроизводимая команда и маркер, который она печатает. Без маркера задача не закрыта.\n\n"
        "## Стоп-условия (Stop-if)\n\n"
        "Раунд останавливается и вопрос уходит оператору, если нужного файла нет, шаг требует сети или секретов,\n"
        "гейт не зеленеет после трёх раундов ремонта, или evidence пришлось бы выдумать.\n\n"
        "Гейт: `loopgate/gates/check_loop.py` (`python3 -m loopgate.gates.check_loop --strict`) —\n"
        "`LOOP_ENGINEERING_PASS_0001` (exit 0).\n"
    ),
    "PLANS.md": (
        "# PLANS.md\n\n"
        "Гейт: `loopgate/gates/check_loop.py` (`python3 -m loopgate.gates.check_loop --strict`) →\n"
        "`LOOP_ENGINEERING_PASS_0001`, exit 0.\n\n"
        "## Этапы\n\n"
        "1. (заполнить: что делаем, критерий приёмки, команда проверки)\n"
    ),
    "README.md": (
        "# Project\n\n"
        "Гейт: `loopgate/gates/check_loop.py` (`python3 -m loopgate.gates.check_loop --strict`).\n"
        "Петля принимается, когда он печатает `LOOP_ENGINEERING_PASS_0001` (exit 0). До первого раунда\n"
        "честный ответ — `LOOP_ENGINEERING_COLD_0001`.\n"
    ),
}


# What a project must supply itself. These are facts about the project (its commands, its signing key, its
# product scenarios), so init cannot generate them honestly; it names them instead.
NEEDS_PROJECT = [
    ("D10", "configs/repo_profiles.json — the repo's evidence bundle (the commands a worker must prove)"),
    ("D11", "configs/product_scenarios.json — what a user must be able to do, and why it matters"),
    ("D12", "configs/allowed_signers + configs/anchor_witness.json — the public key that signs the verdict"),
    ("D13", "configs/visual_scenarios.json — what a person must see on the status page"),
    ("D14", "configs/change_health.json + change_health_baseline.json — metrics and their recorded baseline"),
]


def _write_if_absent(path: str, text: str, created: list[str], kept: list[str]) -> None:
    if os.path.exists(path):
        kept.append(os.path.relpath(path))
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    created.append(os.path.relpath(path))


def init_project(root: str, args: list[str]) -> int:
    force = "--force" in args
    created: list[str] = []
    kept: list[str] = []

    skill_dst = os.path.join(root, SKILL_REL)
    if os.path.exists(skill_dst) and not force:
        kept.append(SKILL_REL)
    else:
        os.makedirs(os.path.dirname(skill_dst), exist_ok=True)
        shutil.copyfile(SKILL_TEMPLATE, skill_dst)
        created.append(SKILL_REL)

    for rel, text in ROOT_TEMPLATES.items():
        _write_if_absent(os.path.join(root, rel), text, created, kept)

    # Cold start: the tree declares it has not run the loop yet. Refuses if round/handoff artifacts exist.
    proc = subprocess.run([sys.executable, "-m", "loopgate.gates.bootstrap_state", "--init", "--root", root],
                          capture_output=True, text=True, cwd=root)
    cold_ok = proc.returncode == 0
    if cold_ok:
        created.append("artifacts/loop_state.json (cold)")
    else:
        kept.append("artifacts/loop_state.json (not changed: " + (proc.stderr or proc.stdout).strip()[:160] + ")")

    print(f"loopgate init — project: {root}")
    for item in created:
        print(f"  created  {item}")
    for item in kept:
        print(f"  kept     {item}")
    print()
    print("Not configured by init (the gate will report these as FAIL until the project provides them):")
    for dim, what in NEEDS_PROJECT:
        print(f"  {dim}  {what}")
    print()
    print("Next: fill Goal / Sources / Constraints / Done-when in AGENTS.md for this project.")
    print("Expect STOP until D10–D14 are configured: the gate names each gap and never fakes a PASS for it.")
    print("COLD is reported only when every red dimension is a history dimension (D03–D05, D09, D12).")
    return 0 if cold_ok else 1


if __name__ == "__main__":
    sys.exit(init_project(os.getcwd(), sys.argv[1:]))
