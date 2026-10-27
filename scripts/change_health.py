#!/usr/bin/env python3
"""The third verdict: not "did it break" but "did it get worse".

`check_loop.py` answers whether the loop is disciplined; `check_product.py` whether a user can do their job.
Both are boolean and both look at the present. This one looks at the difference: the same measurements taken
before and after a change, and a refusal when one of them got worse while everything stayed green.

What it is not: a code-quality score. Every number here is countable from the tree, deterministic, and tied
to a policy in `configs/change_health.json` that says which direction is bad. Nothing here is a judgement
about taste, and a metric nobody can defend does not belong in the file.

    python3 scripts/change_health.py                        # compare against the committed baseline
    python3 scripts/change_health.py --base main            # compare the working tree against a git ref
    python3 scripts/change_health.py --write-baseline       # re-record the baseline (a reviewable diff)
    python3 scripts/change_health.py --metrics              # print today's numbers, judge nothing

Verdict: CHANGE_HEALTH_PASS_0001 (exit 0) when nothing regressed, CHANGE_HEALTH_STOP_0001 (exit 1) naming
every metric that got worse and by how much.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASS_MARKER = "CHANGE_HEALTH_PASS_0001"
STOP_MARKER = "CHANGE_HEALTH_STOP_0001"
POLICY_REL = "configs/change_health.json"
BASELINE_REL = "configs/change_health_baseline.json"
SCHEMA_VERSION = 1


def read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def python_files(root: str) -> list[str]:
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {".git", "dist", "__pycache__", ".venv", "artifacts",
                                                        "handoff", "course", "node_modules"}]
        for name in filenames:
            if name.endswith(".py"):
                out.append(os.path.join(dirpath, name))
    return sorted(out)


def collect(root: str) -> dict:
    """Every metric is a count over files, so two operators on the same commit get the same numbers."""
    files = python_files(root)
    lines = [len(read(p).splitlines()) for p in files]
    gate = read(os.path.join(root, "scripts", "check_loop.py"))
    return {
        "gate_dims": len(set(re.findall(r'Dim\(\s*"(D\d\d)"', gate))),
        "regression_cases": len(re.findall(r'^\s*\("', read(os.path.join(root, "scripts", "regressions.py")), re.M)),
        "python_files": len(files),
        "python_lines": sum(lines),
        "largest_file_lines": max(lines) if lines else 0,
        # Only comment markers count: matching the bare words also matched this line and the label of the
        # falsification below, so the metric was measuring its own vocabulary.
        "todo_markers": sum(len(re.findall(r"#\s*(?:TODO|FIXME|XXX)\b", read(p))) for p in files),
    }


def load_policy(root: str) -> dict:
    raw = read(os.path.join(root, POLICY_REL))
    if not raw:
        raise SystemExit(f"policy missing: {POLICY_REL}")
    return json.loads(raw)


def judge(base: dict, head: dict, policy: dict) -> list[dict]:
    """One row per metric: value before, value after, and whether the policy calls that a regression."""
    rows: list[dict] = []
    for name, rule in (policy.get("metrics") or {}).items():
        before, after = base.get(name), head.get(name)
        kind = rule.get("kind")
        tolerance = rule.get("tolerance", 0) or 0
        row = {"metric": name, "base": before, "head": after, "kind": kind, "tolerance": tolerance,
               "changed": before != after, "regressed": False, "why": rule.get("why", "")}
        if not isinstance(before, int) or not isinstance(after, int):
            row["status"] = "unknown"
            rows.append(row)
            continue
        if kind == "must_not_decrease":
            row["regressed"], better = after < before, after > before
        elif kind == "must_not_increase":
            row["regressed"], better = after > before + tolerance, after < before
        elif kind == "max_growth_pct":
            limit = before * (1 + tolerance / 100.0)
            row["regressed"], better = after > limit, after < before
        else:
            row["status"] = f"unknown policy kind {kind!r}"
            rows.append(row)
            continue
        # Direction is per policy: for a size metric an increase inside the tolerance is "grew", not
        # "better" — calling it better would be the comparator flattering the author.
        if row["regressed"]:
            row["status"] = "regressed"
        elif before == after:
            row["status"] = "same"
        elif better:
            row["status"] = "improved"
        else:
            row["status"] = "within tolerance"
        rows.append(row)
    unknown = [m for m in (policy.get("metrics") or {}) if m not in base]
    for name in unknown:
        rows.append({"metric": name, "base": None, "head": head.get(name), "kind": "n/a",
                     "status": "unknown", "regressed": False, "changed": True,
                     "why": "not present in the baseline: record a new baseline to include it"})
    return rows


def snapshot_from_git(ref: str) -> tuple[str, tempfile.TemporaryDirectory]:
    """A scratch checkout of `ref`, so the comparison is between two commits and not two moods."""
    tmp = tempfile.TemporaryDirectory(prefix="change-health-")
    proc = subprocess.run(["git", "-C", ROOT, "worktree", "add", "--detach", tmp.name, ref],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        tmp.cleanup()
        raise SystemExit(f"cannot check out {ref}: {proc.stderr.strip()[:200]}")
    return tmp.name, tmp


def fmt(value) -> str:
    return "—" if value is None else f"{value:,}" if isinstance(value, int) else str(value)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Third verdict: compare countable metrics before and after.")
    ap.add_argument("--base", help="git ref to compare against (default: the committed baseline)")
    ap.add_argument("--write-baseline", action="store_true", help="record the current metrics as the baseline")
    ap.add_argument("--metrics", action="store_true", help="print today's metrics and exit")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--root", default=ROOT)
    args = ap.parse_args(argv)

    root = os.path.abspath(args.root)
    policy = load_policy(root)
    head = collect(root)

    if args.metrics:
        print(json.dumps(head, indent=2, sort_keys=True) if args.json else
              "\n".join(f"  {k:<22} {fmt(v)}" for k, v in sorted(head.items())))
        return 0

    if args.write_baseline:
        payload = {"schema": SCHEMA_VERSION,
                   "recorded_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                   "note": ("Generated by scripts/change_health.py --write-baseline. A baseline is a claim "
                            "about the tree; re-record it in a reviewed commit, never to make a red verdict "
                            "green."),
                   "metrics": head}
        path = os.path.join(root, BASELINE_REL)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")
        print(f"baseline written: {BASELINE_REL} ({len(head)} metrics)")
        return 0

    if args.base:
        base_root, tmp = snapshot_from_git(args.base)
        try:
            base_metrics = collect(base_root)
        finally:
            subprocess.run(["git", "-C", ROOT, "worktree", "remove", "--force", base_root],
                           capture_output=True, text=True)
            tmp.cleanup()
        label = f"git {args.base}"
    else:
        raw = read(os.path.join(root, BASELINE_REL))
        if not raw:
            print(f"no baseline ({BASELINE_REL}) and no --base: there is nothing to compare against, and a "
                  f"verdict without a reference is an opinion", file=sys.stderr)
            print(STOP_MARKER)
            return 1
        payload = json.loads(raw)
        if payload.get("schema") != SCHEMA_VERSION:
            print(f"baseline schema {payload.get('schema')!r} != {SCHEMA_VERSION}: re-record it", file=sys.stderr)
            print(STOP_MARKER)
            return 1
        base_metrics = payload.get("metrics") or {}
        label = f"baseline {payload.get('recorded_at', '?')}"

    rows = judge(base_metrics, head, policy)
    regressed = [r for r in rows if r["regressed"]]

    if args.json:
        print(json.dumps({"marker": STOP_MARKER if regressed else PASS_MARKER, "against": label,
                          "base": base_metrics, "head": head, "rows": rows}, indent=2, ensure_ascii=False))
    else:
        print(f"change health — {label}")
        print()
        print(f"  {'metric':<22}{'before':>10}{'after':>10}  verdict")
        for row in rows:
            delta = ""
            if isinstance(row["base"], int) and isinstance(row["head"], int) and row["base"] != row["head"]:
                diff = row["head"] - row["base"]
                delta = f"  ({diff:+d})"
            mark = {"regressed": "WORSE", "improved": "better", "same": "same",
                    "within tolerance": "grew, in tolerance"}.get(row["status"], row["status"])
            print(f"  {row['metric']:<22}{fmt(row['base']):>10}{fmt(row['head']):>10}  {mark}{delta}")
        print()
        for row in regressed:
            print(f"  why this matters: {row['metric']} — {row['why']}")
        unknown = [r["metric"] for r in rows if r["status"] == "unknown"]
        if unknown:
            print(f"  not measured (named, not hidden): {', '.join(unknown)}")

    print(STOP_MARKER if regressed else PASS_MARKER)
    return 1 if regressed else 0


if __name__ == "__main__":
    sys.exit(main())
