#!/usr/bin/env python3
"""Second-level review: the whole pull request, read by an engine that did not write it.

`scripts/worker_brief.py verify` proves the worker ran the bundle. It says nothing about whether the change
is a good idea: a worker can prove every gate and still ship the wrong design. This script sends the entire
PR diff to an independent model through `ywcrew` (the only door to the CLI agents, per AGENTS.md), asks for
a verdict, and either prints it or posts it as a PR review.

    python3 scripts/review_pr.py --profile sample-service --pr 3 --dry-run     # build the brief, dispatch nothing
    python3 scripts/review_pr.py --profile sample-service --pr 3               # dispatch, print the verdict
    python3 scripts/review_pr.py --profile sample-service --pr 3 --post        # also post it to the PR

Verdict: PR_REVIEW_PASS_0001 (exit 0) when the reviewer accepts the change; PR_REVIEW_STOP_0001 (exit 1)
when it asks for changes, rejects it, or the dispatch cannot be made. A reviewer that cannot be reached is
not an approval.

Cost note: `--backend` and `--model` default to `agy:gemini-3.8-flash` on purpose. The panel (`--panel`) is
available and costs one dispatch per member; it is not the default because a review is not improved by
paying three times for the same first look.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASS_MARKER = "PR_REVIEW_PASS_0001"
STOP_MARKER = "PR_REVIEW_STOP_0001"
VERDICT_RE = re.compile(r"VERDICT\s*:\s*(APPROVE|CHANGES|REJECT)", re.I)
MAX_DIFF_CHARS = 60_000

sys.path.insert(0, os.path.join(ROOT, "scripts"))
try:
    import worker_brief  # noqa: E402
except ImportError:  # pragma: no cover
    worker_brief = None


def run(cmd: list[str], stdin: str | None = None, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout)


def profile_or_die(name: str) -> dict:
    if worker_brief is None:
        sys.exit("worker_brief.py is not importable: cannot resolve a repo profile")
    try:
        return worker_brief.get_profile(ROOT, name)
    except (KeyError, SystemExit):
        print(f"unknown profile {name!r}; try one of: "
              f"{', '.join(sorted(worker_brief.load_profiles(ROOT)['profiles']))}", file=sys.stderr)
        raise SystemExit(1)


def pr_meta(repo: str, pr: int) -> dict:
    proc = run(["gh", "pr", "view", str(pr), "-R", repo, "--json",
                "title,body,headRefName,baseRefName,headRefOid,files,additions,deletions"])
    if proc.returncode != 0:
        raise RuntimeError(f"gh pr view failed: {proc.stderr.strip()[:300]}")
    return json.loads(proc.stdout)


def pr_diff(repo: str, pr: int) -> str:
    proc = run(["gh", "pr", "diff", str(pr), "-R", repo])
    if proc.returncode != 0:
        raise RuntimeError(f"gh pr diff failed: {proc.stderr.strip()[:300]}")
    return proc.stdout


def build_task(profile: dict, repo: str, pr: int, meta: dict, diff: str, bundle: list[str]) -> dict:
    """A five-part task for the dispatcher. English by policy: the payload is not a chat message."""
    files = [f.get("path") for f in (meta.get("files") or []) if f.get("path")]
    truncated = diff[:MAX_DIFF_CHARS]
    cut = "" if len(diff) <= MAX_DIFF_CHARS else (
        f"\n\n[diff truncated: showing the first {MAX_DIFF_CHARS} of {len(diff)} characters]")
    rules = "; ".join((profile.get("pr_rules") or [])[:5])
    task = {
        "objective": (
            f"Review pull request #{pr} in {repo} — '{meta.get('title', '')}' — as the second level of review. "
            f"The worker already proved the evidence bundle, so do not re-litigate whether the commands ran: "
            f"judge the change itself. Merge base {meta.get('baseRefName')} <- head {meta.get('headRefName')} "
            f"({meta.get('additions', '?')} added, {meta.get('deletions', '?')} removed, {len(files)} files). "
            "Say plainly whether this should be merged."),
        "briefing": (
            "This repository accepts work only inside a loop with evidence: one task = one PR, the PR body "
            "carries the mandatory commands verbatim with exit codes, and a reviewer re-runs the bundle in a "
            "separate worktree of the head commit. Your review is the second, independent level: design, "
            "correctness, failure modes and whether the change does what the issue asked — not the plumbing."
            + (f" Repository PR rules: {rules}." if rules else "")
            + (f" The pull request body:\n\n{(meta.get('body') or '')[:4000]}" if meta.get("body") else "")),
        "locations": ("The full diff is below and it is the whole change. Files touched: "
                      + (", ".join(files[:40]) if files else "(none reported)")
                      + ". The repository's own gates live in scripts/ (check_loop.py, assert_dims.py, "
                        "regressions.py) and configs/repo_profiles.json."),
        "constraints": (
            "Read-only: propose changes, never rewrite the code. Do not comment on formatting that the "
            "project's own linter accepts. Do not invent files or APIs you cannot see in the diff. If the "
            "diff is truncated, say which part you could not see instead of guessing. Do not discuss the "
            "evidence bundle's completeness — another gate covers that."),
        "output_contract": (
            "Markdown, at most 400 words. Sections: 'Findings' (each with file:line, severity "
            "blocker/major/minor, and the consequence for a user or operator), 'Missing tests or evidence', "
            "'Questions for the author'. The last line of your reply must be exactly "
            "'VERDICT: APPROVE' or 'VERDICT: CHANGES' or 'VERDICT: REJECT' — nothing after it."),
    }
    payload = "\n\n".join([f"## Objective\n{task['objective']}", f"## Diff\n```diff\n{truncated}\n```{cut}"])
    # The dispatcher can hand the reviewer the real files, but it resolves globs against the dispatcher's
    # own cwd — and the reviewed repository is usually a different checkout. A path that does not exist
    # there aborts the whole dispatch, so only files this tree actually has are offered; the diff in the
    # prompt is what the review is based on either way.
    local = [f for f in (files or []) if os.path.exists(os.path.join(ROOT, f))]
    return {"task": task, "diff": payload, "files": local or None}


def dispatch(spec: dict, backend: str, model: str | None, panel: bool) -> tuple[str, str]:
    """Send the task through ywcrew and return (raw_result_text, run_reference)."""
    body = {"mode": "read-only", "label": spec["task"]["objective"][:60],
            "task": dict(spec["task"]), "files": spec.get("files") or []}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump(body, fh, ensure_ascii=False)
        task_path = fh.name
    try:
        if panel:
            cmd = ["ywcrew", "panel", "--task-file", task_path]
        else:
            body["backend"] = backend
            if model:
                body["model"] = model
            with open(task_path, "w", encoding="utf-8") as fh:
                json.dump(body, fh, ensure_ascii=False)
            cmd = ["ywcrew", "run", "--task-file", task_path]
        proc = run(cmd)
        if proc.returncode != 0:
            raise RuntimeError(f"{' '.join(cmd[:2])} failed: {(proc.stderr or proc.stdout).strip()[:300]}")
        out = proc.stdout.strip()
        run_id = None
        try:
            data = json.loads(out)
            run_id = data.get("runId") or data.get("run_id") or (data.get("run") or {}).get("id")
        except json.JSONDecodeError:
            match = re.search(r"\b(run_[A-Za-z0-9_-]+|[0-9a-f]{8,})\b", out)
            run_id = match.group(1) if match else None
        if not run_id:
            raise RuntimeError(f"could not read a run id from ywcrew output: {out[:300]}")
        # `ywcrew result` prints JSON by default; the flag that would ask for it does not exist and the
        # earlier version failed on it after a review had already been paid for.
        result = run(["ywcrew", "result", run_id, "--wait", "--timeout", "1200"], timeout=1800)
        text = result.stdout or result.stderr
        if result.returncode != 0:
            raise RuntimeError(f"ywcrew result failed for {run_id}: {text.strip()[:300]}")
        return text, run_id
    finally:
        os.unlink(task_path)


def extract_text(raw: str) -> str:
    """The reviewer's prose, whichever shape the dispatcher used to wrap it."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    chunks: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            for key in ("output", "text", "content", "result", "summary"):
                value = node.get(key)
                if isinstance(value, str) and len(value) > 40:
                    chunks.append(value)
            for value in node.values():
                if isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    return "\n\n".join(chunks) if chunks else raw


