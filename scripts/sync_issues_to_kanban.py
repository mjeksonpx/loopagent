#!/usr/bin/env python3
"""Issue bridge — GitHub issues -> Hermes kanban execution queue (PLANS.md stage 6).

Roles of the two sides
----------------------
* GitHub issues are the **source of truth** and the external feedback channel (testers, operator, chief).
* The Hermes kanban board is the **local execution queue**: tasks are claimed atomically and executed by
  a named profile in an isolated workspace. The bridge is the only path from one to the other.

What the bridge guarantees
--------------------------
* **One task = one PR**: one open issue produces exactly one kanban task, whose body is the worker brief
  rendered by ``scripts/worker_brief.py`` (mandatory evidence bundle included, not implied).
* **Idempotence**: the task carries the issue-derived idempotency key ``gh-<owner>-<repo>-<n>``; a second
  run reports the task as already queued instead of minting a duplicate.
* **Fail-closed**: an issue without acceptance criteria is *not* queued (it is reported as needing
  triage), an unknown repository profile is refused, and ``--apply`` refuses to queue work that no
  worker could execute (no workspace/project target configured) — a queue that cannot run is a lie.
* **Read-only by default**: mutations happen only with ``--apply``. ``--issues-file`` is an offline
  fixture source and can never be applied.

Left to the triage role (stage 5): the semantic half of validity — whether the reported behaviour is
reproducible at all. The bridge enforces only the machine-checkable subset (labels, body substance,
acceptance criteria) and says so in its report.

Usage
-----
    python3 scripts/sync_issues_to_kanban.py --profile sample-service                    # dry run, live gh
    python3 scripts/sync_issues_to_kanban.py --profile sample-service --issues-file configs/fixtures/issues_sample.json
    python3 scripts/sync_issues_to_kanban.py --profile sample-service --apply            # queue for real
    python3 scripts/sync_issues_to_kanban.py --profile sample-service --selftest         # prove the write path

Exit 0 prints ``ISSUE_BRIDGE_PASS_0001``; a failure prints ``ISSUE_BRIDGE_STOP_0001`` and exits 1.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import worker_brief  # noqa: E402  (sibling script: the canonical brief + validator)

PASS_MARKER = "ISSUE_BRIDGE_PASS_0001"
STOP_MARKER = "ISSUE_BRIDGE_STOP_0001"
REPORT_REL = "artifacts/issue_bridge_report.json"
SELFTEST_KEY = "issue-bridge-selftest"
SELFTEST_TITLE = "[issue-bridge selftest] write path check"
MIN_BODY_CHARS = 120
TITLE_LIMIT = 110
GH_FIELDS = "number,title,body,labels,url,state"
ACCEPTANCE_RE = re.compile(
    r"^\s*#{1,4}\s*(acceptance|acceptance criteria|критерии приёмки)\b"
    r"|^\s*\**acceptance criteria\**\s*:",
    re.I | re.M,
)


# ---------------------------------------------------------------- sources

def gh_issues(repo: str, limit: int) -> list[dict]:
    proc = subprocess.run(
        ["gh", "issue", "list", "-R", repo, "--state", "open", "--limit", str(limit),
         "--json", GH_FIELDS],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gh issue list failed ({proc.returncode}): {proc.stderr.strip()}")
    return [normalize_issue(i) for i in json.loads(proc.stdout or "[]")]


def fixture_issues(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    issues = payload["issues"] if isinstance(payload, dict) else payload
    return [normalize_issue(i) for i in issues]


def normalize_issue(raw: dict) -> dict:
    labels = raw.get("labels") or []
    names = [l.get("name", "") if isinstance(l, dict) else str(l) for l in labels]
    return {
        "number": int(raw["number"]),
        "title": (raw.get("title") or "").strip(),
        "body": raw.get("body") or "",
        "url": raw.get("url") or "",
        "labels": [n for n in names if n],
    }


# ---------------------------------------------------------------- triage (machine-checkable subset)

def classify(issue: dict, profile: dict) -> tuple[str, str]:
    """Return ('queue' | 'needs-triage' | 'skip', reason).

    The reason names the *basis* of the verdict, because the two queue paths are not equally strong: an
    acceptance-criteria section is evidence in the issue itself, while a triage label is the triage
    role's semantic approval — the brief then requires the worker to state the concrete criteria it
    implemented against.
    """
    labels = {l.lower() for l in issue.get("labels") or []}
    skipped = labels & {str(l).lower() for l in profile.get("skip_labels") or []}
    if skipped:
        return "skip", f"label(s) {', '.join(sorted(skipped))}: not work for this queue"
    body = (issue.get("body") or "").strip()
    if len(body) < MIN_BODY_CHARS:
        return "needs-triage", (f"body is {len(body)} chars (< {MIN_BODY_CHARS}): nothing to reproduce "
                                "or to accept")
    triaged = sorted(labels & {str(l).lower() for l in profile.get("triage_labels") or []})
    if ACCEPTANCE_RE.search(body):
        return "queue", "acceptance-criteria section present in the issue"
    if triaged:
        return "queue", (f"triage label {', '.join(triaged)} (triage role approved; the brief requires the "
                         "worker to state the concrete criteria)")
    return "needs-triage", "no acceptance-criteria section and no triage label"


# ---------------------------------------------------------------- kanban queue

ESTIMATES_REL = "configs/task_estimates.json"


def issue_key(profile: dict, number: int) -> str:
    return "gh-" + profile["repo"].replace("/", "-") + f"-{number}"


def load_estimates(root: str = ROOT) -> dict:
    """Per-assignee timeboxes measured from queue history (scripts/estimate_task.py writes this file)."""
    try:
        with open(os.path.join(root, ESTIMATES_REL), encoding="utf-8") as fh:
            return json.load(fh).get("assignees") or {}
    except (OSError, json.JSONDecodeError):
        return {}


def assignee_for(issue: dict, profile: dict) -> str:
    """A role label wins over the profile default: 'qa' issues go to the qa profile, everything else to
    the profile's implementer. One issue, one owner."""
    kb = profile.get("kanban") or {}
    labels = {l.lower() for l in issue.get("labels") or []}
    for label, assignee in (kb.get("assignee_by_label") or {}).items():
        if str(label).lower() in labels:
            return assignee
    return kb.get("assignee") or ""


