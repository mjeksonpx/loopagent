#!/usr/bin/env python3
"""Give a fresh clone the one thing it can honestly have: a declaration that the loop has not run here yet.

`artifacts/` and `handoff/` are gitignored state, so a clone starts without them and the strict gate is red
on D03/D04/D05/D12 — not because the code is broken, but because there is no history to prove. Copying a
history out of this machine would be a fake; a fake is the one thing this project cannot ship.

So the tree says so out loud:

    python3 scripts/bootstrap_state.py --init          # writes artifacts/loop_state.json, state = cold
    python3 scripts/check_loop.py --strict             # -> LOOP_ENGINEERING_COLD_0001, exit 0
    ...run the loop once (a round artifact + a state update)...
    python3 scripts/check_loop.py --strict             # -> LOOP_ENGINEERING_PASS_0001, exit 0

The cold marker is not a pass: it says the code dimensions are green and four state dimensions could not
be measured. The gate refuses the declaration as soon as round or handoff artifacts appear, so it cannot
be used to hide a broken loop behind an empty directory.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_REL = "artifacts/loop_state.json"
ROUNDS_REL = "artifacts/rounds"
HANDOFF_REL = "handoff"
COLD_MARKER = "LOOP_STATE_COLD_0001"
STOP_MARKER = "LOOP_STATE_STOP_0001"


def cold_state() -> dict:
    now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    return {
        "state": "cold",
        "round": 0,
        "readiness": 0,
        "evidence": 0,
        "risk": 100,
        "blockers": [
            "cold start: no round artifact exists in this tree, so the loop's history is unproven",
            "no handoff has been written; roles have not run against a real gate verdict",
            "no signed anchor exists: a signature is produced by a run, not by a clone",
        ],
        "phase": "build",
        "updated_at": now,
        "marker": None,
        "history": [],
        "note": ("Written by scripts/bootstrap_state.py. This is a declaration, not evidence: it exists so "
                 "the gate can name what a fresh clone cannot prove instead of pretending either way."),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Initialize an honest cold-start state for a fresh tree.")
    ap.add_argument("--init", action="store_true", help="write artifacts/loop_state.json with state=cold")
    ap.add_argument("--force", action="store_true", help="overwrite an existing cold state file (never a real one)")
    ap.add_argument("--root", default=ROOT)
    args = ap.parse_args(argv)

    root = os.path.abspath(args.root)
    state_path = os.path.join(root, STATE_REL)
    rounds_dir = os.path.join(root, ROUNDS_REL)
    handoff_dir = os.path.join(root, HANDOFF_REL)

    rounds = [n for n in (os.listdir(rounds_dir) if os.path.isdir(rounds_dir) else []) if n.startswith("round_")]
    handoff = []
    if os.path.isdir(handoff_dir):
        for _dirpath, _dirnames, filenames in os.walk(handoff_dir):
            handoff.extend(n for n in filenames if n.endswith(".md"))

    if rounds or handoff:
        print(f"refusing to declare a cold start: {len(rounds)} round artifact(s) and {len(handoff)} "
              f"handoff file(s) prove this tree has run the loop")
        print(STOP_MARKER)
        return 1

    if os.path.exists(state_path) and not args.force:
        try:
            with open(state_path, encoding="utf-8") as fh:
                existing = json.load(fh)
        except (OSError, json.JSONDecodeError):
            existing = {}
        if existing.get("state") != "cold":
            print(f"refusing to overwrite {STATE_REL}: it holds a real state (round "
                  f"{existing.get('round')!r}, marker {existing.get('marker')!r})")
            print(STOP_MARKER)
            return 1
        print(f"{STATE_REL} is already a cold state; nothing to do")

    if not args.init:
        ap.error("--init is required (this script never writes without being asked)")

    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with open(state_path, "w", encoding="utf-8") as fh:
        json.dump(cold_state(), fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"wrote {STATE_REL}: state=cold, round=0 — the gate will name the unprovable dimensions")
    print("next: python3 scripts/check_loop.py --strict")
    print(COLD_MARKER)
    return 0


if __name__ == "__main__":
    sys.exit(main())
