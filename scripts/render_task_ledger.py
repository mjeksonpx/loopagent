#!/usr/bin/env python3
"""Write a card's history into the repository, where a clone can read it.

The queue lives in a SQLite file on one machine; `artifacts/` and `handoff/` are gitignored because they
are evidence of a run. The consequence is that "what did we try, and why did it stop" disappears with the
laptop. This script renders one card to `backlog/<task-id>.md` — spec, the evidence bundle the worker
owed, how each run ended, and the review trail — so the memory survives `git clone`.

Execution stays where it was: the board still dispatches, the ledger only records.

    python3 scripts/render_task_ledger.py t_example
    python3 scripts/render_task_ledger.py --all-done
    python3 scripts/render_task_ledger.py t_example --dry-run

Verdict: TASK_LEDGER_PASS_0001 (exit 0) when every requested card was rendered;
TASK_LEDGER_STOP_0001 (exit 1) when a card is unknown or the ledger cannot be written.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASS_MARKER = "TASK_LEDGER_PASS_0001"
STOP_MARKER = "TASK_LEDGER_STOP_0001"
KANBAN_DB = os.path.expanduser("~/.hermes/kanban.db")
OUT_DIR = "backlog"

sys.path.insert(0, os.path.join(ROOT, "scripts"))
try:
    import worker_brief  # noqa: E402
except ImportError:  # pragma: no cover - the ledger must still render without the brief module
    worker_brief = None


def connect(db: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_task(conn: sqlite3.Connection, task_id: str) -> dict | None:
    row = conn.execute("select * from tasks where id = ?", (task_id,)).fetchone()
    return dict(row) if row else None


def fetch_runs(conn: sqlite3.Connection, task_id: str) -> list[dict]:
    rows = conn.execute("select * from task_runs where task_id = ? order by id", (task_id,)).fetchall()
    return [dict(r) for r in rows]


def fetch_comments(conn: sqlite3.Connection, task_id: str) -> list[dict]:
    rows = conn.execute("select author, body, created_at from task_comments where task_id = ? "
                        "order by id", (task_id,)).fetchall()
    return [dict(r) for r in rows]


def done_tasks(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("select id from tasks where status in ('done','blocked') order by rowid").fetchall()
    return [r["id"] for r in rows]


def bundle_for(profile_name: str | None) -> list[str]:
    """The commands this card's profile owed, with the source that requires each one."""
    if not profile_name or worker_brief is None:
        return []
    try:
        profile = worker_brief.get_profile(ROOT, profile_name)
        return [f"{item['command']} — {worker_brief_source(item)}"
                for item in worker_brief.bundle_commands(profile)]
    except (KeyError, SystemExit, OSError):
        return []


def worker_brief_source(item: dict) -> str:
    source = (item.get("source") or "").strip().splitlines()
    return source[0] if source else "profile"


def run_exit_code(metadata: str | None) -> str | None:
    if not metadata:
        return None
    try:
        data = json.loads(metadata)
    except (TypeError, ValueError):
        return None
    for key in ("command_exit_code", "exit_code"):
        if key in data and data[key] is not None:
            return str(data[key])
    return None


def iso(ts) -> str:
    if not ts:
        return "—"
    try:
        return datetime.datetime.fromtimestamp(float(ts)).astimezone().isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return str(ts)


def pr_links(comments: list[dict], runs: list[dict]) -> list[str]:
    found: list[str] = []
    for blob in [c.get("body") or "" for c in comments] + [r.get("summary") or "" for r in runs] + \
                [r.get("metadata") or "" for r in runs]:
        for token in blob.replace("(", " ").replace(")", " ").split():
            if "/pull/" in token or "/issues/" in token:
                token = token.strip(".,;\"'`")
                if token not in found:
                    found.append(token)
    return found


def spec_from_body(body: str) -> str:
    """The issue's own words, not the brief that quoted them.

    The bridge stores the rendered brief as the card body, so copying it here would duplicate the bundle
    and the constraints in every ledger file. The binding part is the issue text; the rest is regenerable
    with `scripts/worker_brief.py build`.
    """
    marker = "## Issue body (binding acceptance criteria)"
    if marker in body:
        tail = body.split(marker, 1)[1]
        stop = tail.find("\n## ")
        chunk = tail if stop == -1 else tail[:stop]
        quoted = [line[2:] if line.startswith("> ") else ("" if line.strip() == ">" else line)
                  for line in chunk.strip().splitlines()]
        text = "\n".join(quoted).strip()
        if text:
            return text
    goal = body.find("## Goal")
    if goal != -1 and "## " in body[goal + 3:]:
        tail = body[goal + len("## Goal"):]
        stop = tail.find("\n## ")
        text = (tail if stop == -1 else tail[:stop]).strip()
        if text:
            return text
    return body


