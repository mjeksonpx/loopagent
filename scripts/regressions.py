#!/usr/bin/env python3
"""Regression suite for the loop gate (course lesson 09: review + *regression checks*).

Every case below is a failure that was found by hand during rounds 02-07 and is now pinned as an
automated regression: the suite copies the repository to a scratch dir, injects one defect per case,
runs the gate there and asserts the expected marker AND the expected failing dimension. A case fails
if the gate stays green (the check became vacuous) or if the wrong dimension reacts.

Usage:
    python3 scripts/regressions.py            # run all cases
    python3 scripts/regressions.py --verbose  # print each case's gate output

Exit 0 prints ``REGRESSION_SUITE_PASS_0001``; any failed case prints ``REGRESSION_SUITE_STOP_0001``
and exits 1.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Callable

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GATE_PASS = "LOOP_ENGINEERING_PASS_0001"
GATE_STOP = "LOOP_ENGINEERING_STOP_0001"
PASS_MARKER = "REGRESSION_SUITE_PASS_0001"
STOP_MARKER = "REGRESSION_SUITE_STOP_0001"
IGNORE = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "ywcrew")


def make_copy() -> str:
    tmp = tempfile.mkdtemp(prefix="loop_regress_")
    dst = os.path.join(tmp, "repo")
    shutil.copytree(ROOT, dst, ignore=IGNORE, symlinks=True)
    return dst


def run_gate(repo: str) -> tuple[str, list[str], str]:
    proc = subprocess.run(
        [sys.executable, os.path.join(repo, "scripts", "check_loop.py"), "--root", repo],
        capture_output=True, text=True,
    )
    out = proc.stdout
    marker = GATE_PASS if GATE_PASS in out else GATE_STOP
    failures = []
    for line in out.splitlines():
        head = line.split()[0] if line.split() else ""
        if head.startswith("D") and "[FAIL]" in line:
            failures.append(head)
    return marker, failures, out


# ------------------------------------------------------------------ injections

def wipe_loop_state(repo: str) -> None:
    os.remove(os.path.join(repo, "artifacts", "loop_state.json"))


def drop_two_rounds(repo: str) -> None:
    rounds = os.path.join(repo, "artifacts", "rounds")
    names = sorted(os.listdir(rounds))
    for name in names[-2:]:
        os.remove(os.path.join(rounds, name))


def tamper_anchor(repo: str) -> None:
    path = os.path.join(repo, "artifacts", "loop_gate_pass.json")
    anchor = json.load(open(path, encoding="utf-8"))
    anchor["verdict_hash"] = "0000000000000000"
    json.dump(anchor, open(path, "w", encoding="utf-8"), indent=2)


def inflate_health_baseline(repo: str) -> None:
    """Claim a better past than the tree ever had: the third verdict must notice."""
    path = os.path.join(repo, "configs", "change_health_baseline.json")
    payload = json.load(open(path, encoding="utf-8"))
    metrics = payload.get("metrics") or {}
    metrics["regression_cases"] = metrics.get("regression_cases", 0) + 20
    payload["metrics"] = metrics
    json.dump(payload, open(path, "w", encoding="utf-8"), indent=2)


def mint_anchor_for_wrong_state(repo: str) -> None:
    """Self-consistent forgery: flip a dimension's recorded state and re-mint its hash."""
    path = os.path.join(repo, "artifacts", "loop_gate_pass.json")
    anchor = json.load(open(path, encoding="utf-8"))
    for dim in anchor["dimensions"]:
        if dim["id"] == "D08":
            dim["status"], dim["skip"] = "SKIP", True
    stable = {
        "marker": "LOOP_ENGINEERING_PASS_0001",
        "dimensions": [{"id": d["id"], "status": d["status"]}
                       for d in anchor["dimensions"] if d["id"] != "D05"],
    }
    anchor["verdict_hash"] = hashlib.sha256(
        json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
    json.dump(anchor, open(path, "w", encoding="utf-8"), indent=2)
    hand = os.path.join(repo, "handoff", "latest_loop.json")
    payload = json.load(open(hand, encoding="utf-8"))
    payload["gate_report_verdict_hash"] = anchor["verdict_hash"]
    json.dump(payload, open(hand, "w", encoding="utf-8"), indent=2)


def fabricate_handoff_hash(repo: str) -> None:
    hand = os.path.join(repo, "handoff", "latest_loop.json")
    payload = json.load(open(hand, encoding="utf-8"))
    payload["status"] = "pass"
    payload["marker"] = "LOOP_ENGINEERING_PASS_0001"
    payload["gate_report_verdict_hash"] = "deadbeefdeadbeef"
    payload["blockers"] = []
    payload["roles_run"] = [{"role": "fake"}]
    payload["reviewed_by"] = ["fake"]
    json.dump(payload, open(hand, "w", encoding="utf-8"), indent=2)


def add_direct_cli_call(repo: str) -> None:
    with open(os.path.join(repo, "scripts", "_probe_direct_cli.sh"), "w", encoding="utf-8") as fh:
        fh.write('#!/bin/sh\ncodex exec --full-auto "fix it"\n')


def add_secret_shaped_string(repo: str) -> None:
    with open(os.path.join(repo, "secrets_probe.md"), "w", encoding="utf-8") as fh:
        fh.write("token = 'sk-" + "A" * 32 + "'\n")


def hide_lessons(repo: str) -> None:
    course = os.path.join(repo, "course")
    victim = next(d for d in sorted(os.listdir(course)) if d[0].isdigit())
    os.rename(os.path.join(course, victim), os.path.join(course, victim + ".hidden"))
    for i, name in enumerate(sorted(d for d in os.listdir(course) if d[0].isdigit())):
        if i >= 12:
            os.rename(os.path.join(course, name), os.path.join(course, name + ".hidden"))


def pad_a_round(repo: str) -> None:
    path = os.path.join(repo, "artifacts", "rounds", "round_09.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# Round 09\n## Gap\nx\n## Change\nx\n## Evidence\n" + "filler " * 40 + "\n## Metrics\nx\n## Next\nx\n")


def gut_evidence_bundle(repo: str) -> None:
    """Keep one command instead of the repository's full pre-commit bundle."""
    path = os.path.join(repo, "configs", "repo_profiles.json")
    payload = json.load(open(path, encoding="utf-8"))
    for profile in payload["profiles"].values():
        profile["evidence_bundle"] = profile["evidence_bundle"][:1]
    json.dump(payload, open(path, "w", encoding="utf-8"), indent=2)


def neuter_evidence_validator(repo: str) -> None:
    """Make the bundle validator always accept: the rule is then printed but not enforced."""
    path = os.path.join(repo, "scripts", "worker_brief.py")
    text = open(path, encoding="utf-8").read()
    marker = 'def verify_evidence(profile: dict, transcript: str) -> list[str]:'
    assert marker in text
    head, _, rest = text.partition(marker)
    body_end = rest.index("\n\n\n")
    replacement = (marker + "\n    \"\"\"Neutered for the regression case.\"\"\"\n    return []\n")
    open(path, "w", encoding="utf-8").write(head + replacement + rest[body_end:])


def drop_issue_bridge(repo: str) -> None:
    os.remove(os.path.join(repo, "scripts", "sync_issues_to_kanban.py"))


def drop_dispatch_constraints(repo: str) -> None:
    """The ywcrew task spec loses the bundle: the rule is prose again for the dispatch path."""
    path = os.path.join(repo, "scripts", "worker_brief.py")
    text = open(path, encoding="utf-8").read()
    assert '"constraints": constraints,' in text
    open(path, "w", encoding="utf-8").write(text.replace('"constraints": constraints,', '"constraints": "",'))


def borrow_exit_code(repo: str) -> None:
    """Re-introduce the hole the first live review found: one trailing exit code proves every gate.

    The validator is patched to accept any exit line after a command (the old free-floating window)
    instead of an exit line inside that command's own block; D10's fifth falsification must catch it.
    """
    path = os.path.join(repo, "scripts", "worker_brief.py")
    text = open(path, encoding="utf-8").read()
    old = ("            codes = [m.group(1) for _, text, _ in block for m in [EXIT_LINE_RE.match(text)] if m]")
    assert old in text, "validator shape changed: update this injector"
    new = ("            codes = [m.group(1) for _, text, _ in lines[idx + 1:] "
           "for m in [EXIT_LINE_RE.match(text)] if m]")
    open(path, "w", encoding="utf-8").write(text.replace(old, new))


def first_match_only(repo: str) -> None:
    """Review only the first occurrence of a command: a later failing rerun hides behind it.

    Second live review (harness, read-only, run 2026-10-12-309f9098) found this: a transcript where a
    command passed and then failed again reported a clean bundle. D10's sixth falsification must catch it.
    """
    path = os.path.join(repo, "scripts", "worker_brief.py")
    text = open(path, encoding="utf-8").read()
    old = "        for occurrence, idx in enumerate(matches, 1):"
    assert old in text
    open(path, "w", encoding="utf-8").write(text.replace(old, "        for occurrence, idx in enumerate(matches[:1], 1):"))


def drop_scenario_whys(repo: str) -> None:
    """Remove the user-facing 'why' from every scenario: the gate keeps passing but stops saying what a
    user would feel. D11 must reject that (a check nobody can argue about is not an acceptance criterion)."""
    path = os.path.join(repo, "configs", "product_scenarios.json")
    payload = json.load(open(path, encoding="utf-8"))
    for sc in payload["scenarios"]:
        sc.pop("why", None)
    json.dump(payload, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def neuter_product_evaluator(repo: str) -> None:
    """Make the product evaluator accept everything: the second gate becomes decoration."""
    path = os.path.join(repo, "scripts", "check_product.py")
    text = open(path, encoding="utf-8").read()
    old = '    exp = scenario.get("expect") or {}\n    reasons: list[str] = []'
    assert old in text, "product evaluator shape changed: update this injector"
    open(path, "w", encoding="utf-8").write(text.replace(old, '    return []\n' + old))


def thin_product_scenarios(repo: str) -> None:
    """Keep three scenarios: the product gate degrades into a smoke test."""
    path = os.path.join(repo, "configs", "product_scenarios.json")
    payload = json.load(open(path, encoding="utf-8"))
    payload["scenarios"] = payload["scenarios"][:3]
    json.dump(payload, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def tamper_signed_anchor(repo: str) -> None:
    """Rewrite the anchor after signing: the signature must no longer match (silent rewrite)."""
    path = os.path.join(repo, "artifacts", "loop_gate_pass.json")
    payload = json.load(open(path, encoding="utf-8"))
    payload["verdict_hash"] = "deadbeefdeadbeef"
    json.dump(payload, open(path, "w", encoding="utf-8"), indent=2)


def empty_allowed_signers(repo: str) -> None:
    """Remove the trusted public key: nothing can be verified, so nothing may be accepted."""
    open(os.path.join(repo, "configs", "allowed_signers"), "w", encoding="utf-8").write(
        "# no trusted signers\n")


def point_witness_at_another_run(repo: str) -> None:
    """Make the witness record describe a different verdict than the anchor on disk."""
    path = os.path.join(repo, "artifacts", "anchor_witness.json")
    record = json.load(open(path, encoding="utf-8"))
    record["verdict_hash"] = "0" * 16
    json.dump(record, open(path, "w", encoding="utf-8"), indent=2)


def strip_visual_whys(repo: str) -> None:
    """Visual scenarios lose the user-facing consequence: checks nobody can argue about."""
    path = os.path.join(repo, "configs", "visual_scenarios.json")
    payload = json.load(open(path, encoding="utf-8"))
    for sc in payload["scenarios"]:
        sc.pop("why", None)
    json.dump(payload, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def neuter_visual_evaluator(repo: str) -> None:
    """Make the visual evaluator accept every page: the visual gate becomes a screenshot generator."""
    path = os.path.join(repo, "scripts", "check_visual.py")
    text = open(path, encoding="utf-8").read()
    old = '    exp = scenario.get("expect") or {}\n    dom = observation.get("dom") or ""'
    assert old in text, "visual evaluator shape changed: update this injector"
    open(path, "w", encoding="utf-8").write(text.replace(old, "    return []\n" + old))


def drop_product_location(repo: str) -> None:
    """The profile loses both ways to find the product: the gate cannot say what it is accepting."""
    path = os.path.join(repo, "configs", "repo_profiles.json")
    payload = json.load(open(path, encoding="utf-8"))
    scenarios = json.load(open(os.path.join(repo, "configs", "product_scenarios.json"), encoding="utf-8"))
    name = scenarios.get("profile") or next(iter(payload["profiles"]))
    payload["profiles"][name].pop("local_path", None)
    payload["profiles"][name].pop("local_path_env", None)
    json.dump(payload, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def corrupt_witness_signature(repo: str) -> None:
    """Corrupt the signature: the anchor must not be accepted on an unverifiable signature."""
    path = os.path.join(repo, "artifacts", "loop_gate_verdict.txt.sig")
    text = open(path, encoding="utf-8").read()
    open(path, "w", encoding="utf-8").write(text.replace(text.splitlines()[-2], "AAAA"))


CASES: list[tuple[str, Callable[[str], None], str, list[str]]] = [
    ("positive: untouched copy stays green", lambda r: None, GATE_PASS, []),
    ("R1 loop state deleted -> D03", wipe_loop_state, GATE_STOP, ["D03"]),
    ("R2 round artifacts removed -> D04/D05", drop_two_rounds, GATE_STOP, ["D04", "D05"]),
    ("R3 anchor hash tampered -> D05", tamper_anchor, GATE_STOP, ["D05"]),
    # R4 used to be caught by D05's state comparison; now the witness catches it first: a re-minted anchor
    # is no longer covered by the signature, so D12 fails and D05 no longer has to.
    ("R4 anchor minted for a state we are not in -> D12", mint_anchor_for_wrong_state,
     GATE_STOP, ["D12"]),
    ("R5 handoff hash fabricated -> D05", fabricate_handoff_hash, GATE_STOP, ["D05"]),
    ("R6 pasteable CLI-agent call -> D06", add_direct_cli_call, GATE_STOP, ["D06"]),
    ("R7 credential-shaped string -> D07", add_secret_shaped_string, GATE_STOP, ["D07"]),
    ("R8 corpus below threshold -> D08", hide_lessons, GATE_STOP, ["D08"]),
    ("R9 headings with filler only -> D04", pad_a_round, GATE_STOP, ["D04"]),
    ("R10 worker bundle cut to a subset -> D10", gut_evidence_bundle, GATE_STOP, ["D10"]),
    ("R11 evidence validator neutered -> D10", neuter_evidence_validator, GATE_STOP, ["D10"]),
    ("R12 issue bridge removed -> D10", drop_issue_bridge, GATE_STOP, ["D10"]),
    ("R13 dispatch spec loses the bundle -> D10", drop_dispatch_constraints, GATE_STOP, ["D10"]),
    ("R14 borrowed exit code -> D10", borrow_exit_code, GATE_STOP, ["D10"]),
    ("R15 hidden failing rerun -> D10", first_match_only, GATE_STOP, ["D10"]),
    ("R16 scenario loses its user-facing why -> D11", drop_scenario_whys, GATE_STOP, ["D11"]),
    ("R17 product evaluator neutered -> D11", neuter_product_evaluator, GATE_STOP, ["D11"]),
    ("R18 product gate thinned to a smoke test -> D11", thin_product_scenarios, GATE_STOP, ["D11"]),
    ("R19 anchor rewritten after signing -> D12", tamper_signed_anchor, GATE_STOP, ["D12"]),
    ("R20 trusted signers emptied -> D12", empty_allowed_signers, GATE_STOP, ["D12"]),
    ("R21 witness record describes another run -> D12", point_witness_at_another_run, GATE_STOP, ["D12"]),
    ("R22 visual scenario loses its why -> D13", strip_visual_whys, GATE_STOP, ["D13"]),
    ("R23 visual evaluator neutered -> D13", neuter_visual_evaluator, GATE_STOP, ["D13"]),
    ("R24 witness signature corrupted -> D12", corrupt_witness_signature, GATE_STOP, ["D12"]),
    ("R25 profile loses local_path and local_path_env -> D11", drop_product_location, GATE_STOP, ["D11"]),
    ("R26 baseline claims a better past than the tree has -> D14", inflate_health_baseline, GATE_STOP, ["D14"]),
]


def main() -> int:
    ap = argparse.ArgumentParser(description="Loop gate regression suite.")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    # The baseline decides what each injection is compared against. Where the loop state exists the
    # untouched copy is green and any red is caused by the injection. Where it does not exist (a fresh
    # clone: artifacts/ and handoff/ are gitignored state), the untouched copy is already red, and the
    # honest reading is "these dimensions were red before you touched anything" — so the positive case is
    # skipped with that reason, and injections that tamper with absent state are skipped instead of
    # failing. What remains is still a real attack: the injected dimension must be red in both cases.
    baseline_repo = make_copy()
    baseline_marker, baseline_red, _ = run_gate(baseline_repo)
    shutil.rmtree(os.path.dirname(baseline_repo), ignore_errors=True)
    baseline_green = baseline_marker == GATE_PASS

    results: list[dict] = []
    for name, inject, want_marker, want_dims in CASES:
        repo = make_copy()
        try:
            if want_marker == GATE_PASS and not baseline_green:
                # The only case that expects green: not applicable where the loop never ran. Skipped with
                # the reason, never silently counted as a pass.
                results.append({"case": name, "ok": True, "skipped": True,
                                "reason": f"baseline not green: {', '.join(baseline_red) or 'none'}",
                                "marker": baseline_marker, "failed_dims": baseline_red})
                print(f"[skip] {name}")
                print(f"        baseline is red ({', '.join(baseline_red) or 'none'}): this tree has "
                      "never run the loop, so there is no green baseline to break")
                continue
            try:
                inject(repo)
            except (FileNotFoundError, KeyError) as exc:
                # The injection targets state this tree does not have: not applicable, not a pass.
                results.append({"case": name, "ok": True, "skipped": True,
                                "reason": f"state absent: {exc.__class__.__name__}: {exc}",
                                "marker": None, "failed_dims": []})
                print(f"[skip] {name}")
                print(f"        state absent: {exc.__class__.__name__}: {exc}")
                continue
            except Exception as exc:  # a broken injector is a failed case, never a crashed suite
                results.append({"case": name, "ok": False, "marker": None, "failed_dims": [],
                                "injector_error": f"{exc.__class__.__name__}: {exc}"})
                print(f"[FAIL] {name}")
                print(f"        injector error: {exc.__class__.__name__}: {exc}")
                continue
            marker, failures, out = run_gate(repo)
            ok = marker == want_marker and (not want_dims or any(d in failures for d in want_dims))
            results.append({"case": name, "ok": ok, "marker": marker, "failed_dims": failures})
            status = "ok  " if ok else "FAIL"
            print(f"[{status}] {name}")
            print(f"        marker={marker} failed_dims={failures or '[]'}")
            if args.verbose or not ok:
                print("\n".join("        | " + line for line in out.splitlines()[-6:]))
        finally:
            shutil.rmtree(os.path.dirname(repo), ignore_errors=True)

    bad = [r for r in results if not r["ok"]]
    skipped = [r for r in results if r.get("skipped")]
    print()
    print(f"cases: {len(results) - len(bad) - len(skipped)} ok, {len(bad)} failed, "
          f"{len(skipped)} skipped")
    if skipped:
        print("skipped cases target loop state this tree does not carry (artifacts/, handoff/); "
              "they are not counted as passes")
    report = {"marker": PASS_MARKER if not bad else STOP_MARKER, "cases": results}
    # A fresh clone has no artifacts/ (it is gitignored state), and a suite that crashes on the last
    # line is indistinguishable from a suite that failed: create the directory instead.
    report_dir = os.path.join(ROOT, "artifacts")
    os.makedirs(report_dir, exist_ok=True)
    with open(os.path.join(report_dir, "regressions_report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2, sort_keys=True)
    if bad:
        print(STOP_MARKER)
        return 1
    print(PASS_MARKER)
    return 0


if __name__ == "__main__":
    sys.exit(main())