def task_title(profile_name: str, issue: dict) -> str:
    head = f"[{profile_name}#{issue['number']}] {issue['title']}"
    return head if len(head) <= TITLE_LIMIT else head[:TITLE_LIMIT - 1].rstrip() + "…"


def existing_tasks() -> list[dict]:
    proc = subprocess.run(["hermes", "kanban", "list", "--json"], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"hermes kanban list failed ({proc.returncode}): {proc.stderr.strip()}")
    return json.loads(proc.stdout or "[]")


def queued_numbers(tasks: list[dict], profile_name: str) -> dict[int, str]:
    """Map issue number -> task id for tasks this bridge already created."""
    prefix = f"[{profile_name}#"
    out: dict[int, str] = {}
    for task in tasks:
        title = task.get("title") or ""
        m = re.match(re.escape(prefix) + r"(\d+)\]", title)
        if m:
            out[int(m.group(1))] = task.get("id", "?")
    return out


def kanban_create(title: str, brief_path: str, profile: dict, key: str,
                  initial_status: str | None = None, assignee: str | None = None,
                  max_runtime: str | None = None) -> dict:
    kb = profile.get("kanban") or {}
    cmd = ["hermes", "kanban", "create", title, "--body-file", brief_path,
           "--idempotency-key", key, "--created-by", "issue-bridge", "--json"]
    assignee = assignee or kb.get("assignee")
    if assignee:
        cmd += ["--assignee", assignee]
    if max_runtime:
        cmd += ["--max-runtime", max_runtime]
    if kb.get("project"):
        cmd += ["--project", kb["project"]]
    if kb.get("workspace"):
        cmd += ["--workspace", kb["workspace"]]
    if kb.get("priority") is not None:
        cmd += ["--priority", str(kb["priority"])]
    if initial_status:
        cmd += ["--initial-status", initial_status]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"hermes kanban create failed ({proc.returncode}): {proc.stderr.strip()}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"raw": proc.stdout.strip()}


def backlog_dir(root: str) -> str:
    return os.path.join(root, "backlog")