def render(task: dict, runs: list[dict], comments: list[dict]) -> str:
    task_id = task["id"]
    profile = next((r.get("profile") for r in runs if r.get("profile")), None)
    lines: list[str] = []
    add = lines.append
    add(f"# {task.get('title') or task_id}")
    add("")
    add(f"- card: `{task_id}`")
    add(f"- assignee: `{task.get('assignee')}`")
    add(f"- status: **{task.get('status')}**")
    add(f"- created: {iso(task.get('created_at'))}")
    if task.get("completed_at"):
        add(f"- closed: {iso(task.get('completed_at'))}")
    if task.get("workspace_path"):
        add(f"- workspace: `{os.path.basename(str(task['workspace_path']))}`")
    if task.get("branch_name"):
        add(f"- branch: `{task['branch_name']}`")
    if task.get("idempotency_key"):
        add(f"- idempotency key: `{task['idempotency_key']}`")
    links = pr_links(comments, runs)
    for link in links:
        add(f"- link: {link}")
    add("")

    add("## Spec")
    add("")
    body = (task.get("body") or "").strip()
    if not body:
        add("_no body recorded on the card_")
    else:
        for raw in spec_from_body(body).splitlines():
            add(raw)
    add("")

    bundle = bundle_for(profile)
    add("## Evidence bundle the card owed")
    add("")
    if bundle:
        add(f"Profile `{profile}` requires all of these; a missing command is a REJECT at review.")
        add("")
        for item in bundle:
            add(f"- `{item}`")
    else:
        add("_no profile bundle recorded (the card was not dispatched with a repo profile)_")
    add("")

    add("## Runs")
    add("")
    if not runs:
        add("_no run recorded_")
    else:
        add("| run | status | outcome | started | ended | exit |")
        add("|---|---|---|---|---|---|")
        for run in runs:
            add(f"| {run.get('id')} | {run.get('status')} | {run.get('outcome') or '—'} | "
                f"{iso(run.get('started_at'))} | {iso(run.get('ended_at'))} | "
                f"{run_exit_code(run.get('metadata')) or '—'} |")
        add("")
        for run in runs:
            summary = (run.get("summary") or "").strip()
            error = (run.get("error") or "").strip()
            if summary or error:
                add(f"**run {run.get('id')}**")
                add("")
                for raw in (summary or error).splitlines():
                    add(f"> {raw}" if raw.strip() else ">")
                add("")

    add("## Review trail")
    add("")
    if not comments:
        add("_no comments_")
    else:
        for comment in comments:
            add(f"**{comment.get('author') or 'unknown'}** · {iso(comment.get('created_at'))}")
            add("")
            for raw in (comment.get("body") or "").splitlines():
                add(f"> {raw}" if raw.strip() else ">")
            add("")

    add("## Verdict")
    add("")
    last = runs[-1] if runs else None
    if not last:
        add("No run has executed this card yet: the ledger records the intent, not a result.")
    else:
        exit_code = run_exit_code(last.get("metadata"))
        add(f"Last run `{last.get('id')}` ended as `{last.get('status')}`"
            + (f" with exit `{exit_code}`" if exit_code is not None else "")
            + f" ({iso(last.get('ended_at'))}).")
        if task.get("last_failure_error"):
            add("")
            add(f"Last recorded failure: `{task['last_failure_error']}`")
    add("")
    # No render timestamp on purpose: a clock in the artefact makes every re-render a diff, and a ledger
    # that churns is a ledger nobody reads. The history lives in git, which already records when.
    add("_Rendered from the board by `scripts/render_task_ledger.py`. "
        "The board decides, the ledger remembers._")
    add("")
    return "\n".join(lines)


def write_ledger(root: str, task_id: str, content: str, dry_run: bool) -> tuple[str, bool]:
    out_dir = os.path.join(root, OUT_DIR)
    path = os.path.join(out_dir, f"{task_id}.md")
    previous = None
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            previous = fh.read()
    if previous == content:
        return path, False
    if not dry_run:
        os.makedirs(out_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
    return path, True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Render board cards into backlog/<task-id>.md.")
    ap.add_argument("task_ids", nargs="*", help="card ids, e.g. t_example")
    ap.add_argument("--all-done", action="store_true", help="every card that is done or blocked")
    ap.add_argument("--db", default=KANBAN_DB)
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if not os.path.exists(args.db):
        print(f"board not found: {args.db}", file=sys.stderr)
        print(STOP_MARKER)
        return 1
    conn = connect(args.db)
    ids = list(args.task_ids)
    if args.all_done:
        ids.extend(i for i in done_tasks(conn) if i not in ids)
    if not ids:
        print("nothing to render: pass card ids or --all-done", file=sys.stderr)
        print(STOP_MARKER)
        return 1

    failed = 0
    for task_id in ids:
        task = fetch_task(conn, task_id)
        if not task:
            print(f"  {task_id} [MISSING] no such card on the board", file=sys.stderr)
            failed += 1
            continue
        runs = fetch_runs(conn, task_id)
        comments = fetch_comments(conn, task_id)
        path, changed = write_ledger(args.root, task_id, render(task, runs, comments), args.dry_run)
        rel = os.path.relpath(path, args.root)
        state = "written" if changed else "unchanged"
        if args.dry_run:
            state = "would write" if changed else "unchanged"
        print(f"  {task_id} [{state}] {rel} — {len(runs)} run(s), {len(comments)} comment(s)")
    conn.close()
    if failed:
        print(f"cards not rendered: {failed}", file=sys.stderr)
        print(STOP_MARKER)
        return 1
    print(PASS_MARKER)
    return 0


if __name__ == "__main__":
    sys.exit(main())
