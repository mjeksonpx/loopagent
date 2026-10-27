#!/usr/bin/env python3
"""Visual checks — what an operator *sees*, not only what the JSON says.

Why a separate script: the product and technical gates read machine reports. A person reads a page. The
difference matters — a page can be technically correct and visually wrong (a FAIL row rendered green, a
verdict buried below the fold, an empty queue table that looks like a broken page). Those are user-facing
defects that no JSON assertion catches, and they are exactly what the `qa` role is for.

Mechanism: render the operator status page, serve it locally, capture a screenshot and the DOM with headless
Chrome, then judge the DOM against the scenario's required/forbidden text. The screenshot is kept as an
artefact for the vision review step (a human or the agent looks at it; a text assertion cannot see layout).

Requires Google Chrome (headless); fail-closed when it is absent — a visual check that silently skips is
worse than no visual check.

Usage
-----
    python3 scripts/check_visual.py --list
    python3 scripts/check_visual.py                     # capture + judge every scenario
    python3 scripts/check_visual.py --strict            # SKIP becomes FAIL
    python3 scripts/check_visual.py --scenario V01 --verbose
"""

from __future__ import annotations

import argparse
import datetime as dt
import functools
import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import render_status_page  # noqa: E402  (sibling script: the page under test)

PASS_MARKER = "VISUAL_CHECK_PASS_0001"
STOP_MARKER = "VISUAL_CHECK_STOP_0001"
SCENARIOS_REL = "configs/visual_scenarios.json"
SHOT_DIR_REL = "artifacts/visual_checks"
CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
]


def load_scenarios(root: str = ROOT) -> dict:
    with open(os.path.join(root, SCENARIOS_REL), encoding="utf-8") as fh:
        return json.load(fh)


def chrome_binary() -> str | None:
    for candidate in CHROME_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    return shutil.which("google-chrome") or shutil.which("chromium") or None


# ---------------------------------------------------------------- evaluation (pure)

def evaluate(scenario: dict, observation: dict) -> list[str]:
    """Reasons the observed page violates the scenario; empty list = the page passes.

    Pure so the technical gate can attack it (D13) with falsified DOM snapshots.
    """
    exp = scenario.get("expect") or {}
    dom = observation.get("dom") or ""
    reasons: list[str] = []
    for needle in exp.get("require_text") or []:
        if needle not in dom:
            reasons.append(f"a user would not see {needle!r} on the page")
    for needle in exp.get("forbid_text") or []:
        if needle in dom:
            reasons.append(f"the page shows {needle!r}, which must not be visible to a user")
    if exp.get("visible_cell_truncation"):
        import re as _re
        width = int(exp.get("cell_width", 70))
        for cell in _re.findall(r"<td[^>]*>([^<>]+)</td>", dom):
            text = cell.strip()
            # The renderer clips long cells to `width` characters, ending with an ellipsis. A cell at or over
            # that width without the ellipsis is text cut mid-word — visible to a user, invisible to a JSON check.
            if len(text) >= width and not text.endswith("…"):
                reasons.append(f"a table cell is cut mid-word without an ellipsis: {text[-28:]!r}")
    shot = observation.get("screenshot")
    if exp.get("screenshot_required") and not (shot and os.path.exists(shot)):
        reasons.append("no screenshot was captured, so nobody can look at the page")
    if exp.get("min_bytes") and observation.get("screenshot_bytes", 0) < exp["min_bytes"]:
        reasons.append(f"the screenshot is {observation.get('screenshot_bytes', 0)} bytes "
                       f"(< {exp['min_bytes']}), so the page most likely rendered blank")
    return reasons


# ---------------------------------------------------------------- capture

class _Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # keep the check output readable
        pass


def serve(directory: str) -> tuple[http.server.ThreadingHTTPServer, int]:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    handler = functools.partial(_Handler, directory=directory)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, port


