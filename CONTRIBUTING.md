# Contributing

The one rule of this repository: **a claim without a command is not accepted** — not from a human, not
from an agent, not from a pull request description.

## What CI runs

```bash
make check        # = python -m compileall + assert_dims + regressions
```

`make check` is deliberately weaker than `make gate`. The strict gate's marker
(`LOOP_ENGINEERING_PASS_0001`) requires loop *state* — round artifacts, a handoff bound to the gate's
machine report, a signed anchor — which only exists after the loop has actually run somewhere. A fresh
clone cannot produce it, and pretending otherwise would be the exact failure this project is about.
`make gate` is what you run on a checkout where the loop state exists.

## Adding or changing a dimension

A gate dimension is only worth having if it can fail. Every new or changed dimension must come with:

1. the check itself (`scripts/check_loop.py`), returning `OK` / `FAIL` / `SKIP` with a reason that names
   what is missing;
2. **a falsification case in `scripts/regressions.py`** — inject the defect into a copy of the repository
   and prove the gate reddens on *that* dimension (`R<n>` case);
3. a line in the dimension table of `.agents/skills/loop-engineering/SKILL.md`, and in `README.md` if the
   user-visible verdict changes.

A dimension with no falsification case is a slogan: nobody can tell whether it has ever fired.

## Working on the gates

```bash
python3 scripts/check_loop.py --strict            # the gate (read-only; prints STOP/PASS + exit code)
python3 scripts/regressions.py                    # 25+ injections, one copy per case
python3 scripts/assert_dims.py --require D01 D06  # assert specific dimensions only
python3 scripts/worker_brief.py profiles          # repository profiles this checkout knows
```

Two habits that keep the suite honest:

- **Never print a marker yourself.** `LOOP_ENGINEERING_PASS_0001` is printed by the gate. A script that
  prints it on its own authority is a bug, and `D06`-style guardrails will not save you from a review.
- **Keep the gate read-only.** It may write only with an explicit `--report` path. If a check needs to
  change the repository, it is not a check.

## Style

- stdlib only. The core has no third-party dependency on purpose: `git clone` and run. Optional extras
  (e.g. `jsonschema` for one product scenario) are declared in `pyproject.toml` and must degrade to a
  `SKIP` **with a stated reason**, never to a silent PASS.
- Python 3.9+ compatible. CI runs 3.9, 3.12 and 3.14.
- Failure messages name the missing thing and the command that would produce it. "invalid" is not a
  message.
- Comments explain *why*, in the place where the next reader will ask.

## Pull requests

Use the template. It asks for the evidence block: every command you ran, its verbatim output tail, and
its exit code. A PR that changes behaviour without that block is not reviewed — it is returned.

## Reporting a problem

Open an issue with the command, the output and the exit code. If the gate missed a defect you found, that
is the most valuable issue you can file: it means the gate needs a new falsification case.