def backlog_existing(root: str, profile_name: str) -> dict[int, str]:
    """Issue number -> ledger file name, for cards this bridge already wrote as markdown."""
    prefix = f"[{profile_name}#"
    out: dict[int, str] = {}
    directory = backlog_dir(root)
    if not os.path.isdir(directory):
        return out
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".md"):
            continue
        try:
            with open(os.path.join(directory, name), encoding="utf-8") as fh:
                head = fh.readline()
        except OSError:
            continue
        m = re.search(re.escape(prefix) + r"(\d+)\]", head)
        if m:
            out[int(m.group(1))] = name
    return out


def backlog_write(root: str, title: str, brief: str, profile: dict, key: str,
                  assignee: str | None, max_runtime: str | None) -> dict:
    """Write the card as a markdown ledger file: the queue works without any execution backend.

    `--target kanban` hands the card to a dispatcher that runs it; `--target backlog` only records it,
    so a clone without Hermes still turns issues into reviewable, versioned task specs.
    """
    directory = backlog_dir(root)
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{key}.md")
    number = key.rsplit("-", 1)[-1]
    header = (f"# {title}\n\n"
              f"- issue: {profile['repo']}#{number}\n"
              f"- assignee: {assignee or '(unset)'}\n"
              f"- max-runtime: {max_runtime or '(none)'}\n"
              f"- idempotency-key: {key}\n"
              f"- status: queued\n"
              f"- queued-at: {dt.datetime.now().astimezone().isoformat(timespec='seconds')}\n\n---\n\n")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(header + brief)
    return {"id": os.path.relpath(path, root), "status": "queued"}


AUTONOMY_REL = "configs/autonomy.json"


def autonomy_config(root: str) -> dict:
    """The ladder between a human placing every card and the machine placing them overnight.

    Default is the safest rung, not the most convenient one: a missing or unreadable config means HITL,
    because a bridge that starts queueing work nobody approved is worse than a bridge that asks.
    """
    path = os.path.join(root, AUTONOMY_REL)
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {"mode": "hitl", "max_cards": 0, "budget_tokens": 0, "source": f"{AUTONOMY_REL} missing or invalid"}
    cfg = cfg if isinstance(cfg, dict) else {}
    return {"mode": str(cfg.get("mode") or "hitl").lower(),
            "max_cards": cfg.get("max_cards"),
            "budget_tokens": cfg.get("budget_tokens"),
            "active": cfg.get("active"),
            "source": AUTONOMY_REL}


def autonomy_refusal(root: str, target: str) -> str | None:
    """Why --apply is not allowed right now, or None when it is.

    `backlog` writes a ledger line and starts nothing, so it is allowed at every rung; `kanban` starts a
    worker, and that is what a human has to authorise — or what an AFK config has to bound in advance.
    """
    if target != "kanban":
        return None
    cfg = autonomy_config(root)
    mode = cfg["mode"]
    if mode == "hitl":
        return ("autonomy is HITL: every card is placed by a human (`hermes kanban create` or the Telegram "
                "gateway). Set mode=afk in configs/autonomy.json with a budget to let the bridge queue work")
    if mode != "afk":
        return f"unknown autonomy mode {mode!r} in configs/autonomy.json (expected 'hitl' or 'afk')"
    budget = cfg["budget_tokens"]
    cards = cfg["max_cards"]
    if not isinstance(budget, int) or isinstance(budget, bool) or budget <= 0:
        return ("afk mode without a token budget: configs/autonomy.json needs budget_tokens > 0, otherwise "
                "an unattended bridge can spend without a ceiling")
    if not isinstance(cards, int) or isinstance(cards, bool) or cards < 1:
        return ("afk mode without max_cards: configs/autonomy.json needs max_cards >= 1, otherwise one poll "
                "can queue the whole issue tracker")
    return None


def apply_target_error(profile: dict) -> str | None:
    """Refuse to queue work no worker could execute (fail-closed, stage-6 prerequisite)."""
    kb = profile.get("kanban") or {}
    if kb.get("project") or kb.get("workspace"):
        return None
    return ("profile has neither kanban.project nor kanban.workspace: register the target repo as a "
            "Hermes project (`hermes project create`) or set kanban.workspace, otherwise the dispatched "
            "worker gets an empty scratch workspace and the brief's own stop-if fires")


# ---------------------------------------------------------------- selftest