def verdict_of(text: str) -> str | None:
    found = VERDICT_RE.findall(text)
    return found[-1].upper() if found else None


def post_review(repo: str, pr: int, body: str, verdict: str) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as fh:
        fh.write(body)
        path = fh.name
    try:
        flag = {"APPROVE": "--approve", "CHANGES": "--request-changes"}.get(verdict, "--comment")
        proc = run(["gh", "pr", "review", str(pr), "-R", repo, flag, "--body-file", path])
        if proc.returncode != 0:
            return f"failed: {(proc.stderr or proc.stdout).strip()[:200]}"
        return f"posted as {flag}"
    finally:
        os.unlink(path)


def selftest() -> int:
    """Offline unit test: assert parser verdict mapping and verify that missing verdict never posts."""
    # The selftest must pass in any tree: the published one ships `sample-service`, the working one ships
    # the real profiles. Hardcoding a profile name made the test pass only where that name happened to exist.
    profiles = sorted((worker_brief.load_profiles(ROOT) if worker_brief else {}) or {})
    if not profiles:
        print("selftest: no repository profile to parse the fixtures with", file=sys.stderr)
        return 1
    profile_name = profiles[0]
    fixtures = [
        ("configs/fixtures/review_approve.json", "APPROVE", PASS_MARKER, 0),
        ("configs/fixtures/review_changes.json", "CHANGES", STOP_MARKER, 1),
        ("configs/fixtures/review_no_verdict.json", None, STOP_MARKER, 1),
    ]
    for path, expected_verdict, expected_marker, expected_rc in fixtures:
        full_path = os.path.join(ROOT, path)
        if not os.path.exists(full_path):
            print(f"selftest: missing fixture {path}", file=sys.stderr)
            return 1
        raw = open(full_path, encoding="utf-8").read()
        text = extract_text(raw)
        verdict = verdict_of(text)
        if verdict != expected_verdict:
            print(f"selftest failed for {path}: expected verdict {expected_verdict!r}, got {verdict!r}", file=sys.stderr)
            return 1
    # Run main() on all three fixtures to assert full end-to-end exit code and output
    for path, expected_verdict, expected_marker, expected_rc in fixtures:
        full_path = os.path.join(ROOT, path)
        proc = subprocess.run(
            [sys.executable, __file__, "--profile", profile_name, "--pr", "0", "--result-file", full_path],
            capture_output=True, text=True
        )
        if proc.returncode != expected_rc:
            print(f"selftest failed for {path}: expected rc {expected_rc}, got {proc.returncode}. Output:\n{proc.stdout}\n{proc.stderr}", file=sys.stderr)
            return 1
        if expected_marker not in proc.stdout and expected_marker not in proc.stderr:
            print(f"selftest failed for {path}: marker {expected_marker} not found in output", file=sys.stderr)
            return 1
        if expected_verdict is None:
            if "the reviewer did not return a VERDICT line" not in proc.stderr:
                print(f"selftest failed for {path}: did not name missing verdict line in stderr", file=sys.stderr)
                return 1

    # Assert that --post is NOT attempted when the verdict is missing
    proc_post = subprocess.run(
        [sys.executable, __file__, "--profile", profile_name, "--pr", "0",
         "--result-file", os.path.join(ROOT, "configs/fixtures/review_no_verdict.json"), "--post"],
        capture_output=True, text=True
    )
    if proc_post.returncode != 1:
        print("selftest failed: --post with missing verdict must exit 1", file=sys.stderr)
        return 1
    if "post:" in proc_post.stdout or "post:" in proc_post.stderr:
        print("selftest failed: --post attempted despite missing verdict", file=sys.stderr)
        return 1

    print("selftest: all fixture verdicts and stop conditions verified")
    print(PASS_MARKER)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Second-level review of a pull request via ywcrew.")
    # Required in practice, but validated after the --selftest branch: a bare `--selftest` must be able to
    # run without inventing a repository, while every other invocation still fails fast instead of
    # defaulting to some arbitrary profile and pull request.
    ap.add_argument("--profile", help="repo profile that names the repository (required unless --selftest)")
    ap.add_argument("--pr", type=int, help="pull request number to review (required unless --selftest)")
    ap.add_argument("--backend", default="agy")
    ap.add_argument("--model", default="gemini-3.8-flash")
    ap.add_argument("--panel", action="store_true", help="use ywcrew panel instead of one backend")
    ap.add_argument("--post", action="store_true", help="post the verdict to the PR as a review")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the brief, dispatch nothing (ignored with --result-file: nothing is dispatched)")
    ap.add_argument("--diff-file", help="review a diff from a file instead of asking gh (offline)")
    ap.add_argument("--result-file", help="parse an existing `ywcrew result` JSON instead of dispatching")
    ap.add_argument("--selftest", action="store_true", help="run offline fixture assertions, then exit")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    if not args.profile or args.pr is None:
        ap.error("--profile and --pr are required unless --selftest is given")

    profile = profile_or_die(args.profile)
    repo = profile["repo"]

    if args.result_file:
        try:
            raw = open(args.result_file, encoding="utf-8").read()
        except OSError as exc:
            print(f"cannot read the result file: {exc}", file=sys.stderr)
            print(STOP_MARKER)
            return 1
        run_id = "from --result-file"
    else:
        if not shutil.which("gh") and not args.diff_file:
            print("gh is not installed: pass --diff-file or install the GitHub CLI", file=sys.stderr)
            print(STOP_MARKER)
            return 1
        try:
            if args.diff_file:
                diff = open(args.diff_file, encoding="utf-8", errors="replace").read()
                meta = {"title": os.path.basename(args.diff_file), "files": [], "baseRefName": "?",
                        "headRefName": "?"}
            else:
                meta = pr_meta(repo, args.pr)
                diff = pr_diff(repo, args.pr)
        except (RuntimeError, OSError) as exc:
            print(f"cannot read the pull request: {exc}", file=sys.stderr)
            print(STOP_MARKER)
            return 1

        bundle = [i["command"] for i in (worker_brief.bundle_commands(profile) if worker_brief else [])]
        spec = build_task(profile, repo, args.pr, meta, diff, bundle)

        if args.dry_run:
            print(spec["diff"])
            print()
            print(f"--- not dispatched. backend={args.backend}:{args.model} panel={args.panel} ---")
            print(PASS_MARKER)
            return 0

        try:
            raw, run_id = dispatch(spec, args.backend, args.model, args.panel)
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            print(f"dispatch failed: {exc}", file=sys.stderr)
            print(STOP_MARKER)
            return 1

    text = extract_text(raw)
    verdict = verdict_of(text)
    print(text.strip()[:6000])
    print()
    print(f"review run: {run_id}")
    if not verdict:
        print("the reviewer did not return a VERDICT line: an unreadable review is not an approval",
              file=sys.stderr)
        print(STOP_MARKER)
        return 1
    print(f"verdict: {verdict}")
    if args.post:
        print("post: " + post_review(repo, args.pr,
                                     f"Second-level review ({args.backend}"
                                     f"{':' + args.model if args.model else ''} via ywcrew)\n\n"
                                     + text.strip(), verdict))
    if verdict == "APPROVE":
        print(PASS_MARKER)
        return 0
    print(STOP_MARKER)
    return 1


if __name__ == "__main__":
    sys.exit(main())
