#!/usr/bin/env python3
"""Canonical worker brief (evidence-first) + the validator that keeps it honest.

Why this file exists
--------------------
The first live PR run ended in a legitimate REJECT: the worker had proven ``go build`` only, while the
target repository requires ``golangci-lint run ./...`` and ``go test -count=1 ./...`` before any
commit. The rule lived in the skill prose and in the supervisor's head, so the worker never received
it and the supervisor had to replay the evidence after the fact. Here the rule lives **inside the
brief the worker gets** (``render_brief``) and is **machine-checked** (``verify_evidence``).

Contract
--------
* ``render_brief`` is deterministic: same profile + same issue in, same bytes out (no timestamps).
* ``verify_evidence`` fails on the two failure modes of the smoke run: a mandatory command that was
  never run, and a command whose exit code is not visible or is non-zero. Prints
  ``WORKER_EVIDENCE_PASS_0001`` (exit 0) or ``WORKER_EVIDENCE_STOP_0001`` (exit 1, reasons listed).
* Read-only with respect to the repository: the only write is ``build --out PATH``.

Usage
-----
    python3 scripts/worker_brief.py profiles
    python3 scripts/worker_brief.py build --profile sample-service --issue 42 --title "..." [--body "..."|--body-file f] [--url U] [--out PATH]
    python3 scripts/worker_brief.py verify --profile sample-service --evidence PATH [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILES_REL = "configs/repo_profiles.json"
PASS_MARKER = "WORKER_EVIDENCE_PASS_0001"
STOP_MARKER = "WORKER_EVIDENCE_STOP_0001"

EXIT_LINE_RE = re.compile(r"^\s*exit(?:\s*code)?\s*[:=]\s*(\d+)\s*$", re.I)
NOT_RUN_RE = re.compile(r"^\s*(?:NOT RUN|not run|skipped)\s*$", re.M)
PROMPT_RE = re.compile(r"^\s*(?:[$>]|➜)?\s*")


# ---------------------------------------------------------------- profiles

def load_profiles(root: str = ROOT) -> dict:
    with open(os.path.join(root, PROFILES_REL), encoding="utf-8") as fh:
        return json.load(fh)["profiles"]


def get_profile(root: str, name: str) -> dict:
    profiles = load_profiles(root)
    if name not in profiles:
        raise KeyError(f"unknown profile {name!r}; known: {', '.join(sorted(profiles)) or 'none'}")
    return profiles[name]


def bundle_commands(profile: dict) -> list[dict]:
    return list(profile.get("evidence_bundle") or [])


def render_ywcrew_task(profile_name: str, profile: dict, issue: dict,
                       backend: str = "harness", mode: str = "edit") -> dict:
    """The same brief as a ywcrew five-part task spec.

    The evidence rule has to travel with the *dispatch* too, not only with the kanban task body: a
    ywcrew run carries ``task.briefing`` + ``task.constraints``, so the bundle and its proof format are
    written into those fields verbatim. Rendering it here keeps one source for the rule.
    """
    bundle = bundle_commands(profile)
    base = profile.get("base_branch", "main")
    branch = (profile.get("branch_pattern") or "<role>/issue-<n>").replace("<n>", str(issue.get("number")))
    commands = "; ".join(i["command"] for i in bundle)
    constraints = (
        f"One task = one PR: one branch, one pull request. Commit and push the branch of the workspace "
        f"you were given (if you create one yourself, name it `{branch}`); never commit or push to "
        f"`{base}`. Work only inside {profile['repo']}; no other repository, no infrastructure/GitOps "
        f"changes. "
        f"EVIDENCE BUNDLE (mandatory, all of it, verbatim output with exit code): {commands}. "
        "Paste each command with its real output and an `exit=0` line; a command you could not run is "
        "reported as `NOT RUN` with the reason, never omitted and never replaced by a guess. A missing "
        "command is an automatic REJECT at review even when the change is correct. The bundle is the "
        "floor, not proof of completeness: re-derive it from the repository rules and run any gate they "
        "require beyond this list, reporting it in the same format. "
        "No secrets/keys in code, commits, PR body or the transcript. No direct CLI-agent invocations. "
        "Return exactly five sections: Branch and commit / Pull request / Evidence / Files changed / "
        "Not done or not verified."
    )
    objective = (f"Resolve {profile['repo']} issue #{issue.get('number')}: {issue.get('title')}. "
                 f"Acceptance criteria are in the issue body below; prove every gate the repository "
                 f"requires before opening the PR.")
    locations = ", ".join((profile.get("sources_of_truth") or []) + [f"issue #{issue.get('number')}"])
    return {
        "backend": backend,
        "mode": mode,
        "task": {
            "briefing": render_brief(profile_name, profile, issue),
            "objective": objective,
            "locations": locations,
            "constraints": constraints,
        },
        "files": list(profile.get("sources_of_truth") or []),
    }


# ---------------------------------------------------------------- brief

def render_brief(profile_name: str, profile: dict, issue: dict) -> str:
    """Render the brief handed to the worker (also used as the kanban task body)."""
    number = issue.get("number")
    title = (issue.get("title") or "").strip()
    body = (issue.get("body") or "").strip()
    url = issue.get("url") or ""
    labels = ", ".join(sorted(issue.get("labels") or [])) or "(none)"
    repo = profile["repo"]
    base = profile.get("base_branch", "main")
    branch = (profile.get("branch_pattern") or "<role>/issue-<n>").replace("<n>", str(number))
    bundle = bundle_commands(profile)

    lines: list[str] = []
    add = lines.append
    add(f"# Worker brief — {repo} issue #{number}")
    add("")
    add("You are the implementer for exactly ONE task. The task is done when the evidence below is")
    add("complete, not when the change looks right.")
    add("")
    add("## Goal")
    add("")
    add(title)
    add("")
    if body:
        add("## Issue body (binding acceptance criteria)")
        add("")
        for raw in body.splitlines():
            add("> " + raw if raw.strip() else ">")
        add("")
    add("## Sources of truth")
    add("")
    st = profile.get("sources_of_truth") or []
    if st:
        for rel in st:
            add(f"- `{rel}` in {repo} — repository rules; read before editing.")
    add(f"- GitHub issue #{number} ({url or 'see the task board'}) — its acceptance criteria are binding.")
    add(f"- Labels: {labels}")
    add("")
    add("## Constraints")
    add("")
    add(f"- One task = one PR: one branch, one pull request. Commit and push the branch of the workspace")
    add(f"  you were given (a project worktree already has a dedicated branch; if you create one yourself,")
    add(f"  name it `{branch}`). Never commit or push to `{base}`.")
    add(f"- Work only inside {repo}. No other repository, no infrastructure/GitOps changes.")
    add("- Touch only the files this task needs: no drive-by refactors, renames or reformatting.")
    add("- Repair is bounded: if one command is red three times in a row, stop and hand over with its")
    add("  output. A fourth attempt is not allowed, and a transcript with three red runs of the same")
    add("  command is rejected — a loop that cannot stop is the failure this repository exists to catch.")
    add("- No secrets, keys or tokens in code, commits, the PR body or the evidence transcript.")
    add("- No direct CLI-agent invocations anywhere in the repository (its own guardrail scans for that).")
    add("- If the workspace is not a clone of " + repo + " at " + base + ": stop and report — do not clone,")
    add("  and do not write outside the workspace.")
    add("")
    add("## Done-when")
    add("")
    for item in bundle:
        add(f"- `{item['command']}` exits 0")
    add(f"- The branch is pushed. Open the pull request against `{base}` yourself when `gh auth status`")
    add("  succeeds in your environment; otherwise report branch + head SHA and the orchestrator opens it.")
    add("  Either way the PR body carries the evidence bundle below, verbatim.")
    add("")
    add("## Evidence bundle — MANDATORY, ALL of it, verbatim output + exit code")
    add("")
    add("This bundle is every gate this repository requires, not a subset. A missing command is an")
    add("automatic REJECT at review even when the change itself is correct — the reviewer re-runs the")
    add("bundle in a separate worktree of your head commit and will find the gap.")
    add("")
    add("| # | command | why this repository requires it |")
    add("|---|---|---|")
    for i, item in enumerate(bundle, 1):
        add(f"| {i} | `{item['command']}` | {item['source']} |")
    add("")
    add("Transcript format — paste the real terminal output, then the exit code, per command:")
    add("")
    add("```text")
    add("$ <command>")
    add("<verbatim output; do not summarize, do not truncate>")
    add("exit=0")
    add("```")
    add("")
    add("Rules for the transcript:")
    add("")
    add("- Every command above appears, with its exit code on a line of its own (`exit=0`) **inside that")
    add("  command's own block** — before the next command line. An exit code borrowed from a neighbouring")
    add("  command proves nothing and is treated as missing.")
    add("- A command you could not run is reported as `NOT RUN` plus the reason — never omitted, never")
    add("  replaced by a guess about what it would have printed.")
    add("- A non-zero exit code is reported as it happened; then fix the cause and re-run that command.")
    add("- The same transcript goes into the PR body and into your final answer. Verdicts, adjectives and")
    add("  summaries do not substitute for it.")
    add("")
    add("## Completeness of this list")
    add("")
    source = profile.get("bundle_source") or (profile.get("sources_of_truth") or ["the repository rules"])[0]
    add(f"The list above is the floor, not a proof of completeness: it is derived from {source}.")
    add(f"Re-read that source yourself. If it requires a gate this list does not name, run it too and")
    add("report it in the same format; if the repository's own rules are stricter than this list, the")
    add("stricter reading wins. Say explicitly in section 5 which gates you ran beyond the list, and name")
    add("any requirement you found but could not satisfy.")
    add("")
    add("## Review expectations")
    add("")
    for rule in profile.get("pr_rules") or []:
        add(f"- {rule}")
    add("- State in the PR body the concrete acceptance criteria you implemented against. A triage label")
    add("  is the triage role's approval of the issue, not a substitute for criteria you can be held to.")
    add("")
    add("## Output contract — return exactly these five sections, in this order")
    add("")
    add("1. `## Branch and commit` — branch name and commit SHA.")
    add("2. `## Pull request` — PR URL and title, or, when `gh auth status` did not succeed in your")
    add("   environment, the branch + SHA you pushed and the PR body you propose.")
    add("3. `## Evidence` — the transcript above, verbatim.")
    add("4. `## Files changed` — output of `git diff --stat <base>...HEAD`.")
    add("5. `## Not done / not verified` — anything unfinished, skipped or unverifiable, stated plainly.")
    add("")
    add("No preamble. No verdict about your own work. If a prerequisite above is missing, say so in")
    add("section 5 and stop instead of improvising.")
    add("")
    return "\n".join(lines)


# ---------------------------------------------------------------- validator

def _command_lines(transcript: str, commands: list[str]) -> list[tuple[int, str, bool]]:
    """``(index, text, is_command)`` per non-empty line, with the bundle commands marked.

    The marker is what bounds each command's evidence: a command owns only the lines up to the next
    command, so an exit code can never be borrowed from a neighbour.
    """
    wanted = set(commands)
    out = []
    for i, raw in enumerate(transcript.splitlines()):
        stripped = PROMPT_RE.sub("", raw).strip()
        if stripped:
            out.append((i, stripped, stripped in wanted))
    return out


def verify_evidence(profile: dict, transcript: str) -> list[str]:
    """Return the reasons the transcript is NOT acceptable; an empty list means accepted.

    A command counts as proven only when its own line is present verbatim and an exit code appears on
    its own line inside that command's own block (up to the next command). Every occurrence is judged,
    not just the first: a rerun that failed after a passing run is still a failed gate, and a `NOT RUN`
    marker anywhere in the block disqualifies it. Anything else is 'not proven', which is exactly the gap
    that made the first PR review reject the worker.
    """
    reasons: list[str] = []
    if not transcript or not transcript.strip():
        return ["evidence transcript is empty: nothing is proven"]
    bundle = bundle_commands(profile)
    lines = _command_lines(transcript, [i["command"] for i in bundle])
    for item in bundle:
        command = item["command"]
        matches = [i for i, (_, text, is_cmd) in enumerate(lines) if is_cmd and text == command]
        if not matches:
            reasons.append(f"`{command}` was not run (no verbatim command line in the transcript)")
            continue
        red_runs = 0
        for occurrence, idx in enumerate(matches, 1):
            tail = lines[idx + 1:]
            end = next((n for n, line in enumerate(tail) if line[2]), len(tail))
            block = tail[:end]
            where = f"`{command}`" + (f" (occurrence {occurrence} of {len(matches)})" if len(matches) > 1 else "")
            codes = [m.group(1) for _, text, _ in block for m in [EXIT_LINE_RE.match(text)] if m]
            if not codes:
                reasons.append(f"{where} has no exit code on a line of its own in its own block "
                               f"(expected `exit=0` before the next command)")
                continue
            if codes[0] != "0":
                red_runs += 1
                reasons.append(f"{where} exited {codes[0]}, expected 0")
            if any(NOT_RUN_RE.match(text) for _, text, _ in block):
                reasons.append(f"{where} is marked NOT RUN")
        # Bounded repair: the brief allows two red runs, the third is a handover. A worker that kept
        # retrying is not more diligent — it is the loop that never stops, with the bill to match.
        if red_runs >= 3:
            reasons.append(f"`{command}` was red {red_runs} times: repair is bounded at three attempts, "
                           "the worker had to hand over with the output instead of retrying")
    return reasons


# ---------------------------------------------------------------- CLI

def _issue_from_args(args) -> dict:
    body = args.body or ""
    if args.body_file:
        with open(args.body_file, encoding="utf-8") as fh:
            body = fh.read()
    return {
        "number": args.issue,
        "title": args.title or f"issue #{args.issue}",
        "body": body,
        "url": args.url or f"https://github.com/{args.repo or 'OWNER/REPO'}/issues/{args.issue}",
        "labels": [l.strip() for l in (args.labels or "").split(",") if l.strip()],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Worker brief renderer and evidence-bundle validator.")
    ap.add_argument("--root", default=ROOT)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("profiles", help="list known repository profiles")

    b = sub.add_parser("build", help="render the brief for one issue")
    b.add_argument("--profile", required=True)
    b.add_argument("--repo", help="override the repository slug used in the issue URL")
    b.add_argument("--issue", type=int, required=True)
    b.add_argument("--title", default="")
    b.add_argument("--body", default="")
    b.add_argument("--body-file")
    b.add_argument("--url", default="")
    b.add_argument("--labels", default="")
    b.add_argument("--format", choices=["markdown", "ywcrew-json"], default="markdown",
                   help="markdown brief, or the ywcrew five-part task spec with the same brief inside")
    b.add_argument("--backend", default="harness", help="ywcrew backend for --format ywcrew-json")
    b.add_argument("--mode", default="edit", choices=["edit", "read-only"],
                   help="ywcrew mode for --format ywcrew-json")
    b.add_argument("--out", help="write the brief here instead of stdout")

    v = sub.add_parser("verify", help="check an evidence transcript against the profile bundle")
    v.add_argument("--profile", required=True)
    v.add_argument("--evidence", required=True)
    v.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)
    if args.cmd == "profiles":
        for name, prof in sorted(load_profiles(args.root).items()):
            bundle = ", ".join(i["command"] for i in bundle_commands(prof))
            print(f"{name}\t{prof['repo']}\t{bundle}")
        return 0
    try:
        profile = get_profile(args.root, args.profile)
    except (KeyError, OSError, json.JSONDecodeError) as exc:
        print(f"profile error: {exc}", file=sys.stderr)
        return 2

    if args.cmd == "build":
        issue = _issue_from_args(args)
        if args.format == "ywcrew-json":
            payload = render_ywcrew_task(args.profile, profile, issue, args.backend, args.mode)
            text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        else:
            text = render_brief(args.profile, profile, issue)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(text)
            print(f"brief: {args.out} ({len(text)} chars, format={args.format})")
        else:
            sys.stdout.write(text)
        return 0

    text = open(args.evidence, encoding="utf-8").read()
    reasons = verify_evidence(profile, text)
    if args.json:
        print(json.dumps({"profile": args.profile, "ok": not reasons, "reasons": reasons}, indent=2))
    else:
        for reason in reasons:
            print(f"[MISSING] {reason}")
    if reasons:
        print(f"evidence incomplete: {len(reasons)} gap(s) — a reviewer will reject this bundle")
        print(STOP_MARKER)
        return 1
    print("evidence bundle complete: every mandatory command ran with a visible exit code")
    print(PASS_MARKER)
    return 0


if __name__ == "__main__":
    sys.exit(main())
