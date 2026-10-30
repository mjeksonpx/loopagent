#!/usr/bin/env python3
"""Stop a card that costs more than it is worth.

A loop without a budget is not a loop: it is a process that can spend a five-hour subscription on one
stuck card and still hand back nothing. The dispatcher already enforces a wall-clock cap per run
(`--max-runtime`); this script covers the two things a clock cannot see — money spent (tokens) and
progress made (new commits in the run's workspace).

    python3 scripts/guard_card.py                                  # report only, database source
    python3 scripts/guard_card.py --json
    python3 scripts/guard_card.py --enforce                        # SIGTERM + block + comment
    python3 scripts/guard_card.py --snapshot queue.json            # no database, no runtime
    python3 scripts/guard_card.py --snapshot queue.json --json
    python3 scripts/guard_card.py --selftest                       # prove every rule on fixtures

Verdict: CARD_BUDGET_STOP_0001 (exit 1) names every offending card and the rule it broke;
CARD_BUDGET_PASS_0001 (exit 0) means every active run is inside its budget.

Sources of truth (exactly one of them per run)
----------------------------------------------
Database source, both read-only here, and the default because existing users have it:
  ~/.hermes/kanban.db  — tasks (assignee, status) and task_runs (worker_pid, started_at, metadata)
  ~/.hermes/state.db   — session_model_usage keyed by the worker session recorded in run metadata

Snapshot source (`--snapshot`): a JSON document produced by whoever owns the queue, so the budget rule
works without one particular agent runtime and can be tested in CI. Shape, kept minimal on purpose:

    {"runs": [{"task_id": "t_1", "run_id": 1, "assignee": "dev", "status": "running",
               "started_at": 1792512986, "worker_pid": null, "workspace": "/path/to/worktree",
               "session_tokens": {"input": 180000, "output": 12000, "cache_read": 90000,
                                  "cache_write": 0, "api_calls": 40}}]}

`task_id`, `run_id`, `status`, `started_at` and `session_tokens` are required; `assignee`, `worker_pid`,
`workspace` and a card `title` are optional. `max_runs` is counted from the snapshot's own entries: a
snapshot knows the runs it was exported with, and nothing else.

Limits of the measurement, stated so nobody over-reads the number
-----------------------------------------------------------------
* Tokens are counted for the worker session only (not the reviewer's). In the database source a session
  that is absent from the usage table is a measurement limit and reports `unknown`; in a snapshot the
  token block is required input, so a missing key is a *data* defect and the card cannot be certified —
  `unknown` is never rendered as `0` and never counts as a pass. A rule that cannot see the spend must
  not certify it.
* A run whose workspace is not a git repository reports progress as `unknown` rather than "none", so a
  missing workspace never manufactures a "no progress" stop.
* The budget values are a starting point to be re-measured from finished runs
  (`scripts/estimate_task.py` does the same for time).
* `--enforce` against a snapshot can only signal a live `worker_pid`: there is no queue CLI to comment or
  block on, so the board is deliberately left alone and the action list says so. Signaling is refused
  (`EPERM`/`ESRCH`) rather than escalated.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASS_MARKER = "CARD_BUDGET_PASS_0001"
STOP_MARKER = "CARD_BUDGET_STOP_0001"
SELFTEST_MARKER = "CARD_BUDGET_SELFTEST_PASS_0001"
DEFAULT_CONFIG = os.path.join(ROOT, "configs", "card_budget.json")
KANBAN_DB = os.path.expanduser("~/.hermes/kanban.db")
STATE_DB = os.path.expanduser("~/.hermes/state.db")
ACTIVE_STATUSES = ("running", "claimed", "in_progress", "review")
SNAPSHOT_REQUIRED = ("task_id", "run_id", "status", "started_at", "session_tokens")
TOKEN_KEYS = (("input", "input"), ("output", "output"), ("cache_read", "cache_read"),
              ("cache_write", "cache_write"), ("api_calls", "api_calls"))


class SnapshotError(ValueError):
    """The snapshot cannot be read as a queue: named, because a silent empty queue looks like a pass."""


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


def snapshot_tokens(block) -> dict | None:
    """Token block from a snapshot entry; None when it is absent or unusable (never a silent zero)."""
    if not isinstance(block, dict):
        return None
    numbers = {}
    for name, key in TOKEN_KEYS:
        value = block.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        numbers[name] = value
    numbers["total"] = numbers["input"] + numbers["output"] + numbers["cache_read"] + numbers["cache_write"]
    return numbers


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


def snapshot_runs(path: str) -> list[dict]:
    """Read a queue snapshot into the same row shape `active_runs` returns.

    Required keys are validated here rather than defaulted: an entry missing `started_at` or
    `session_tokens` is reported to the caller (through `_missing`) so the card is refused, not measured
    as zero. Shape errors abort with a named reason — "no active runs" and "I could not read the queue"
    must never look the same.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except OSError as exc:
        raise SnapshotError(f"{path}: {exc.__class__.__name__}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SnapshotError(f"{path}: not JSON ({exc})") from exc
    if isinstance(payload, dict):
        entries = payload.get("runs")
        if not isinstance(entries, list):
            raise SnapshotError(f"{path}: expected an object with a 'runs' list")
    elif isinstance(payload, list):
        entries = payload
    else:
        raise SnapshotError(f"{path}: expected an object with a 'runs' list or a bare list of runs")
    runs: list[dict] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise SnapshotError(f"{path}: runs[{index}] is not an object")
        missing = [key for key in SNAPSHOT_REQUIRED if entry.get(key) in (None, "")]
        tokens = snapshot_tokens(entry.get("session_tokens"))
        if "session_tokens" not in missing and tokens is None:
            missing.append("session_tokens")
        runs.append({
            "run_id": entry.get("run_id"),
            "task_id": entry.get("task_id"),
            "status": entry.get("status"),
            "worker_pid": entry.get("worker_pid"),
            "started_at": entry.get("started_at"),
            "last_heartbeat_at": entry.get("last_heartbeat_at"),
            "metadata": None,
            "max_runtime_seconds": entry.get("max_runtime_seconds"),
            "title": entry.get("title") or entry.get("task_title") or "",
            "assignee": entry.get("assignee"),
            "workspace_path": entry.get("workspace") or entry.get("workspace_path"),
            "priority": entry.get("priority"),
            "_origin": "snapshot",
            "_tokens": tokens,
            "_missing": missing,
        })
    return runs


def run_count(db: str, task_id: str) -> int:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    total = conn.execute("select count(*) from task_runs where task_id = ?", (task_id,)).fetchone()[0]
    conn.close()
    return total


def evaluate(run: dict, cfg: dict, state_db: str, db: str, now: float,
             runs_used: int | None = None) -> dict:
    budget = budget_for(cfg, run.get("assignee"))
    started = run.get("started_at")
    runtime_min = (now - started) / 60.0 if started else None
    origin = run.get("_origin") or "database"
    if origin == "snapshot":
        tokens = run.get("_tokens")
    else:
        tokens = session_tokens(state_db, worker_session(run.get("metadata")))
    commits = commits_since(run.get("workspace_path"), started)
    if runs_used is None:
        runs_used = run_count(db, run["task_id"])

    offences: list[str] = []
    unverifiable: list[str] = []
    for key in run.get("_missing") or []:
        if key == "session_tokens":
            # Input the exporter was required to provide: unknown is not an excuse, it is a missing fact.
            unverifiable.append(
                f"spent an unknown number of tokens (the snapshot entry has no usable `session_tokens`), "
                f"so the {budget['max_tokens']:,} token cap cannot be certified")
        elif key == "started_at":
            unverifiable.append("no `started_at` in the snapshot entry, so wall clock and the progress "
                                "clock cannot be judged")
        else:
            unverifiable.append(f"the snapshot entry is missing `{key}`, so the run cannot be judged")
    offences.extend(unverifiable)
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
        "origin": origin,
        "runtime_minutes": None if runtime_min is None else round(runtime_min, 1),
        "runs_used": runs_used,
        "tokens": tokens["total"] if tokens else None,
        "token_split": tokens,
        "commits": commits,
        "budget": budget,
        "unverifiable": unverifiable,
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
    if card.get("origin") == "snapshot":
        actions.append("snapshot source: the queue itself was not touched (no block, no comment) — "
                       "a snapshot has no board to write to; update the exporter's queue instead")
        return actions
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


def find_hermes() -> str | None:
    for candidate in (os.environ.get("HERMES_BIN"), os.path.expanduser("~/.local/bin/hermes"),
                      "/opt/homebrew/bin/hermes"):
        if candidate and os.path.exists(candidate):
            return candidate
    from shutil import which
    return which("hermes")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Stop cards that exceed their token/run/progress budget.")
    ap.add_argument("--db", default=KANBAN_DB)
    ap.add_argument("--state-db", default=STATE_DB)
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--snapshot", help="read the queue from a JSON snapshot instead of the kanban "
                                      "database (no runtime, no ~/.hermes)")
    ap.add_argument("--enforce", action="store_true", help="SIGTERM the worker and block the card")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true", help="prove every budget rule on fixtures")
    args = ap.parse_args(argv)

    if args.selftest:
        rc = selftest()
        print(SELFTEST_MARKER if rc == 0 else STOP_MARKER)
        return rc

    cfg = load_config(args.config)
    if args.snapshot:
        try:
            runs = snapshot_runs(args.snapshot)
        except SnapshotError as exc:
            print(f"snapshot error: {exc}", file=sys.stderr)
            print(STOP_MARKER)
            return 1
    else:
        runs = active_runs(args.db)
    now = time.time()
    per_task: dict[str, int] = {}
    for run in runs:
        per_task[run["task_id"]] = per_task.get(run["task_id"], 0) + 1
    cards = [evaluate(run, cfg, args.state_db, args.db, now,
                      runs_used=per_task.get(run["task_id"]) if args.snapshot else None)
             for run in runs]
    offenders = [c for c in cards if c["offences"]]

    actions: dict[str, list[str]] = {}
    if args.enforce:
        hermes = None if args.snapshot else find_hermes()
        for card in offenders:
            actions[card["task_id"]] = enforce(card, args.db, hermes)

    if args.json:
        print(json.dumps({"marker": STOP_MARKER if offenders else PASS_MARKER,
                          "source": "snapshot" if args.snapshot else "database",
                          "budgets": cfg["default"], "cards": cards, "actions": actions},
                         ensure_ascii=False, indent=2))
    else:
        if not cards:
            print("no active runs: nothing is spending")
        if args.snapshot and cards:
            print(f"source: snapshot {args.snapshot}")
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