def selftest(profile: dict) -> int:
    """Prove the queue write path live: create (blocked) -> show -> archive."""
    brief = "# issue-bridge selftest\n\nNo worker action required; this task exists to prove the bridge's\n" \
            "create/show/archive path and the idempotency key.\n"
    tmp = os.path.join(ROOT, "artifacts", "_issue_bridge_selftest.md")
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(brief)
    created = kanban_create(SELFTEST_TITLE, tmp, profile, SELFTEST_KEY, initial_status="blocked")
    task_id = created.get("id")
    if not task_id:
        print(f"selftest: create returned no task id: {created}")
        return 1
    show = subprocess.run(["hermes", "kanban", "show", task_id, "--json"],
                          capture_output=True, text=True)
    if show.returncode != 0:
        print(f"selftest: kanban show failed: {show.stderr.strip()}")
        return 1
    shown = json.loads(show.stdout)
    task = shown.get("task", shown)
    reuse = kanban_create(SELFTEST_TITLE, tmp, profile, SELFTEST_KEY, initial_status="blocked")
    same = reuse.get("id") == task_id
    print(f"selftest: id={task_id} status={task.get('status')} assignee={task.get('assignee')} "
          f"created_by={task.get('created_by')} body_len={len(task.get('body') or '')}")
    print(f"selftest: second create with the same idempotency key returned {reuse.get('id')} "
          f"(same task: {same})")
    archive = subprocess.run(["hermes", "kanban", "archive", task_id],
                             capture_output=True, text=True)
    archived = archive.returncode == 0
    print(f"selftest: archived={archived}")
    os.remove(tmp)
    return 0 if (same and archived and task.get("created_by") == "issue-bridge") else 1


