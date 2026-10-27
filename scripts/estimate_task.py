#!/usr/bin/env python3
"""Task timeboxes from measured history (the answer to "how long will this take, and who decides?").

Why this file exists
--------------------
Nobody should guess a task's duration, and the supervisor is the worst possible estimator: it is the party
whose optimism is being tested. The queue already produces the data — every kanban run records when it
started and when it finished — so the estimate is *measured*, not promised:

    p50 = what a task of this kind usually costs
    p90 = what it costs when it goes badly
    cap = 2 x p90, rounded up, passed to the dispatcher as ``--max-runtime``

The cap is not a prediction: it is the point where the dispatcher SIGTERMs the worker and re-queues the
task, which turns "it ran forever" into a visible failure. Per-assignee, because a reviewer's p90 and an
implementer's p90 have nothing to do with each other.

Who estimates: the queue, from its own history (this script), and the operator approves the proposal by
committing ``configs/task_estimates.json``. The bridge then applies the cap to every queued task.

Usage
-----
    python3 scripts/estimate_task.py                 # show the measured distribution and the proposal
    python3 scripts/estimate_task.py --write         # write configs/task_estimates.json (review, then commit)
    python3 scripts/estimate_task.py --json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ESTIMATES_REL = "configs/task_estimates.json"
MIN_CAP_MINUTES = 15
ROUNDING_MINUTES = 5
# Below this many finished runs a percentile is noise, so the proposal stays at the floor and says so.
MIN_SAMPLES = 3


def kanban_tasks() -> list[dict]:
    proc = subprocess.run(["hermes", "kanban", "list", "--json", "--archived"],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"hermes kanban list failed ({proc.returncode}): {proc.stderr.strip()}")
    return json.loads(proc.stdout or "[]")


def durations_minutes(tasks: list[dict]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for task in tasks:
        started, completed = task.get("started_at"), task.get("completed_at")
        assignee = task.get("assignee") or "(unassigned)"
        if not started or not completed or completed < started:
            continue
        out.setdefault(assignee, []).append((completed - started) / 60.0)
    return out


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


def round_up(value: float, step: int = ROUNDING_MINUTES) -> int:
    return int(step * -(-value // step))


def propose(measured: dict[str, list[float]]) -> dict:
    proposal = {"generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                "rule": f"cap = max({MIN_CAP_MINUTES} min, round_up(2 x p90, {ROUNDING_MINUTES} min)); "
                        f"needs >= {MIN_SAMPLES} finished runs before the percentiles mean anything",
                "assignees": {}}
    for assignee, values in sorted(measured.items()):
        p50, p90 = percentile(values, 0.5), percentile(values, 0.9)
        if len(values) < MIN_SAMPLES:
            cap = MIN_CAP_MINUTES
            basis = f"floor: only {len(values)} finished run(s)"
        else:
            cap = max(MIN_CAP_MINUTES, round_up(2 * p90))
            basis = f"2 x p90({p90:.1f} min) over {len(values)} run(s)"
        proposal["assignees"][assignee] = {"samples": len(values), "p50_minutes": round(p50, 1),
                                           "p90_minutes": round(p90, 1), "max_runtime": f"{cap}m",
                                           "basis": basis}
    return proposal


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Derive per-assignee task timeboxes from kanban history.")
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--write", action="store_true", help=f"write {ESTIMATES_REL}")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        measured = durations_minutes(kanban_tasks())
    except (OSError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"cannot read the queue history: {exc}", file=sys.stderr)
        return 1
    if not measured:
        print("no finished runs yet: nothing to measure, every cap stays at the floor")
        return 1

    proposal = propose(measured)
    if args.json:
        print(json.dumps(proposal, ensure_ascii=False, indent=2))
    else:
        print("measured task durations (finished kanban runs)")
        print(f"{'assignee':<14}{'runs':>6}{'p50':>9}{'p90':>9}{'cap':>8}  basis")
        for assignee, row in proposal["assignees"].items():
            print(f"{assignee:<14}{row['samples']:>6}{row['p50_minutes']:>9.1f}{row['p90_minutes']:>9.1f}"
                  f"{row['max_runtime']:>8}  {row['basis']}")
        print()
        print("a cap is not a promise: at the cap the dispatcher SIGTERMs the worker and re-queues the task,")
        print("so 'it ran forever' becomes a visible failure instead of a silent one.")

    if args.write:
        path = os.path.join(args.root, ESTIMATES_REL)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(proposal, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        print(f"wrote {ESTIMATES_REL} — review it and commit; the bridge applies it to queued tasks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
