#!/usr/bin/env python3
"""Operator status page — the human-readable surface the qa role screenshots and reviews.

Why: the loop produces two verdicts (technical gate, product acceptance) plus a queue, and none of them is
readable by a person without running commands. This renders them into one page so that "what would an
operator see right now?" becomes a thing you can look at — and, more importantly, a thing that can be
checked *visually* (screenshot + vision) instead of only by grepping JSON.

The page is deliberately static and local: no CDN, no JavaScript, no fonts to fetch, so a screenshot of it
is reproducible offline.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_REL = "artifacts/status.html"


def load(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def queue_rows() -> list[dict]:
    # `hermes` is the optional execution backend: a clone without it must still render the page (both
    # verdicts come from files), so a missing binary degrades to an empty queue instead of a traceback.
    try:
        proc = subprocess.run(["hermes", "kanban", "list", "--json"], capture_output=True, text=True)
    except OSError:
        return []
    if proc.returncode != 0:
        return []
    try:
        tasks = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return []
    return [{"id": t.get("id"), "status": t.get("status"), "assignee": t.get("assignee"),
             "title": clip(t.get("title") or "")} for t in tasks]


CELL_WIDTH = 70


def clip(text: str, width: int = CELL_WIDTH) -> str:
    """Cut a table cell visibly: a reader must be able to see that something was cut.

    The vision review of the first version of this page caught a title cut mid-word with no ellipsis
    ("...proving nothin"). Every long cell goes through this, and the visual gate asserts the rule.
    """
    text = str(text)
    return text if len(text) <= width else text[:width - 1].rstrip() + "…"


def render(root: str = ROOT) -> str:
    gate = load(os.path.join(root, "artifacts", "loop_gate_report.json")) or {}
    product = load(os.path.join(root, "artifacts", "product_acceptance_report.json")) or {}
    state = load(os.path.join(root, "artifacts", "loop_state.json")) or {}
    witness = load(os.path.join(root, "artifacts", "anchor_witness.json")) or {}

    dims = gate.get("dimensions") or []
    gate_ok = gate.get("marker") == "LOOP_ENGINEERING_PASS_0001"
    product_scenarios = product.get("scenarios") or []
    product_ok = product.get("marker") == "PRODUCT_ACCEPTANCE_PASS_0001"
    rows = queue_rows()

    def esc(value) -> str:
        return html.escape(str(value))

    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        "<title>loop-system status</title>",
        "<style>",
        "body{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;margin:2rem;color:#111}",
        "h1{font-size:1.3rem}h2{font-size:1rem;margin-top:1.6rem}",
        "table{border-collapse:collapse;margin-top:.4rem}td,th{border:1px solid #ccc;padding:.25rem .6rem;"
        "text-align:left;font-size:.85rem}",
        ".verdict{font-weight:700}",
        ".ok{color:#0a7f2e}.fail{color:#b00020}.skip{color:#8a6d00}",
        "</style></head><body>",
        "<h1>loop-system status</h1>",
        f"<p>generated {esc(dt.datetime.now().astimezone().isoformat(timespec='seconds'))}</p>",
        "<h2>Verdicts</h2>",
        "<table><tr><th>gate</th><th>verdict</th><th>marker</th></tr>",
        f"<tr><td>technical gate (loop discipline)</td>"
        f"<td class='verdict {'ok' if gate_ok else 'fail'}'>"
        f"{'accepted' if gate_ok else 'not accepted'}</td><td>{esc(gate.get('marker', 'no report'))}</td></tr>",
        f"<tr><td>product acceptance (user-facing)</td>"
        f"<td class='verdict {'ok' if product_ok else 'fail'}'>"
        f"{'product accepted' if product_ok else 'product not accepted'}</td>"
        f"<td>{esc(product.get('marker', 'no report'))}</td></tr>",
        "</table>",
        "<h2>Technical gate dimensions</h2>",
        "<table><tr><th>id</th><th>dimension</th><th>status</th></tr>",
    ]
    for dim in dims:
        status = str(dim.get("status", "?"))
        css = "ok" if status == "OK" else ("skip" if status == "SKIP" else "fail")
        parts.append(f"<tr><td>{esc(dim.get('id'))}</td><td>{esc(clip(dim.get('name')))}</td>"
                     f"<td class='{css}'>{esc(status)}</td></tr>")
    parts += ["</table>", "<h2>Product scenarios</h2>",
              "<table><tr><th>id</th><th>scenario</th><th>status</th></tr>"]
    for sc in product_scenarios:
        status = str(sc.get("status", "?"))
        css = "ok" if status == "OK" else ("skip" if status == "SKIP" else "fail")
        parts.append(f"<tr><td>{esc(sc.get('id'))}</td><td>{esc(clip(sc.get('title')))}</td>"
                     f"<td class='{css}'>{esc(status)}</td></tr>")
    parts += ["</table>", "<h2>Loop state</h2>",
              f"<p>round {esc(state.get('round', '?'))} &middot; phase {esc(state.get('phase', '?'))} &middot; "
              f"readiness {esc(state.get('readiness', '?'))} &middot; evidence {esc(state.get('evidence', '?'))}"
              f" &middot; risk {esc(state.get('risk', '?'))} &middot; "
              f"blockers {len(state.get('blockers') or [])}</p>",
              f"<p>anchor verdict {esc(witness.get('verdict_hash', 'unsigned'))}</p>",
              "<h2>Queue</h2>",
              "<table><tr><th>task</th><th>status</th><th>assignee</th><th>title</th></tr>"]
    for row in rows:
        parts.append(f"<tr><td>{esc(row['id'])}</td><td>{esc(row['status'])}</td>"
                     f"<td>{esc(row['assignee'])}</td><td>{esc(row['title'])}</td></tr>")
    parts += ["</table>", "</body></html>", ""]
    return "\n".join(parts)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Render the operator status page.")
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--out", default=OUT_REL)
    args = ap.parse_args(argv)
    page = render(args.root)
    path = os.path.join(args.root, args.out)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(page)
    print(f"status page: {args.out} ({len(page)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