# ---------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Bridge GitHub issues into the Hermes kanban queue.")
    ap.add_argument("--profile", required=True, help="repository profile from configs/repo_profiles.json")
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--issues-file", help="offline fixture source (never applied)")
    ap.add_argument("--apply", action="store_true", help="actually create the kanban tasks")
    ap.add_argument("--target", choices=["kanban", "backlog"], default="kanban",
                    help="where a card goes: 'kanban' hands it to the Hermes dispatcher, 'backlog' writes "
                         "a markdown ledger file under backlog/ (works without any execution backend)")
    ap.add_argument("--selftest", action="store_true", help="prove the kanban write path, then exit")
    ap.add_argument("--report", default=REPORT_REL)
    ap.add_argument("--print-brief", type=int, help="print the rendered brief for this issue number")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        profile = worker_brief.get_profile(args.root, args.profile)
    except (KeyError, OSError, json.JSONDecodeError) as exc:
        print(f"profile error: {exc}", file=sys.stderr)
        return 2

    if args.selftest:
        rc = selftest(profile)
        print(PASS_MARKER if rc == 0 else STOP_MARKER)
        return rc

    source = "fixture" if args.issues_file else "github"
    try:
        issues = (fixture_issues(args.issues_file) if args.issues_file
                  else gh_issues(profile["repo"], args.limit))
    except (OSError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"source error ({source}): {exc}", file=sys.stderr)
        print(STOP_MARKER)
        return 1

    # Authorization is asked first: a refusal that is about the mode must not be buried under a message
    # about the data source.
    refusal = autonomy_refusal(args.root, args.target) if args.apply else None
    if refusal:
        print(f"refusing --apply: {refusal}", file=sys.stderr)
        print(STOP_MARKER)
        return 1

    if args.apply and source == "fixture":
        print("refusing --apply with --issues-file: fixture numbers are not real issues, and their "
              "idempotency keys would shadow the real ones", file=sys.stderr)
        print(STOP_MARKER)
        return 1
    target_error = apply_target_error(profile) if (args.apply and args.target == "kanban") else None
    if target_error:
        print(f"refusing --apply: {target_error}", file=sys.stderr)
        print(STOP_MARKER)
        return 1

    if args.target == "backlog":
        existing = backlog_existing(args.root, args.profile) if args.apply else {}
    else:
        existing = queued_numbers(existing_tasks(), args.profile) if args.apply and source == "github" else {}
    report = {
        "marker": PASS_MARKER,
        "profile": args.profile,
        "repo": profile["repo"],
        "source": source,
        "mode": "apply" if args.apply else "dry-run",
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "evidence_bundle": [i["command"] for i in worker_brief.bundle_commands(profile)],
        "queued": [], "already_queued": [], "needs_triage": [], "skipped": [], "errors": [],
    }

    print(f"issue bridge — {profile['repo']} via {source} "
          f"({'APPLY' if args.apply else 'dry-run; pass --apply to queue'})")
    print(f"evidence bundle required of every worker: "
          f"{', '.join(i['command'] for i in worker_brief.bundle_commands(profile))}")
    print()

    printed = 0
    created_count = 0
    for issue in sorted(issues, key=lambda i: i["number"]):
        number, title = issue["number"], issue["title"]
        verdict, reason = classify(issue, profile)
        if verdict == "skip":
            report["skipped"].append({"issue": number, "title": title, "reason": reason})
            print(f"  #{number:<5} SKIP        {title[:60]} — {reason}")
            continue
        if verdict == "needs-triage":
            report["needs_triage"].append({"issue": number, "title": title, "reason": reason})
            print(f"  #{number:<5} NEEDS-TRIAGE {title[:60]} — {reason}")
            continue
        if number in existing:
            report["already_queued"].append({"issue": number, "task": existing[number]})
            print(f"  #{number:<5} QUEUED      {title[:60]} — task {existing[number]} (idempotent)")
            continue

        brief = worker_brief.render_brief(args.profile, profile, issue)
        key = issue_key(profile, number)
        assignee = assignee_for(issue, profile)
        estimate = (load_estimates(args.root).get(assignee) or {})
        cap = estimate.get("max_runtime")
        entry = {"issue": number, "title": title, "idempotency_key": key,
                 "assignee": assignee, "max_runtime": cap,
                 "estimate_basis": estimate.get("basis"),
                 "brief_chars": len(brief)}
        show_brief = args.print_brief == number or (args.print_brief is None and not args.apply
                                                   and not args.json and printed == 0)
        if show_brief:
            printed += 1
            print()
            print(brief)
            print()
        if args.apply and args.target == "kanban":
            cap_cards = autonomy_config(args.root)["max_cards"]
            if isinstance(cap_cards, int) and created_count >= cap_cards:
                print(f"  #{number:<5} DEFERRED    max_cards={cap_cards} reached: remaining issues wait for "
                      f"the next poll")
                report["skipped"].append({"issue": number, "reason": f"max_cards={cap_cards} reached"})
                continue
        if not args.apply:
            entry["planned_task_title"] = task_title(args.profile, issue)
            report["queued"].append(entry)
            print(f"  #{number:<5} WOULD-QUEUE {title[:60]} — key {key}")
            continue
        tmp = os.path.join(ROOT, "artifacts", "_bridge_body.md")
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(brief)
        try:
            created = (backlog_write(args.root, task_title(args.profile, issue), brief, profile, key,
                                     assignee=assignee, max_runtime=cap)
                       if args.target == "backlog" else
                       kanban_create(task_title(args.profile, issue), tmp, profile, key,
                                     assignee=assignee, max_runtime=cap))
        except RuntimeError as exc:
            report["errors"].append({"issue": number, "error": str(exc)})
            print(f"  #{number:<5} ERROR       {exc}")
            continue
        finally:
            os.remove(tmp)
        created_count += 1
        entry["task"] = created.get("id")
        entry["task_status"] = created.get("status")
        report["queued"].append(entry)
        print(f"  #{number:<5} QUEUED      {title[:60]} — task {entry['task']} "
              f"({entry['task_status']}, assignee={entry.get('assignee')}, cap={cap or 'none'})")

    ok = not report["errors"]
    report["marker"] = PASS_MARKER if ok else STOP_MARKER
    report["counts"] = {"queued": len(report["queued"]), "already_queued": len(report["already_queued"]),
                        "needs_triage": len(report["needs_triage"]), "skipped": len(report["skipped"]),
                        "errors": len(report["errors"])}
    report_path = args.report if os.path.isabs(args.report) else os.path.join(args.root, args.report)
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")

    counts = report["counts"]
    print()
    print(f"issues: {len(issues)} — queued {counts['queued']}, already queued {counts['already_queued']}, "
          f"needs triage {counts['needs_triage']}, skipped {counts['skipped']}, errors {counts['errors']}")
    print(f"report: {os.path.relpath(report_path, args.root)}")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    print(PASS_MARKER if ok else STOP_MARKER)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