def capture(chrome: str, url: str, shot_path: str, width: int = 1280, height: int = 1400) -> tuple[str, int]:
    """Return (dom, screenshot bytes). Both come from one headless Chrome run each."""
    os.makedirs(os.path.dirname(shot_path), exist_ok=True)
    shot = subprocess.run([chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                           f"--window-size={width},{height}", f"--screenshot={shot_path}", url],
                          capture_output=True, text=True, timeout=60)
    if shot.returncode != 0 or not os.path.exists(shot_path):
        return "", 0
    dom_proc = subprocess.run([chrome, "--headless=new", "--disable-gpu", "--dump-dom", url],
                              capture_output=True, text=True, timeout=60)
    dom = dom_proc.stdout if dom_proc.returncode == 0 else ""
    return dom, os.path.getsize(shot_path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Visual checks over the operator status page.")
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--scenario", action="append")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        config = load_scenarios(args.root)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        print(STOP_MARKER)
        return 2

    scenarios = config["scenarios"]
    if args.list:
        for sc in scenarios:
            flag = "optional" if sc.get("optional") else "mandatory"
            print(f"  {sc['id']}  [{flag}] {sc['page']:<22} {sc['title']}")
        return 0
    if args.scenario:
        wanted = set(args.scenario)
        scenarios = [s for s in scenarios if s["id"] in wanted] or []

    chrome = chrome_binary()
    print("VISUAL CHECK — scripts/check_visual.py")
    print(f"page: {config['page']}   chrome: {chrome or '(not found)'}")
    print()
    if chrome is None:
        print("[FAIL] no headless Chrome/Chromium on this machine: a visual check that silently skips is "
              "worse than none")
        print(STOP_MARKER)
        return 1

    render_status_page.main(["--root", args.root])
    server, port = serve(os.path.join(args.root, "artifacts"))
    results, failures, skips = [], [], []
    try:
        for sc in scenarios:
            shot = os.path.join(args.root, SHOT_DIR_REL, f"{sc['id']}_{sc['page'].replace('/', '_')}.png")
            dom, size = capture(chrome, f"http://127.0.0.1:{port}/{sc['page']}", shot)
            observation = {"dom": dom, "screenshot": shot, "screenshot_bytes": size}
            if not dom:
                observation["skip"] = "the page did not render (Chrome returned no DOM)"
            reasons = evaluate(sc, observation)
            status = "SKIP" if observation.get("skip") else ("OK" if not reasons else "FAIL")
            if status == "SKIP" and args.strict:
                status, reasons = "FAIL", [f"prerequisite missing: {observation['skip']}"]
            results.append({"id": sc["id"], "title": sc["title"], "page": sc["page"], "status": status,
                            "reasons": reasons, "screenshot": os.path.relpath(shot, args.root),
                            "screenshot_bytes": size})
            print(f"{sc['id']} [{status}] {sc['title']}")
            if observation.get("skip"):
                print(f"       - SKIP: {observation['skip']}")
            for reason in reasons:
                print(f"       - {reason}")
            if args.verbose and size:
                print(f"       - screenshot {os.path.relpath(shot, args.root)} ({size} bytes)")
            if status == "FAIL":
                failures.append(sc["id"])
            if status == "SKIP":
                skips.append(sc["id"])
    finally:
        server.shutdown()

    ok = not failures
    print()
    print(f"visual scenarios: {len(results) - len(failures) - len(skips)} ok, {len(failures)} fail, "
          f"{len(skips)} skip")
    report = {"marker": PASS_MARKER if ok else STOP_MARKER, "page": config["page"],
              "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
              "scenarios": results, "failures": failures, "skips": skips}
    report_path = os.path.join(args.root, config.get("report", "artifacts/visual_check_report.json"))
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"report: {os.path.relpath(report_path, args.root)}")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    if ok:
        print(PASS_MARKER)
        return 0
    print(STOP_MARKER)
    return 1


if __name__ == "__main__":
    sys.exit(main())