# ---------------------------------------------------------------- selftest

def _run(argv: list[str]) -> tuple[int, str]:
    """Run one verdict in-process, capturing both streams: a refusal explains itself on stderr."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue() + err.getvalue()


def selftest() -> int:
    """Prove each rule on fixtures — the snapshot path exists so this can run in CI, so prove it there."""
    ok = True
    tmp = tempfile.mkdtemp(prefix="guard_card_selftest_")
    now = int(time.time())
    cfg_path = os.path.join(tmp, "card_budget.json")
    with open(cfg_path, "w", encoding="utf-8") as fh:
        json.dump({"default": {"max_tokens": 100_000, "max_runs": 2, "max_runtime_minutes": 30,
                               "no_progress_minutes": 20}}, fh)
    tokens_ok = {"input": 1000, "output": 100, "cache_read": 0, "cache_write": 0, "api_calls": 3}
    tokens_over = {"input": 200_000, "output": 10_000, "cache_read": 0, "cache_write": 0, "api_calls": 40}

    def snapshot(name: str, runs: list[dict]) -> str:
        path = os.path.join(tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"runs": runs}, fh)
        return path

    def check(label: str, code: int, out: str, want_code: int, want: str, forbid: str | None = None) -> None:
        nonlocal ok
        good = code == want_code and want in out and (forbid is None or forbid not in out)
        print(f"[{'ok  ' if good else 'FAIL'}] {label}")
        if not good:
            ok = False
            print(f"        exit={code} (want {want_code}); marker/needle {want!r} "
                  f"{'found' if want in out else 'MISSING'}"
                  + (f"; forbidden {forbid!r} {'PRESENT' if forbid in out else 'absent'}" if forbid else ""))
            print("        output: " + out.strip().replace("\n", " | ")[:400])

    base = {"task_id": "t_1", "run_id": 1, "assignee": "dev", "status": "running",
            "started_at": now - 60, "worker_pid": None, "workspace": None}

    inside = snapshot("inside.json", [dict(base, session_tokens=tokens_ok)])
    code, out = _run(["--snapshot", inside, "--config", cfg_path])
    check("inside budget -> PASS", code, out, 0, PASS_MARKER)

    over = snapshot("over.json", [dict(base, session_tokens=tokens_over)])
    code, out = _run(["--snapshot", over, "--config", cfg_path])
    check("tokens over cap -> STOP naming the card and the rule", code, out, 1, STOP_MARKER)
    check("tokens over cap names the token rule", code, out, 1, "cap 100,000")

    missing = {k: v for k, v in base.items()}
    no_tokens = snapshot("no_tokens.json", [dict(missing)])
    code, out = _run(["--snapshot", no_tokens, "--config", cfg_path])
    check("missing session_tokens -> STOP, prints unknown", code, out, 1, "unknown tokens", "0 tokens")

    thirty = now - 40 * 60
    clock = snapshot("clock.json", [dict(base, started_at=thirty, session_tokens=tokens_ok)])
    code, out = _run(["--snapshot", clock, "--config", cfg_path])
    check("wall clock over cap -> STOP", code, out, 1, "min of wall clock (cap 30)")

    many = snapshot("many.json", [dict(base, run_id=i, session_tokens=tokens_ok) for i in range(1, 4)])
    code, out = _run(["--snapshot", many, "--config", cfg_path])
    check("max_runs over cap -> STOP", code, out, 1, "runs on one card (cap 2)")

    # A real repository with no commit after the run started: progress is measured, not assumed.
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    # The commit has to be dated before the run starts, otherwise it counts as progress made by the run.
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now - 3600))
    env = {**os.environ, "GIT_AUTHOR_NAME": "selftest", "GIT_AUTHOR_EMAIL": "selftest@example.org",
           "GIT_COMMITTER_NAME": "selftest", "GIT_COMMITTER_EMAIL": "selftest@example.org",
           "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
    git = ["git", "-C", repo]
    made = True
    for cmd in ([*git, "init", "-q"], [*git, "commit", "-q", "--allow-empty", "-m", "before"]):
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
        made = made and proc.returncode == 0
    if made:
        stale = snapshot("stale.json", [dict(base, started_at=now - 25 * 60, workspace=repo,
                                             session_tokens=tokens_ok)])
        code, out = _run(["--snapshot", stale, "--config", cfg_path])
        check("no commit past the progress clock -> STOP", code, out, 1, "no commit in")
    else:
        print("[skip] no-commit rule: `git` unavailable, the rule cannot be exercised here")
        ok = False

    # The enforce path must not pretend to own a board it cannot see.
    code, out = _run(["--snapshot", over, "--config", cfg_path, "--enforce"])
    check("--enforce on a snapshot touches no queue", code, out, 1, "queue itself was not touched")

    unreadable = os.path.join(tmp, "broken.json")
    with open(unreadable, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    code, out = _run(["--snapshot", unreadable, "--config", cfg_path])
    check("unreadable snapshot -> STOP naming the file", code, out, 1, "snapshot error")

    empty_db = os.path.join(tmp, "missing.db")
    code, out = _run(["--db", empty_db, "--state-db", empty_db, "--config", cfg_path])
    check("no database present -> PASS (nothing is spending)", code, out, 0, PASS_MARKER)

    print(f"selftest: {'all rules fired' if ok else 'a rule did not fire'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
