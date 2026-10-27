#!/usr/bin/env python3
"""Change detector for the loop gate (the "report only on meaningful change" piece of stage 3).

Runs ``scripts/check_loop.py`` and reduces the run to a small deterministic state fingerprint:
verdict marker, per-dimension statuses, the failure list, the loop metrics and the round. Compares it
with the previous fingerprint stored in ``artifacts/loop_watch_last.json``:

* first ever run  -> prints one baseline line and stores the fingerprint;
* unchanged       -> prints **nothing** and exits 0 (a cron `monitor` script than skips the agent run
                     entirely, so a quiet project costs no tokens);
* changed         -> prints a compact before -> after diff and stores the new fingerprint.

The output contains no timestamps or paths that vary between runs, so identical states always produce
identical output (required for the cron monitor contract).

Usage:
    python3 scripts/loop_watch.py             # print only when the state changed
    python3 scripts/loop_watch.py --force     # always print the current fingerprint
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REPORT = os.path.join(ROOT, "artifacts", "loop_watch_report.json")
LAST = os.path.join(ROOT, "artifacts", "loop_watch_last.json")


def fingerprint() -> dict:
    """Run the gate and reduce it to a deterministic state fingerprint."""
    subprocess.run(
        [sys.executable, os.path.join(HERE, "check_loop.py"), "--report", REPORT],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    report = json.load(open(REPORT, encoding="utf-8"))
    state = json.load(open(os.path.join(ROOT, "artifacts", "loop_state.json"), encoding="utf-8"))
    dims = {d["id"]: d["status"] for d in report.get("dimensions", [])}
    return {
        "marker": report.get("marker"),
        "acceptance": "strict-complete"
        if report.get("marker") == "LOOP_ENGINEERING_PASS_0001" and not report.get("failures")
        else ("stage-1" if report.get("marker") == "LOOP_ENGINEERING_PASS_0001" else "not-accepted"),
        "dims": dims,
        "failures": report.get("failures") or [],
        "round": state.get("round"),
        "readiness": state.get("readiness"),
        "evidence": state.get("evidence"),
        "risk": state.get("risk"),
        "blockers": len(state.get("blockers") or []),
    }


def render_fp(fp: dict) -> str:
    dims = " ".join(f"{k}={v}" for k, v in sorted(fp["dims"].items()))
    return (
        f"{fp['marker']} ({fp['acceptance']}) round={fp['round']} "
        f"readiness={fp['readiness']} evidence={fp['evidence']} risk={fp['risk']} "
        f"blockers={fp['blockers']}\n  {dims}"
    )


def diff(prev: dict, cur: dict) -> list[str]:
    lines: list[str] = []
    for key in ("marker", "acceptance", "round", "readiness", "evidence", "risk", "blockers"):
        if prev.get(key) != cur.get(key):
            lines.append(f"  {key}: {prev.get(key)} -> {cur.get(key)}")
    for dim in sorted(set(prev.get("dims", {})) | set(cur.get("dims", {}))):
        if prev.get("dims", {}).get(dim) != cur.get("dims", {}).get(dim):
            lines.append(f"  {dim}: {prev.get('dims', {}).get(dim)} -> {cur.get('dims', {}).get(dim)}")
    if prev.get("failures") != cur.get("failures"):
        lines.append(f"  failures: {prev.get('failures')} -> {cur.get('failures')}")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description="Loop gate change detector (deterministic output).")
    ap.add_argument("--force", action="store_true", help="print the current fingerprint even if unchanged")
    args = ap.parse_args()

    cur = fingerprint()
    prev = json.load(open(LAST, encoding="utf-8")) if os.path.exists(LAST) else None

    if args.force:
        print("loop gate state:")
        print(render_fp(cur))
        json.dump(cur, open(LAST, "w", encoding="utf-8"), indent=2, sort_keys=True)
        return 0

    if prev is None:
        print("loop gate baseline established:")
        print(render_fp(cur))
        json.dump(cur, open(LAST, "w", encoding="utf-8"), indent=2, sort_keys=True)
        return 0

    if prev == cur:
        json.dump(cur, open(LAST, "w", encoding="utf-8"), indent=2, sort_keys=True)
        return 0

    print("loop gate state changed:")
    for line in diff(prev, cur):
        print(line)
    print("now:")
    print(render_fp(cur))
    json.dump(cur, open(LAST, "w", encoding="utf-8"), indent=2, sort_keys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
