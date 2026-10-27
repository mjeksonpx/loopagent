#!/usr/bin/env python3
"""Stop a card that costs more than it is worth.

A loop without a budget is not a loop: it is a process that can spend a five-hour subscription on one
stuck card and still hand back nothing. The dispatcher already enforces a wall-clock cap per run
(`--max-runtime`); this script covers the two things a clock cannot see — money spent (tokens) and
progress made (new commits in the run's workspace).

    python3 scripts/guard_card.py                    # report only
    python3 scripts/guard_card.py --json
    python3 scripts/guard_card.py --enforce          # SIGTERM + block + comment

Verdict: CARD_BUDGET_STOP_0001 (exit 1) names every offending card and the rule it broke;
CARD_BUDGET_PASS_0001 (exit 0) means every active run is inside its budget.

Sources of truth, both read-only here:
  ~/.hermes/kanban.db  — tasks (assignee, status) and task_runs (worker_pid, started_at, metadata)
  ~/.hermes/state.db   — session_model_usage keyed by the worker session recorded in run metadata

Limits of the measurement, stated so nobody over-reads the number: tokens are counted for the worker
session only (not the reviewer's), a run whose workspace is not a git repository reports progress as
unknown rather than "none", and the budget values are a starting point to be re-measured from finished
runs (`scripts/estimate_task.py` does the same for time).
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASS_MARKER = "CARD_BUDGET_PASS_0001"
STOP_MARKER = "CARD_BUDGET_STOP_0001"
DEFAULT_CONFIG = os.path.join(ROOT, "configs", "card_budget.json")
KANBAN_DB = os.path.expanduser("~/.hermes/kanban.db")
STATE_DB = os.path.expanduser("~/.hermes/state.db")
ACTIVE_STATUSES = ("running", "claimed", "in_progress", "review")


def load_config(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        raw = {}
    base = {"max_tokens": 250_000, "max_runs": 3, "max_runtime_minutes": 30, "no_progress_minutes": 20}
    base.update(raw.get("default") or {})
    return {"default": base, "profiles": raw.get("profiles") or {}}


def budget_for(cfg: dict, assignee: str | None) -> dict:
    merged = dict(cfg["default"])
    merged.update(cfg["profiles"].get(assignee or "", {}) or {})
    return merged


def worker_session(metadata: str | None) -> str | None:
    if not metadata:
        return None
    try:
        data = json.loads(metadata)
    except (TypeError, ValueError):
        return None
    for key in ("worker_session_id", "session_id"):
        value = data.get(key)
        if value:
            return str(value)
    return None


def session_tokens(state_db: str, session_id: str | None) -> dict | None:
    """Tokens billed to this worker session, or None when the session is not in the usage table."""
    if not session_id or not os.path.exists(state_db):
        return None
    try:
        conn = sqlite3.connect(f"file:{state_db}?mode=ro", uri=True)
        rows = conn.execute(
            "select coalesce(sum(input_tokens),0), coalesce(sum(output_tokens),0), "
            "coalesce(sum(cache_read_tokens),0), coalesce(sum(cache_write_tokens),0), "
            "coalesce(sum(api_call_count),0) from session_model_usage where session_id = ?",
            (session_id,),
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        return None
    input_t, output_t, read_t, write_t, calls = rows[0]
    if not calls:
        return None
    return {"input": input_t, "output": output_t, "cache_read": read_t, "cache_write": write_t,
            "api_calls": calls, "total": input_t + output_t + read_t + write_t}


def commits_since(workspace: str | None, started_at: float | None) -> int | None:
    """Commits made in the run's workspace after it started; None when that cannot be known."""
    if not workspace or not started_at or not os.path.isdir(workspace):
        return None
    if not os.path.isdir(os.path.join(workspace, ".git")) and not os.path.exists(os.path.join(workspace, ".git")):
        return None
    try:
        proc = subprocess.run(["git", "-C", workspace, "log", f"--since=@{int(started_at)}", "--oneline"],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return len([line for line in proc.stdout.splitlines() if line.strip()])


def active_runs(db: str) -> list[dict]:
    if not os.path.exists(db):
        return []
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
    rows = conn.execute(
        f"""select r.id as run_id, r.task_id, r.status, r.worker_pid, r.started_at, r.last_heartbeat_at,
                   r.metadata, r.max_runtime_seconds,
                   t.title, t.assignee, t.workspace_path, t.priority
            from task_runs r join tasks t on t.id = r.task_id
            where r.ended_at is null and r.status in ({placeholders})
            order by r.started_at""",
        ACTIVE_STATUSES,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def run_count(db: str, task_id: str) -> int:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    total = conn.execute("select count(*) from task_runs where task_id = ?", (task_id,)).fetchone()[0]
    conn.close()
    return total


def evaluate(run: dict, cfg: dict, state_db: str, db: str, now: float) -> dict:
    budget = budget_for(cfg, run.get("assignee"))
    started = run.get("started_at")
    runtime_min = (now - started) / 60.0 if started else None
    tokens = session_tokens(state_db, worker_session(run.get("metadata")))
    commits = commits_since(run.get("workspace_path"), started)
    runs_used = run_count(db, run["task_id"])

    offences: list[str] = []
    if tokens and tokens["total"] > budget["max_tokens"]:
        offences.append(f"spent {tokens['total']:,} tokens (cap {budget['max_tokens']:,})")
    if runs_used > budget["max_runs"]:
        offences.append(f"{runs_used} runs on one card (cap {budget['max_runs']})")
    if runtime_min is not None and runtime_min > budget["max_runtime_minutes"]:
        offences.append(f"{runtime_min:.0f} min of wall clock (cap {budget['max_runtime_minutes']})")
    if (runtime_min is not None and runtime_min > budget["no_progress_minutes"]
            and commits == 0):
        offences.append(f"no commit in {runtime_min:.0f} min (progress clock {budget['no_progress_minutes']} min)")

    return {
        "task_id": run["task_id"],
        "run_id": run["run_id"],
        "assignee": run.get("assignee"),
        "title": (run.get("title") or "")[:70],
        "worker_pid": run.get("worker_pid"),
        "runtime_minutes": None if runtime_min is None else round(runtime_min, 1),
        "runs_used": runs_used,
        "tokens": tokens["total"] if tokens else None,
        "token_split": tokens,
        "commits": commits,
        "budget": budget,
        "offences": offences,
    }


def enforce(card: dict, db: str, hermes: str | None) -> list[str]:
    """Stop the worker, then let the board know through its own CLI (never by hand-writing the DB)."""
    actions: list[str] = []
    pid = card.get("worker_pid")
    if pid:
        try:
            os.kill(int(pid), signal.SIGTERM)
            actions.append(f"SIGTERM -> pid {pid}")
        except ProcessLookupError:
            actions.append(f"pid {pid} already gone")
        except OSError as exc:
            actions.append(f"SIGTERM failed for pid {pid}: {exc}")
    else:
        actions.append("no worker_pid recorded (the run was never started locally)")
    reason = "; ".join(card["offences"]) or "budget exceeded"
    if hermes:
        for args in (["kanban", "comment", card["task_id"], "--author", "guard_card",
                      f"Stopped by guard_card.py: {reason}"],
                     ["kanban", "block", card["task_id"], "--reason", f"card budget: {reason}"]):
            try:
                proc = subprocess.run([hermes, *args], capture_output=True, text=True, timeout=60)
                actions.append(("ok: " if proc.returncode == 0 else "failed: ") + " ".join(args[:3])
                               + ("" if proc.returncode == 0 else f" — {proc.stderr.strip()[:120]}"))
            except (OSError, subprocess.SubprocessError) as exc:
                actions.append(f"failed: {' '.join(args[:3])} — {exc}")
    else:
        actions.append("`hermes` not on PATH: the board was not updated, only the process was stopped")
    return actions


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Stop cards that exceed their token/run/progress budget.")
    ap.add_argument("--db", default=KANBAN_DB)
    ap.add_argument("--state-db", default=STATE_DB)
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--enforce", action="store_true", help="SIGTERM the worker and block the card")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    runs = active_runs(args.db)
    now = time.time()
    cards = [evaluate(run, cfg, args.state_db, args.db, now) for run in runs]
    offenders = [c for c in cards if c["offences"]]

    actions: dict[str, list[str]] = {}
    if args.enforce:
        hermes = None
        for candidate in (os.environ.get("HERMES_BIN"), os.path.expanduser("~/.local/bin/hermes"),
                          "/opt/homebrew/bin/hermes"):
            if candidate and os.path.exists(candidate):
                hermes = candidate
                break
        if not hermes:
            from shutil import which
            hermes = which("hermes")
        for card in offenders:
            actions[card["task_id"]] = enforce(card, args.db, hermes)

    if args.json:
        print(json.dumps({"marker": STOP_MARKER if offenders else PASS_MARKER,
                          "budgets": cfg["default"], "cards": cards, "actions": actions},
                         ensure_ascii=False, indent=2))
    else:
        if not cards:
            print("no active runs: nothing is spending")
        for card in cards:
            state = "OVER" if card["offences"] else "ok"
            tokens = "unknown" if card["tokens"] is None else f"{card['tokens']:,}"
            commits = "unknown" if card["commits"] is None else str(card["commits"])
            print(f"  {card['task_id']} [{state}] {card['assignee']} · {card['runtime_minutes']} min · "
                  f"{tokens} tokens · {commits} commit(s) · {card['title']}")
            for offence in card["offences"]:
                print(f"      - {offence}")
            for action in actions.get(card["task_id"], []):
                print(f"      > {action}")
        if offenders:
            print(f"cards over budget: {len(offenders)} of {len(cards)} active")
    print(STOP_MARKER if offenders else PASS_MARKER)
    return 1 if offenders else 0


if __name__ == "__main__":
    sys.exit(main())
