#!/usr/bin/env python3
"""Local gate for the loop-system project (the "Prove" step of the loop).

Contract
--------
* Prints ``LOOP_ENGINEERING_PASS_0001`` and exits 0 only when every mandatory
  dimension passes (and, in ``--strict`` mode, when no optional dimension is skipped).
* Two honest non-PASS verdicts exist: ``LOOP_ENGINEERING_COLD_0001`` (a tree that never ran the loop
  cannot prove its own history) and dimensions marked ``COLD`` / ``N/A``, which are named, never counted
  as passes. A dimension whose input the project does not ship (D08, the study corpus) is ``N/A``, not a
  failure: ``--strict`` means every dimension that applies is green, not that every input exists.
* Otherwise prints one ``[FAIL]`` line per dimension plus a numbered failure list
  and ``LOOP_ENGINEERING_STOP_0001``, exit 1.
* **Read-only.** The gate never repairs anything and never writes to the tree; the only writes are its
  own transcripts (``--report``) and the D05 binding anchor ``artifacts/loop_gate_pass.json``. Repair
  happens in the loop, not inside the checker.

Hardened after an independent review round (see ``artifacts/rounds/round_04.md``):
D01/D02 are line-anchored and command-bound instead of substring checks, D03 checks
monotone history, D04 requires evidence bound to a command/artifact instead of headings,
D05 binds the handoff to the gate's own machine report instead of hand-written prose,
D08 reports real bytes, D09 checks role artifacts for substance and attribution.
Residual, documented limitation: every input is still author-reachable on this machine —
the gate makes fabrication *detectable by a command*, not impossible.

Usage
-----
    python3 scripts/check_loop.py            # normal mode: optional dims may SKIP
    python3 scripts/check_loop.py --strict   # SKIP becomes FAIL (project acceptance)
    python3 scripts/check_loop.py --report artifacts/loop_gate_report.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys

MARKER_PASS = "LOOP_ENGINEERING_PASS_0001"
MARKER_STOP = "LOOP_ENGINEERING_STOP_0001"
# Third verdict for a tree that has never run the loop: the code can be checked, the history cannot be
# proven and must not be invented. See scripts/bootstrap_state.py and PLANS п.29.
MARKER_COLD = "LOOP_ENGINEERING_COLD_0001"
COLD_DIMS = {"D03", "D04", "D05", "D09", "D12"}
HEALTH_SCRIPT = "scripts/change_health.py"
HEALTH_POLICY_REL = "configs/change_health.json"
HEALTH_BASELINE_REL = "configs/change_health_baseline.json"
# Inputs the project deliberately does not ship. Absent input is not a defect of the tree under test.
NOT_APPLICABLE_DIMS = {"D08"}

TEXT_EXT = {".py", ".md", ".txt", ".sh", ".json", ".yml", ".yaml", ".toml", ".cfg", ".ini", ".swift"}
SKIP_DIRS = {".git", "course", "__pycache__", ".venv", "venv", "node_modules", ".idea", ".DS_Store",
             # build output: a publication tree is a copy of this repository, and auditing the copy as if
             # it were source makes the gate flag its own guardrail patterns (`dist/public/scripts/...`).
             "dist"}

DIM_IDS = ["D01", "D02", "D03", "D04", "D05", "D06", "D07", "D08", "D09", "D10"]
# Dimensions that may legitimately SKIP in normal mode (their input is produced by an optional stage, or
# is not shipped in this tree: the course corpus is study material, not part of the tool).
OPTIONAL_DIMS = {"D09", "D08"}
GATE_SCRIPT = "scripts/check_loop.py"
GATE_CMD = "python3 scripts/check_loop.py"
REPORT_REL = "artifacts/loop_gate_report.json"
# Stage 6 (issue queue): the bridge plus the worker brief it renders.
BRIDGE_SCRIPT = "scripts/sync_issues_to_kanban.py"
BRIEF_SCRIPT = "scripts/worker_brief.py"
PROFILES_REL = "configs/repo_profiles.json"
# Stage 7 (product acceptance): the second verdict, with its own owner and its own gate.
PRODUCT_SCRIPT = "scripts/check_product.py"
PRODUCT_SCENARIOS_REL = "configs/product_scenarios.json"
# Stage 7b (external anchor): the verdict leaves the machine.
WITNESS_SCRIPT = "scripts/anchor_witness.py"
WITNESS_CONFIG_REL = "configs/anchor_witness.json"
ALLOWED_SIGNERS_REL = "configs/allowed_signers"
ANCHOR_SIG_REL = "artifacts/loop_gate_verdict.txt.sig"
WITNESS_RECORD_REL = "artifacts/anchor_witness.json"
# Stage 7c (visual checks): what a user sees, not only what the JSON says.
VISUAL_SCRIPT = "scripts/check_visual.py"
VISUAL_SCENARIOS_REL = "configs/visual_scenarios.json"
STATUS_PAGE_SCRIPT = "scripts/render_status_page.py"
# The brief must itself carry the evidence rule, and the validator must be able to reject a bundle
# that is incomplete or failing — the exact gap that produced the first PR's REJECT.
BRIEF_REQUIREMENTS = ["evidence bundle", "verbatim", "exit", "one task = one pr", "NOT RUN"]
# Binding anchor for D05: written whenever every dimension EXCEPT D05 is green, so a dimension never
# has to certify itself. It records the verdict hash of that all-other-green set.
PASS_REL = "artifacts/loop_gate_pass.json"
BINDING_DIM = "D05"

# Narrow patterns only: a real shell/subprocess call to a CLI agent. Prose mentions are fine.
DIRECT_CLI_PATTERNS = [
    (r"\bcodex\s+exec\b", "direct `codex exec`"),
    (r"\bclaude\s+(-p|--print)\b", "direct `claude -p`"),
    (r"\b(agy|grok|kimi|dsh)\s+(-p|run|exec)\b", "direct CLI agent invocation"),
    (r"subprocess\.[A-Za-z_.]*\(\s*[\[(]?\s*[\"'](codex|claude|grok|kimi|agy|dsh)[\"']",
     "subprocess call to a CLI agent binary"),
]
SHELL_LANGS = {"bash", "sh", "shell", "zsh", "console", "pwsh", "powershell"}
INVOCATION_AT_LINE_START = re.compile(r"^\s*[$>]?\s*(codex|claude|grok|kimi|agy|dsh)\s+\S")
SECRET_PATTERNS = [
    (r"\bsk-[A-Za-z0-9]{20,}", "OpenAI-style secret"),
    (r"\bghp_[A-Za-z0-9]{20,}", "GitHub PAT"),
    (r"\bAKIA[0-9A-Z]{16}\b", "AWS access key id"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key"),
    (r"(?i)\b(api[_-]?key|access[_-]?token|client[_-]?secret)\b\s*[:=]\s*[\"'][A-Za-z0-9_\-]{24,}[\"']",
     "hardcoded credential"),
]

ROUND_HEADINGS = ["## Gap", "## Change", "## Evidence", "## Metrics", "## Next"]
CONTRACT_FIELDS = ["Goal:", "Sources:", "Constraints:", "Done-when:", "Stop-if:"]
ROLE_FILES = {
    "explorer_findings": ("## ", 300),
    "reviewer_risks": ("## ", 300),
    "worker_changes": ("## ", 300),
    "evaluator_verification": ("## ", 300),
}
ARTIFACT_REF = re.compile(r"\b[\w./-]+\.(?:py|json|md|sh|swift|txt)\b")
EXIT_CODE_REF = re.compile(r"exit[=:\s]+\d|exit code", re.I)
FENCED_BLOCK = re.compile(r"```[^\n]*\n(.*?)```", re.S)


class Dim:
    def __init__(self, ident, title, ok, detail, skip=False, reasons=None):
        self.ident, self.title, self.ok, self.detail, self.skip = ident, title, ok, detail, skip
        self.reasons = reasons or []


def read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def walk_text(root, skip_paths):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and not d.startswith(".git")]
        for name in filenames:
            path = os.path.join(dirpath, name)
            if os.path.realpath(path) in skip_paths:
                continue
            if os.path.splitext(name)[1].lower() in TEXT_EXT:
                yield path


def cold_start(root):
    """True when this tree declares a cold start and nothing contradicts it.

    The declaration alone is not enough: if rounds or handoff artifacts exist, the tree has run the loop
    and the state dimensions must be proven normally. That is what stops "cold" from becoming a way to
    hide a broken loop behind an empty directory.
    """
    text = read(os.path.join(root, "artifacts", "loop_state.json"))
    if text is None:
        return False
    try:
        state = json.loads(text)
    except json.JSONDecodeError:
        return False
    if state.get("state") != "cold":
        return False
    rounds = os.path.join(root, "artifacts", "rounds")
    if os.path.isdir(rounds) and any(n.startswith("round_") for n in os.listdir(rounds)):
        return False
    handoff = os.path.join(root, "handoff")
    if os.path.isdir(handoff):
        for dirpath, _dirnames, filenames in os.walk(handoff):
            if any(n.endswith(".md") for n in filenames):
                return False
    return True


def section_body(text, heading):
    """Body of a '## X' section, up to the next '## ' heading."""
    m = re.search(rf"^{re.escape(heading)}\s*$", text, re.M)
    if not m:
        return ""
    rest = text[m.end():]
    nxt = re.search(r"^##\s", rest, re.M)
    return rest[:nxt.start()] if nxt else rest


# ---------------------------------------------------------------- dimensions

def dim_contract_skill(root):
    rel = ".agents/skills/loop-engineering/SKILL.md"
    text = read(os.path.join(root, rel))
    if text is None:
        return Dim("D01", "contract_skill", False, f"{rel} missing", reasons=[f"{rel} does not exist"])
    reasons = []
    for field in CONTRACT_FIELDS:
        m = re.search(rf"^{re.escape(field)}[ \t]*(\S.*)$", text, re.M)
        if not m:
            reasons.append(f"{rel}: contract field {field!r} missing or empty (must start a line and carry a value)")
    dw = re.search(r"^Done-when:[ \t]*(\S.*)$", text, re.M)
    if dw and (GATE_SCRIPT not in dw.group(1) or MARKER_PASS not in dw.group(1)):
        reasons.append(f"{rel}: 'Done-when' must name {GATE_SCRIPT} and the marker {MARKER_PASS}")
    if "ywcrew" not in text:
        reasons.append(f"{rel}: the CLI-agent rule (ywcrew-only) is not stated")
    if MARKER_PASS not in text:
        reasons.append(f"{rel}: {MARKER_PASS} not documented")
    return Dim("D01", "contract_skill", not reasons,
               f"{rel} contract complete" if not reasons else f"{rel} incomplete", reasons=reasons)


def dim_sources_of_truth(root):
    reasons = []
    agents = read(os.path.join(root, "AGENTS.md"))
    if agents is None:
        reasons.append("AGENTS.md missing")
    else:
        for needle in ("Sources of truth", "Done-when", "## Стоп-условия", GATE_SCRIPT):
            if needle not in agents:
                reasons.append(f"AGENTS.md missing {needle!r}")
    plans = read(os.path.join(root, "PLANS.md"))
    if plans is None:
        reasons.append("PLANS.md missing")
    elif GATE_CMD not in plans:
        reasons.append(f"PLANS.md must contain the gate command {GATE_CMD!r}")
    readme = read(os.path.join(root, "README.md"))
    if readme is None:
        reasons.append("README.md missing")
    elif GATE_SCRIPT not in readme:
        reasons.append(f"README.md must reference {GATE_SCRIPT!r}")
    return Dim("D02", "sources_of_truth", not reasons,
               "AGENTS.md + PLANS.md + README.md carry the contract and the gate command",
               reasons=reasons)


def dim_loop_state(root):
    rel = "artifacts/loop_state.json"
    if cold_start(root):
        return Dim("D03", "loop_state", False,
                   f"cold start: {rel} declares no round has run in this tree",
                   skip=True, reasons=["run the loop once (a round artifact plus a state update) and this "
                                       "dimension becomes provable; a synthesised history is a fake"])
    text = read(os.path.join(root, rel))
    if text is None:
        return Dim("D03", "loop_state", False, f"{rel} missing", reasons=[f"{rel} does not exist"])
    reasons = []
    try:
        state = json.loads(text)
    except json.JSONDecodeError as exc:
        return Dim("D03", "loop_state", False, f"{rel} not valid JSON", reasons=[f"{rel}: {exc}"])
    for key, lo, hi in (("readiness", 0, 100), ("evidence", 0, 100), ("risk", 0, 100)):
        val = state.get(key)
        if not isinstance(val, int) or not lo <= val <= hi:
            reasons.append(f"{rel}: '{key}' must be an int in {lo}..{hi}, got {val!r}")
    if not isinstance(state.get("round"), int) or state.get("round", 0) < 1:
        reasons.append(f"{rel}: 'round' must be an int >= 1")
    if not isinstance(state.get("blockers"), list):
        reasons.append(f"{rel}: 'blockers' must be a list")
    if state.get("marker") != MARKER_PASS:
        reasons.append(f"{rel}: 'marker' must be {MARKER_PASS!r}")
    if state.get("phase") not in {"build", "fail", "repair", "prove", "review", "handoff"}:
        reasons.append(f"{rel}: 'phase' must be one of build/fail/repair/prove/review/handoff")
    if not state.get("updated_at"):
        reasons.append(f"{rel}: 'updated_at' missing")

    # Metric provenance: the declared history must be monotone and must end at the current values.
    hist = state.get("history")
    if not isinstance(hist, list) or len(hist) < 2:
        reasons.append(f"{rel}: 'history' must be a list with at least 2 entries (provenance of the metrics)")
    else:
        prev = None
        for i, entry in enumerate(hist):
            if not isinstance(entry, dict):
                reasons.append(f"{rel}: history[{i}] must be an object")
                continue
            for key in ("round", "readiness", "evidence", "risk", "blockers"):
                if not isinstance(entry.get(key), int) or isinstance(entry.get(key), bool):
                    reasons.append(f"{rel}: history[{i}]['{key}'] must be an int")
            if prev is not None:
                if entry.get("readiness", 0) < prev.get("readiness", 0):
                    reasons.append(f"{rel}: history[{i}] readiness decreased ({prev.get('readiness')} -> {entry.get('readiness')})")
                if entry.get("evidence", 0) < prev.get("evidence", 0):
                    reasons.append(f"{rel}: history[{i}] evidence decreased ({prev.get('evidence')} -> {entry.get('evidence')})")
                if entry.get("risk", 100) > prev.get("risk", 100):
                    reasons.append(f"{rel}: history[{i}] risk increased ({prev.get('risk')} -> {entry.get('risk')})")
                if entry.get("blockers", 0) > prev.get("blockers", 0):
                    reasons.append(f"{rel}: history[{i}] blockers increased ({prev.get('blockers')} -> {entry.get('blockers')})")
            prev = entry
        last = hist[-1]
        for key in ("round", "readiness", "evidence", "risk"):
            if last.get(key) != state.get(key):
                reasons.append(f"{rel}: history[-1]['{key}']={last.get(key)!r} does not match current {key}={state.get(key)!r}")
        if last.get("blockers") != len(state.get("blockers") or []):
            reasons.append(f"{rel}: history[-1]['blockers'] must equal len(current blockers)")
    return Dim("D03", "loop_state", not reasons,
               f"{rel} schema + monotone history ok" if not reasons else f"{rel} invalid", reasons=reasons)


def dim_rounds(root, min_rounds=3):
    rdir = os.path.join(root, "artifacts", "rounds")
    if cold_start(root):
        return Dim("D04", "rounds", False, "cold start: no round artifact exists in this tree", skip=True,
                   reasons=["a round file records a real iteration; inventing one would be the exact "
                            "failure this gate exists to catch"])
    names = sorted(n for n in (os.listdir(rdir) if os.path.isdir(rdir) else [])
                   if re.fullmatch(r"round_\d+\.md", n))
    reasons = []
    if len(names) < min_rounds:
        reasons.append(f"artifacts/rounds/: {len(names)} round artifact(s), need >= {min_rounds}")
    for name in names:
        rel = f"artifacts/rounds/{name}"
        text = read(os.path.join(rdir, name)) or ""
        if len(text) < 200:
            reasons.append(f"{rel}: too thin ({len(text)} chars)")
            continue
        for heading in ROUND_HEADINGS:
            if heading not in text:
                reasons.append(f"{rel}: missing {heading!r}")
        blocks = FENCED_BLOCK.findall(text)
        if not blocks:
            reasons.append(f"{rel}: no fenced evidence block (a round must show a command or an artifact)")
        body = section_body(text, "## Evidence")
        if len(body.strip()) < 150:
            reasons.append(f"{rel}: '## Evidence' body too thin ({len(body.strip())} chars)")
        bound = bool(ARTIFACT_REF.search(body)) or bool(EXIT_CODE_REF.search(text)) \
            or MARKER_PASS in text or MARKER_STOP in text or GATE_SCRIPT in text
        if not bound:
            reasons.append(f"{rel}: evidence not bound to a command, exit code, marker or artifact path")
    return Dim("D04", "rounds", not reasons, f"{len(names)} round artifact(s) with bound evidence", reasons=reasons)


def _verdict_hash(payload, exclude=(BINDING_DIM,)):
    """Stable hash over a run's verdict set (statuses only, no timestamps or paths).

    ``D05`` is excluded: the handoff-binding dimension cannot certify itself, so the hash covers
    exactly the dimensions that do not depend on the handoff.
    """
    stable = {
        "marker": payload.get("marker"),
        "dimensions": [{"id": d.get("id"), "status": d.get("status")}
                       for d in payload.get("dimensions", []) if d.get("id") not in exclude],
    }
    return hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def dim_handoff(root, other_pairs=None):
    """D05: bind the handoff to a gate run.

    ``other_pairs`` maps the non-binding dimensions (D01-D04, D06-D09) to ``(ok, skip)`` as computed by
    *this* run; comparing the raw state instead of the printed status keeps the check independent of
    normal/strict mode. When every one of them is green here, the anchor must match them exactly — so an anchor
    minted for a status set the machine is not actually in cannot help: a passing marker still requires
    the run itself to be green. When another dimension is already failing, only the handoff's internal
    binding is checked (the failure is reported by that dimension anyway).
    """
    if cold_start(root):
        return Dim("D05", "handoff", False, "cold start: no handoff has been written in this tree", skip=True,
                   reasons=["a handoff binds role artifacts to a real gate run; with no run there is "
                            "nothing to bind"])
    reasons = []
    rel = "handoff/latest_loop.json"
    text = read(os.path.join(root, rel))
    payload = None
    if text is None:
        reasons.append(f"{rel} missing")
    else:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            reasons.append(f"{rel}: {exc}")
        if payload is not None:
            if payload.get("status") != "pass":
                reasons.append(f"{rel}: 'status' must be 'pass'")
            if payload.get("marker") != MARKER_PASS:
                reasons.append(f"{rel}: 'marker' must be {MARKER_PASS!r}")
            for key in ("gate_command", "generated_at"):
                if not payload.get(key):
                    reasons.append(f"{rel}: '{key}' missing")
            # A passing handoff must not carry open blockers; accepted limits go to 'deferrals'.
            blockers = payload.get("blockers")
            if not isinstance(blockers, list):
                reasons.append(f"{rel}: 'blockers' must be a list (use 'deferrals' for accepted limits)")
            elif blockers:
                reasons.append(f"{rel}: 'blockers' is not empty while status=pass -> {blockers}")
            roles = payload.get("roles_run")
            if not isinstance(roles, list) or not roles:
                reasons.append(f"{rel}: 'roles_run' must list the role runs behind this handoff")
            reviewed = payload.get("reviewed_by")
            if not isinstance(reviewed, list) or not reviewed:
                reasons.append(f"{rel}: 'reviewed_by' must name the independent reviewer(s)")
            fp = payload.get("gate_report_verdict_hash")
            if not fp:
                reasons.append(f"{rel}: 'gate_report_verdict_hash' missing (binds the handoff to a gate run)")
            arts = payload.get("artifacts")
            if not isinstance(arts, list) or not arts:
                reasons.append(f"{rel}: 'artifacts' must be a non-empty list")
            else:
                for a in arts:
                    if not os.path.exists(os.path.join(root, a)):
                        reasons.append(f"{rel}: listed artifact {a!r} does not exist")
    # Bind the handoff to the gate's own machine transcript, not to hand-written prose. The anchor is
    # written whenever every dimension except D05 is green, so the binding dimension never certifies
    # itself; a handoff that quotes the wrong hash describes a different gate run and fails.
    anchor_rel = PASS_REL
    anchor_text = read(os.path.join(root, anchor_rel))
    if anchor_text is None:
        reasons.append(f"{anchor_rel} missing (no run has been green in every other dimension yet)")
    else:
        try:
            anchor = json.loads(anchor_text)
        except json.JSONDecodeError as exc:
            anchor = None
            reasons.append(f"{anchor_rel}: {exc}")
        if anchor is not None:
            if anchor.get("all_other_dimensions_ok") is not True:
                reasons.append(f"{anchor_rel}: all_other_dimensions_ok is not true")
            anchor_statuses = {d.get("id"): d.get("status") for d in anchor.get("dimensions", [])}
            anchor_pairs = {d.get("id"): (bool(d.get("ok")), bool(d.get("skip")))
                            for d in anchor.get("dimensions", []) if d.get("id") != BINDING_DIM}
            record = {"marker": MARKER_PASS,
                      "dimensions": [{"id": k, "status": v} for k, v in anchor_statuses.items()]}
            actual = _verdict_hash(record)
            if anchor.get("verdict_hash") != actual:
                reasons.append(f"{anchor_rel}: recorded verdict hash {anchor.get('verdict_hash')} "
                               f"does not match its own contents {actual}")
            if other_pairs is not None:
                expected = {k: v for k, v in other_pairs.items() if k != BINDING_DIM}
                if all(ok for ok, _ in expected.values()):
                    # The machine is green right now: the anchor must describe exactly this run's state,
                    # otherwise it was written for a different (or invented) one.
                    if anchor_pairs != expected:
                        reasons.append(f"{anchor_rel}: recorded state {anchor_pairs} != this run's "
                                       f"{expected} — the anchor does not describe this run")
            if payload is not None and payload.get("gate_report_verdict_hash"):
                if payload["gate_report_verdict_hash"] != actual:
                    reasons.append(f"{anchor_rel}: verdict hash {actual} != handoff "
                                   f"gate_report_verdict_hash {payload['gate_report_verdict_hash']} "
                                   f"(the handoff describes a different gate run)")
    syn = read(os.path.join(root, "handoff", "supervisor_synthesis.md"))
    if syn is None:
        reasons.append("handoff/supervisor_synthesis.md missing")
    else:
        if MARKER_PASS not in syn:
            reasons.append(f"handoff/supervisor_synthesis.md missing {MARKER_PASS}")
        if GATE_SCRIPT not in syn:
            reasons.append(f"handoff/supervisor_synthesis.md must name the gate command")
    return Dim("D05", "handoff", not reasons,
               "handoff bound to the gate's machine report" if not reasons else "handoff invalid", reasons=reasons)


def _scan_lines(path):
    """Lines to inspect for direct CLI calls, as ``(lineno, line, strict)``.

    Code files are scanned whole (``strict``). Markdown is scanned for invocation-shaped lines only:
    a line that *starts* with an agent binary (optionally after a `$`/`>` prompt char) is a pasteable
    command, while transcripts and reports that merely quote a finding (round artifacts, gate output)
    are evidence and must stay verbatim. Fenced blocks tagged as a shell language are scanned strictly,
    because that is where a reader would copy-paste from.
    """
    text = read(path)
    if text is None:
        return []
    if os.path.splitext(path)[1].lower() != ".md":
        return [(i, line, True) for i, line in enumerate(text.splitlines(), 1)]
    fence_lang, out = "", []
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith("```"):
            if fence_lang:
                fence_lang = ""
            else:
                fence_lang = stripped[3:].strip().lower() or "text"
            continue
        out.append((lineno, line, fence_lang in SHELL_LANGS))
    return out


def dim_no_direct_cli(root, skip_paths):
    hits = []
    for path in walk_text(root, skip_paths):
        for lineno, line, strict in _scan_lines(path):
            if not strict:
                if not INVOCATION_AT_LINE_START.search(line):
                    continue
                hits.append(f"{os.path.relpath(path, root)}:{lineno}: CLI agent invoked at line start")
                continue
            for pattern, label in DIRECT_CLI_PATTERNS:
                if re.search(pattern, line):
                    hits.append(f"{os.path.relpath(path, root)}:{lineno}: {label}")
    return Dim("D06", "guardrail_no_direct_cli", not hits,
               "no direct CLI-agent invocation in repo code or pasteable commands" if not hits
               else f"{len(hits)} hit(s)",
               reasons=hits)


def dim_no_secrets(root, skip_paths):
    hits = []
    for path in walk_text(root, skip_paths):
        text = read(path)
        if not text:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for pattern, label in SECRET_PATTERNS:
                if re.search(pattern, line):
                    hits.append(f"{os.path.relpath(path, root)}:{lineno}: {label}")
    return Dim("D07", "guardrail_no_secrets", not hits,
               "no credential-shaped strings in repo files" if not hits else f"{len(hits)} hit(s)",
               reasons=hits)


def dim_course_corpus(root, min_lessons=17, min_words=40000, min_prompt_bytes=15000):
    cdir = os.path.join(root, "course")
    # The course corpus is study material, not part of the tool: a tree that does not ship it is not
    # broken, and demanding it would make the gate pass only on its author's machine. A tree that ships a
    # partial corpus is broken, and must fail — an absent input SKIPs with a reason, a thin one FAILs.
    if not os.path.isdir(cdir):
        return Dim("D08", "course_corpus", True, "corpus not shipped in this tree",
                   skip=True, reasons=["course/ absent: optional study material, not part of the tool"])
    reasons = []
    for rel in ("course/README.md", "course/00_mechanics_map.md"):
        if not os.path.exists(os.path.join(root, rel)):
            reasons.append(f"{rel} missing")
    lessons, words, pbytes = [], 0, 0
    if os.path.isdir(cdir):
        for sect in sorted(os.listdir(cdir)):
            spath = os.path.join(cdir, sect)
            if not os.path.isdir(spath) or sect == "skill_packages":
                continue
            for les in sorted(os.listdir(spath)):
                lpath = os.path.join(spath, les)
                if not os.path.isdir(lpath):
                    continue
                ptext = read(os.path.join(lpath, "prompts.md"))
                stext = read(os.path.join(lpath, "slides_ocr.md"))
                if ptext is None or stext is None:
                    reasons.append(f"course/{sect}/{les}: prompts.md or slides_ocr.md missing")
                    continue
                if len(ptext) < 200 or len(stext) < 200:
                    reasons.append(f"course/{sect}/{les}: lesson file too thin")
                lessons.append(les)
                pbytes += len(ptext.encode("utf-8"))
                words += len(stext.split())
    if len(lessons) < min_lessons:
        reasons.append(f"course corpus: {len(lessons)} lessons, need >= {min_lessons}")
    if words < min_words:
        reasons.append(f"course corpus: {words} OCR words, need >= {min_words}")
    if pbytes < min_prompt_bytes:
        reasons.append(f"course corpus: {pbytes} prompt bytes, need >= {min_prompt_bytes}")
    detail = f"{len(lessons)} lessons, {words} OCR words, {pbytes} prompt bytes"
    return Dim("D08", "course_corpus", not reasons, detail, reasons=reasons)


def dim_roles_fanout(root):
    if cold_start(root):
        return Dim("D09", "roles_fanout", False, "cold start: no role artifact exists in this tree", skip=True,
                   reasons=["role files are written by a real fan-out against a real task"])
    missing, thin = [], []
    for name, (_, min_chars) in ROLE_FILES.items():
        rel = f"handoff/agents/{name}.md"
        text = read(os.path.join(root, rel))
        if text is None:
            missing.append(rel)
            continue
        if len(text) < min_chars:
            thin.append(f"{rel}: {len(text)} chars, need >= {min_chars}")
        elif "runId:" not in text:
            thin.append(f"{rel}: no 'runId:' attribution to the role run")
    if missing:
        return Dim("D09", "roles_fanout", True, "stage 2 (ywcrew roles) not run yet",
                   skip=True, reasons=[f"missing {m}" for m in missing] + thin)
    if thin:
        return Dim("D09", "roles_fanout", False, "role artifacts present but not substantive", reasons=thin)
    return Dim("D09", "roles_fanout", True, "four role artifacts present with run attribution")


def dim_issue_bridge(root):
    """D10: the issue queue (stage 6) must be wired *and* self-checking.

    Three things are asserted, in the order they can break:
    1. the machinery exists (bridge script, brief renderer, repository profiles);
    2. every profile declares an evidence bundle of at least two commands, and the brief rendered from
       it carries those commands plus the evidence rules verbatim — this is the mechanical answer to the
       live PR failure, where the rule lived in prose and the worker proved one gate out of three;
    3. the validator is not vacuous: it must accept a complete transcript and reject one that skips a
       command or reports a non-zero exit code. A validator that only ever passes would make the whole
       dimension decoration, so it is attacked here with the two failure modes that actually happened.
    """
    reasons = []
    for rel in (BRIDGE_SCRIPT, BRIEF_SCRIPT, PROFILES_REL):
        if not os.path.exists(os.path.join(root, rel)):
            reasons.append(f"{rel} missing")
    if reasons:
        return Dim("D10", "issue_bridge", False, "issue bridge incomplete", reasons=reasons)

    brief_path = os.path.join(root, "scripts", "worker_brief.py")
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("loop_worker_brief", brief_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load a module spec from {brief_path}")
        worker_brief = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker_brief)
    except Exception as exc:  # noqa: BLE001 - any import failure is a failing dimension, not a crash
        return Dim("D10", "issue_bridge", False, f"{BRIEF_SCRIPT} not importable",
                   reasons=[f"{BRIEF_SCRIPT}: {exc.__class__.__name__}: {exc}"])

    try:
        profiles = worker_brief.load_profiles(root)
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        return Dim("D10", "issue_bridge", False, f"{PROFILES_REL} unreadable",
                   reasons=[f"{PROFILES_REL}: {exc}"])
    if not profiles:
        reasons.append(f"{PROFILES_REL}: no repository profile declared")

    sample = {"number": 1, "title": "sample", "body": "sample body",
              "url": "https://example.invalid/issues/1", "labels": []}
    checked = 0
    ready_bundle = None
    attacks_run = 0
    for name, profile in sorted(profiles.items()):
        bundle = profile.get("evidence_bundle") or []
        if len(bundle) >= 2 and all(i.get("command") and i.get("source") for i in bundle) \
                and ready_bundle is None:
            ready_bundle = profile
        if len(bundle) < 2:
            reasons.append(f"{name}: evidence_bundle needs >= 2 commands (a subset is exactly what the "
                           "first real PR was rejected for), got {len(bundle)}")
        for item in bundle:
            if not item.get("command") or not item.get("source"):
                reasons.append(f"{name}: evidence_bundle entry needs both 'command' and 'source': {item!r}")
        rules = " ".join(profile.get("pr_rules") or []).lower()
        if "one task = one pr" not in rules:
            reasons.append(f"{name}: pr_rules must state the one-task-one-PR rule")
        if not (profile.get("kanban") or {}).get("assignee"):
            reasons.append(f"{name}: kanban.assignee missing (the queue would have no executor)")
        if not profile.get("bundle_source"):
            reasons.append(f"{name}: 'bundle_source' missing: the bundle needs a named origin, otherwise "
                           "the list is an unverifiable assertion about the repository")
        brief = worker_brief.render_brief(name, profile, sample)
        for item in bundle:
            if item.get("command") and item["command"] not in brief:
                reasons.append(f"{name}: brief does not name the mandatory command `{item['command']}`")
        low = brief.lower()
        for needle in BRIEF_REQUIREMENTS:
            if needle.lower() not in low:
                reasons.append(f"{name}: rendered brief is missing the evidence rule text {needle!r}")
        if brief != worker_brief.render_brief(name, profile, sample):
            reasons.append(f"{name}: brief rendering is not deterministic")
        # The same rule must travel with a ywcrew dispatch, not only with the kanban task body.
        spec = worker_brief.render_ywcrew_task(name, profile, sample)
        constraints = (spec.get("task") or {}).get("constraints", "")
        for item in bundle:
            if item.get("command") and item["command"] not in constraints:
                reasons.append(f"{name}: ywcrew task constraints omit the mandatory command "
                               f"`{item['command']}`")
        for needle in ("verbatim", "exit", "NOT RUN"):
            if needle.lower() not in constraints.lower():
                reasons.append(f"{name}: ywcrew task constraints are missing the evidence rule text "
                               f"{needle!r}")
        if spec.get("task", {}).get("briefing") != brief:
            reasons.append(f"{name}: ywcrew task briefing differs from the brief (two sources for one rule)")
        checked += 1

    if checked and ready_bundle:
        profile = ready_bundle
        bundle = [i["command"] for i in (profile.get("evidence_bundle") or [])]
        complete = "\n".join(f"$ {c}\noutput\n" + "exit=0\n" for c in bundle)
        partial = f"$ {bundle[0]}\noutput\nexit=0\n"
        failing = f"$ {bundle[0]}\noutput\nexit=1\n"
        # Borrowed exit code: every command is listed, one trailing exit=0 is the only code in the
        # transcript. The first live review used exactly this to reject the whole design.
        borrowed = "\n".join(f"$ {c}\noutput\n" for c in bundle) + "exit=0\n"
        # Hidden rerun: the first occurrence of a command passed, a later rerun of the same command
        # failed. Reviewing only the first occurrence would report a clean bundle (second live review).
        hidden_rerun = (f"$ {bundle[0]}\noutput\nexit=0\n\n$ {bundle[0]}\nboom\nexit=1\n"
                        + "\n".join(f"$ {c}\noutput\nexit=0\n" for c in bundle[1:]))
        # Runaway repair: the same command red three times in a row. "Repair is bounded" is a rule in
        # the brief; a validator that accepts this transcript makes the rule decorative.
        runaway = ("\n".join(f"$ {bundle[0]}\nboom\nexit=1\n" for _ in range(3))
                   + "\n".join(f"$ {c}\noutput\nexit=0\n" for c in bundle[1:]))
        attacks = [("complete transcript rejected", complete, True),
                   ("incomplete transcript accepted", partial, False),
                   ("failing exit code accepted", failing, False),
                   ("empty transcript accepted", "", False),
                   ("borrowed exit code accepted", borrowed, False),
                   ("hidden failing rerun accepted", hidden_rerun, False),
                   ("runaway repair accepted", runaway, False)]
        for label, text, want_ok in attacks:
            got_ok = not worker_brief.verify_evidence(profile, text)
            attacks_run += 1
            if got_ok != want_ok:
                reasons.append(f"evidence validator is vacuous: {label} ({BRIEF_SCRIPT}.verify_evidence)")

    detail = (f"{len(profiles)} repo profile(s), bundle rules in the brief, validator attacked with "
              f"{attacks_run} falsification cases")
    return Dim("D10", "issue_bridge", not reasons, detail if not reasons else "issue bridge invalid",
               reasons=reasons)


def _good_observation(scenario):
    """An observation that satisfies the scenario, derived from its own expectations.

    Building the "good" case from the config (instead of hardcoding one) is what keeps the attack below
    from going stale when scenarios change.
    """
    exp = scenario.get("expect") or {}
    obs = {}
    if "status" in exp:
        obs["status"] = exp["status"]
    if exp.get("json_ok_true"):
        obs["body"] = '{"ok":true,"data":true}'
    if "body_contains" in exp:
        obs["body"] = f"prefix {exp['body_contains']} suffix"
    if "exit_code" in exp:
        obs["exit_code"] = exp["exit_code"]
    if "max_shutdown_ms" in exp:
        obs["shutdown_ms"] = max(1, exp["max_shutdown_ms"] - 1)
    if scenario.get("kind") == "startup_failure":
        obs["exit_code"] = 1
        obs["stderr"] = f"startup failed: {exp.get('stderr_contains', '')}"
    if scenario.get("kind") == "json_schema":
        obs = {"valid_failures": [], "invalid_accepted": []}
    return obs


def dim_product_gate(root):
    """D11: the product gate (stage 7) must exist, be user-facing, and be able to fail.

    The technical gate cannot answer "can a user do their job with this product", so stage 7 adds a second
    verdict with its own owner. This dimension keeps that second gate honest: the scenarios must state what
    a user observes and why it matters, and the evaluator must reject falsified observations — including
    the ones produced by mutating each scenario's own expectations, so the attack cannot go stale.
    """
    reasons = []
    for rel in (PRODUCT_SCRIPT, PRODUCT_SCENARIOS_REL):
        if not os.path.exists(os.path.join(root, rel)):
            reasons.append(f"{rel} missing")
    if reasons:
        return Dim("D11", "product_gate", False, "product gate incomplete", reasons=reasons)

    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("loop_check_product",
                                                     os.path.join(root, PRODUCT_SCRIPT))
        if spec is None or spec.loader is None:
            raise ImportError("cannot load a module spec for the product gate")
        product = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(product)
        config = product.load_scenarios(root)
    except Exception as exc:  # noqa: BLE001 - any failure is a failing dimension, not a crash
        return Dim("D11", "product_gate", False, f"{PRODUCT_SCRIPT} not loadable",
                   reasons=[f"{PRODUCT_SCRIPT}: {exc.__class__.__name__}: {exc}"])

    scenarios = config.get("scenarios") or []
    mandatory = [s for s in scenarios if not s.get("optional")]
    if len(mandatory) < 6:
        reasons.append(f"{PRODUCT_SCENARIOS_REL}: {len(mandatory)} mandatory scenarios, need >= 6 "
                       "(a product gate with a handful of checks is a smoke test, not acceptance)")
    ids = [s.get("id") for s in scenarios]
    if len(ids) != len(set(ids)):
        reasons.append(f"{PRODUCT_SCENARIOS_REL}: duplicate scenario ids in {ids}")
    for sc in scenarios:
        for field in ("id", "title", "kind", "expect"):
            if not sc.get(field):
                reasons.append(f"{PRODUCT_SCENARIOS_REL}: scenario {sc.get('id', '?')} lacks {field!r}")
        why = (sc.get("why") or "").strip()
        if len(why) < 25:
            reasons.append(f"{PRODUCT_SCENARIOS_REL}: scenario {sc.get('id', '?')} has no user-facing "
                           "'why' (the consequence a user would feel), got {why!r}")
    profile_name = config.get("profile")
    try:
        with open(os.path.join(root, PROFILES_REL), encoding="utf-8") as fh:
            profiles = json.load(fh)["profiles"]
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        profiles = {}
        reasons.append(f"{PROFILES_REL}: {exc}")
    if profile_name not in profiles:
        reasons.append(f"{PRODUCT_SCENARIOS_REL}: profile {profile_name!r} is not declared in {PROFILES_REL}")
    elif not (profiles[profile_name].get("local_path") or profiles[profile_name].get("local_path_env")):
        reasons.append(f"{PROFILES_REL}: profile {profile_name!r} declares neither local_path nor "
                       "local_path_env, so the product gate cannot find the product it is supposed to "
                       "accept")

    # Attack the evaluator: every scenario must reject its own falsified observations.
    falsifications, accepted_good = 0, 0
    for sc in scenarios:
        good = _good_observation(sc)
        if good and not product.evaluate(sc, good):
            accepted_good += 1
        exp = sc.get("expect") or {}
        attacks = []
        if "status" in exp:
            attacks.append({**good, "status": (exp["status"] or 0) + 1})
        if exp.get("json_ok_true"):
            attacks.append({**good, "body": '{"ok":false}'})
        if "body_contains" in exp:
            attacks.append({**good, "body": "no marker here"})
        if "exit_code" in exp:
            attacks.append({**good, "exit_code": exp["exit_code"] + 1})
        if "max_shutdown_ms" in exp:
            attacks.append({**good, "shutdown_ms": exp["max_shutdown_ms"] + 500})
        if exp.get("exit_nonzero"):
            attacks.append({**good, "exit_code": 0})
        if exp.get("stderr_contains"):
            attacks.append({**good, "stderr": "something went wrong"})
        if sc.get("kind") == "json_schema":
            attacks.append({"valid_failures": ["fixture.json"], "invalid_accepted": []})
            attacks.append({"valid_failures": [], "invalid_accepted": ["fixture.json"]})
        for attack in attacks:
            if not product.evaluate(sc, attack):
                reasons.append(f"{PRODUCT_SCENARIOS_REL}: {sc['id']} accepts a falsified observation "
                               f"{ {k: v for k, v in attack.items() if good.get(k) != v} } — the check is "
                               "vacuous")
            else:
                falsifications += 1
    if falsifications < 4:
        reasons.append(f"the product evaluator resisted only {falsifications} falsification(s); need >= 4")
    if accepted_good == 0:
        reasons.append("no scenario has a satisfiable observation — the evaluator rejects everything, "
                       "which is as useless as accepting everything")

    detail = (f"{len(scenarios)} scenario(s) ({len(mandatory)} mandatory), {falsifications} falsifications "
              f"rejected, {accepted_good} satisfiable")
    return Dim("D11", "product_gate", not reasons, detail if not reasons else "product gate invalid",
               reasons=reasons)


def dim_anchor_witness(root):
    """D12: the anchor must be signed, and a rewrite must be detectable.

    The technical gate's own residual risk was that every input it reads is authored on this machine: a
    fabricated anchor was detectable by a command but not impossible. Signing the anchor with a
    purpose-limited key (public half committed in ``configs/allowed_signers``) closes the silent-rewrite
    path; the external witness (a published digest, checked by ``check-external``) is the operator step
    that makes a coordinated rewrite visible, and it is deliberately not required here — this gate stays
    local and offline.
    """
    missing_state = [rel for rel in (ANCHOR_SIG_REL, WITNESS_RECORD_REL)
                     if not os.path.exists(os.path.join(root, rel))]
    if cold_start(root) and missing_state:
        return Dim("D12", "anchor_witness", False,
                   "cold start: no signed verdict exists in this tree", skip=True,
                   reasons=[f"{rel} is produced by a real gate run, not by the export" for rel in missing_state])
    reasons = []
    for rel in (WITNESS_SCRIPT, ALLOWED_SIGNERS_REL, WITNESS_CONFIG_REL, ANCHOR_SIG_REL, WITNESS_RECORD_REL):
        if not os.path.exists(os.path.join(root, rel)):
            reasons.append(f"{rel} missing")
    if reasons:
        return Dim("D12", "anchor_witness", False, "anchor witness incomplete", reasons=reasons)

    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("loop_anchor_witness",
                                                     os.path.join(root, WITNESS_SCRIPT))
        if spec is None or spec.loader is None:
            raise ImportError("cannot load a module spec for the anchor witness")
        witness = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(witness)
        config = witness.load_config(root)
    except Exception as exc:  # noqa: BLE001
        return Dim("D12", "anchor_witness", False, f"{WITNESS_SCRIPT} not loadable",
                   reasons=[f"{WITNESS_SCRIPT}: {exc.__class__.__name__}: {exc}"])

    destination = config.get("destination") or {}
    if not destination.get("repo") or not destination.get("issue"):
        reasons.append(f"{WITNESS_CONFIG_REL}: no external destination declared, so the anchor has no "
                       "witness outside this machine")
    if not config.get("marker"):
        reasons.append(f"{WITNESS_CONFIG_REL}: no marker, so a published comment cannot be attributed")

    ok, note = witness.verify(root, config)
    if not ok:
        reasons.append(f"the current anchor does not verify against {ALLOWED_SIGNERS_REL}: {note}")

    record_path = os.path.join(root, WITNESS_RECORD_REL)
    try:
        record = json.load(open(record_path, encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        record = {}
        reasons.append(f"{WITNESS_RECORD_REL}: {exc}")
    if record:
        current = json.load(open(os.path.join(root, PASS_REL), encoding="utf-8")).get("verdict_hash")
        if record.get("verdict_hash") != current:
            reasons.append(f"{WITNESS_RECORD_REL}: the witness signs verdict "
                           f"{str(record.get('verdict_hash'))} but the anchor now carries {current} — "
                           "the witness describes a different run (re-sign it)")

    # Falsification: a rewritten anchor must stop verifying. The probe is written outside the repository,
    # because the gate is read-only with respect to the tree (only its own transcripts may be written).
    import tempfile
    probe_dir = tempfile.mkdtemp(prefix="loop_witness_probe_")
    tampered = os.path.join(probe_dir, "tampered_anchor.json")
    try:
        with open(os.path.join(root, PASS_REL), encoding="utf-8") as fh:
            payload = json.load(fh)
        payload["verdict_hash"] = "0000000000000000"
        with open(tampered, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        forged_ok, _ = witness.verify(root, config, anchor_path=tampered)
        if forged_ok:
            reasons.append("a rewritten anchor still verifies: the signature does not bind the anchor's "
                           "contents")
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)

    detail = (f"anchor signed and verified against {ALLOWED_SIGNERS_REL}; rewritten anchor rejected; "
              f"external witness {destination.get('repo')}#{destination.get('issue')}")
    return Dim("D12", "anchor_witness", not reasons, detail if not reasons else "anchor witness invalid",
               reasons=reasons)


def _good_visual_observation(scenario):
    exp = scenario.get("expect") or {}
    dom = "".join(exp.get("require_text") or []) + ("x" * int(exp.get("min_bytes") or 0))
    return {"dom": dom, "screenshot": __file__, "screenshot_bytes": int(exp.get("min_bytes") or 30000) + 1}


def dim_change_health(root):
    """D14: the third verdict — "did it get worse" — exists, is measured, and is not vacuous.

    `check_loop` answers "is the loop disciplined", `check_product` "can a user work with this". Neither
    notices a tree that stays green while quietly losing dimensions, falsification cases, or gaining
    unchecked code. This dimension does not score quality: it requires that countable metrics are compared
    against a recorded baseline, that a regression is refused, and — the part that keeps it honest — that a
    genuinely worse pair IS refused and an improvement is NOT. A comparator that never reddens is a slogan.
    """
    script_rel, policy_rel, baseline_rel = (HEALTH_SCRIPT, HEALTH_POLICY_REL, HEALTH_BASELINE_REL)
    missing = [rel for rel in (script_rel, policy_rel, baseline_rel)
               if not os.path.exists(os.path.join(root, rel))]
    if missing:
        return Dim("D14", "change_health", False, "third verdict missing", reasons=[f"{rel} missing" for rel in missing])

    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("loop_change_health", os.path.join(root, script_rel))
        if spec is None or spec.loader is None:
            raise ImportError("cannot load a module spec for change_health")
        health = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(health)
        policy = health.load_policy(root)
        head = health.collect(root)
        baseline_raw = read(os.path.join(root, baseline_rel)) or ""
        payload = json.loads(baseline_raw)
    except Exception as exc:  # noqa: BLE001
        return Dim("D14", "change_health", False, f"{script_rel} not loadable",
                   reasons=[f"{script_rel}: {exc.__class__.__name__}: {exc}"])

    reasons: list[str] = []
    if payload.get("schema") != health.SCHEMA_VERSION:
        reasons.append(f"{baseline_rel}: schema {payload.get('schema')!r} != {health.SCHEMA_VERSION}")
    baseline = payload.get("metrics") or {}
    if not baseline:
        reasons.append(f"{baseline_rel}: no metrics recorded")
    if not (policy.get("metrics") or {}):
        reasons.append(f"{policy_rel}: no metric policies — nothing would ever be judged")

    for row in health.judge(baseline, head, policy):
        if row["regressed"]:
            reasons.append(f"{row['metric']}: {row['base']} -> {row['head']} is worse than the recorded "
                           f"baseline ({row['why']})")

    # Falsification: the comparator must redden on a worse pair, stay quiet on a better one, and never
    # invent a judgement for a metric it cannot see.
    attacks = [
        ("a lost gate dimension was accepted", {"gate_dims": 14}, {"gate_dims": 13},
         {"gate_dims": {"kind": "must_not_decrease", "why": ""}}, False),
        ("code growth past tolerance was accepted", {"python_lines": 1000}, {"python_lines": 1300},
         {"python_lines": {"kind": "max_growth_pct", "tolerance": 20, "why": ""}}, False),
        ("a new TODO past tolerance was accepted", {"todo_markers": 3}, {"todo_markers": 9},
         {"todo_markers": {"kind": "must_not_increase", "tolerance": 3, "why": ""}}, False),
        ("an improvement was called a regression", {"regression_cases": 20}, {"regression_cases": 26},
         {"regression_cases": {"kind": "must_not_decrease", "why": ""}}, True),
        ("an unchanged metric was called a regression", {"python_lines": 1000}, {"python_lines": 1000},
         {"python_lines": {"kind": "max_growth_pct", "tolerance": 20, "why": ""}}, True),
    ]
    for label, base, after, rules, want_clean in attacks:
        got_clean = not any(r["regressed"] for r in health.judge(base, after, {"metrics": rules}))
        if got_clean != want_clean:
            reasons.append(f"change-health comparator is vacuous: {label}")
    unknown = health.judge({}, head, {"metrics": {"python_lines": {"kind": "must_not_decrease"}}})
    if unknown and unknown[0].get("status") != "unknown":
        reasons.append("change-health invents a verdict for a metric the baseline never recorded")

    detail = (f"{len(policy.get('metrics') or {})} metric policies, baseline {payload.get('recorded_at', '?')}, "
              f"{len(attacks)} falsifications rejected, {len(baseline)} recorded metrics")
    return Dim("D14", "change_health", not reasons, detail if not reasons else "third verdict invalid",
               reasons=reasons)


def dim_visual_gate(root):
    """D13: the visual half of the qa role must exist and must be able to see a defect.

    A page can be technically correct and visually wrong, and no JSON assertion catches that. This dimension
    keeps the visual gate honest: scenarios must state what a user sees and why it matters, a headless
    browser must actually be available (a visual check that silently skips is worse than none), at least one
    scenario must demand a screenshot, and the evaluator must reject falsified observations.
    """
    reasons = []
    for rel in (VISUAL_SCRIPT, VISUAL_SCENARIOS_REL, STATUS_PAGE_SCRIPT):
        if not os.path.exists(os.path.join(root, rel)):
            reasons.append(f"{rel} missing")
    if reasons:
        return Dim("D13", "visual_gate", False, "visual gate incomplete", reasons=reasons)

    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("loop_check_visual",
                                                     os.path.join(root, VISUAL_SCRIPT))
        if spec is None or spec.loader is None:
            raise ImportError("cannot load a module spec for the visual gate")
        visual = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(visual)
        config = visual.load_scenarios(root)
    except Exception as exc:  # noqa: BLE001
        return Dim("D13", "visual_gate", False, f"{VISUAL_SCRIPT} not loadable",
                   reasons=[f"{VISUAL_SCRIPT}: {exc.__class__.__name__}: {exc}"])

    scenarios = config.get("scenarios") or []
    if len(scenarios) < 3:
        reasons.append(f"{VISUAL_SCENARIOS_REL}: {len(scenarios)} scenario(s), need >= 3")
    ids = [s.get("id") for s in scenarios]
    if len(ids) != len(set(ids)):
        reasons.append(f"{VISUAL_SCENARIOS_REL}: duplicate scenario ids in {ids}")
    for sc in scenarios:
        for field in ("id", "title", "page", "expect"):
            if not sc.get(field):
                reasons.append(f"{VISUAL_SCENARIOS_REL}: scenario {sc.get('id', '?')} lacks {field!r}")
        if len((sc.get("why") or "").strip()) < 25:
            reasons.append(f"{VISUAL_SCENARIOS_REL}: scenario {sc.get('id', '?')} has no user-facing 'why'")
    if not any((s.get("expect") or {}).get("screenshot_required") for s in scenarios):
        reasons.append(f"{VISUAL_SCENARIOS_REL}: no scenario demands a screenshot, so nothing here is "
                       "actually visual")
    chrome = visual.chrome_binary()
    if chrome is None:
        reasons.append("no headless Chrome/Chromium on this machine: the visual gate could only ever SKIP")

    falsifications, accepted_good = 0, 0
    for sc in scenarios:
        good = _good_visual_observation(sc)
        if not visual.evaluate(sc, good):
            accepted_good += 1
        exp = sc.get("expect") or {}
        attacks = []
        for needle in exp.get("require_text") or []:
            attacks.append({**good, "dom": good["dom"].replace(needle, "")})
        for needle in exp.get("forbid_text") or []:
            attacks.append({**good, "dom": good["dom"] + needle})
        if exp.get("screenshot_required"):
            attacks.append({**good, "screenshot": "/nonexistent/shot.png"})
        if exp.get("min_bytes"):
            attacks.append({**good, "screenshot_bytes": 10})
        if exp.get("visible_cell_truncation"):
            width = int(exp.get("cell_width", 70))
            attacks.append({**good, "dom": good["dom"] + f"<td>{'y' * width}</td>"})
        for attack in attacks:
            if not visual.evaluate(sc, attack):
                reasons.append(f"{VISUAL_SCENARIOS_REL}: {sc['id']} accepts a falsified page — the check is "
                               "vacuous")
            else:
                falsifications += 1
    if falsifications < 4:
        reasons.append(f"the visual evaluator resisted only {falsifications} falsification(s); need >= 4")
    if accepted_good == 0:
        reasons.append("no visual scenario has a satisfiable observation")

    detail = (f"{len(scenarios)} scenario(s), headless browser {os.path.basename(chrome) if chrome else 'missing'}, "
              f"{falsifications} falsifications rejected, {accepted_good} satisfiable")
    return Dim("D13", "visual_gate", not reasons, detail if not reasons else "visual gate invalid",
               reasons=reasons)


# ---------------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description="Loop-system local gate (read-only).")
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--strict", action="store_true",
                    help="treat SKIP dimensions as failures (this is the project acceptance mode)")
    ap.add_argument("--report", help="write a JSON summary to this path (only write the gate performs)")
    args = ap.parse_args(argv)

    root = os.path.realpath(args.root)
    # The gate's own source and the machine-written report echo gate output verbatim — they are the
    # checker talking about itself, so guardrail scans skip them. Everything else in the tree is scanned.
    skip_paths = {
        os.path.realpath(os.path.abspath(__file__)),
        os.path.realpath(os.path.join(root, REPORT_REL)),
        os.path.realpath(os.path.join(root, PASS_REL)),
    }
    print("LOOP GATE — scripts/check_loop.py (read-only)")
    print(f"root: {root}")
    print(f"mode: {'strict' if args.strict else 'normal'}")
    print()

    def effective_status(dim):
        if dim.skip and not args.strict:
            return "SKIP"
        return "OK" if dim.ok else "FAIL"

    others = [
        dim_contract_skill(root),
        dim_sources_of_truth(root),
        dim_loop_state(root),
        dim_rounds(root),
        dim_no_direct_cli(root, skip_paths),
        dim_no_secrets(root, skip_paths),
        dim_course_corpus(root),
        dim_roles_fanout(root),
        dim_issue_bridge(root),
        dim_product_gate(root),
        dim_anchor_witness(root),
        dim_visual_gate(root),
        dim_change_health(root),
    ]
    # D05 is evaluated last of the group because it must know this run's other verdicts.
    handoff_dim = dim_handoff(root, {d.ident: (d.ok, d.skip) for d in others})
    dims = others[:4] + [handoff_dim] + others[4:]

    cold = cold_start(root)
    failures, skipped, passed, cold_skips, not_applicable = [], [], 0, [], []
    statuses = []
    for dim in dims:
        if dim.skip:
            if dim.ident in NOT_APPLICABLE_DIMS:
                # An input the project does not ship (D08 study corpus) cannot make a tree red: the
                # dimension applies to the operator's own corpus, and saying "not applicable" is the
                # truthful status. It is still printed, so the gap is visible.
                status = "N/A"
                not_applicable.append(dim)
            elif cold and dim.ident in COLD_DIMS:
                # A tree that never ran the loop cannot prove its own history. Saying so is the honest
                # verdict; printing PASS would be a claim, and inventing rounds would be a fake.
                status = "COLD"
                cold_skips.append(dim)
            elif args.strict:
                status = "FAIL"
                failures.append(dim)
            else:
                status = "SKIP"
                skipped.append(dim)
        elif dim.ok:
            status, passed = "OK", passed + 1
        else:
            status = "FAIL"
            failures.append(dim)
        statuses.append(status)
        print(f"{dim.ident} {dim.title:<26} [{status}] {dim.detail}")
        for reason in (dim.reasons if status in {"FAIL", "SKIP"} else []):
            print(f"       - {reason}")

    print()
    ok = not failures and (not args.strict or not skipped)
    marker = MARKER_STOP if not ok else (MARKER_COLD if cold_skips else MARKER_PASS)
    extra = []
    if skipped:
        extra.append(f"{len(skipped)} skip")
    if cold_skips:
        extra.append(f"{len(cold_skips)} cold")
    if not_applicable:
        extra.append(f"{len(not_applicable)} n/a ({', '.join(d.ident for d in not_applicable)})")
    print(f"dims: {passed} ok, {len(failures)} fail" + (", " + ", ".join(extra) if extra else "")
          + f" (mandatory: {len(dims) - len(OPTIONAL_DIMS)}, optional: {len(OPTIONAL_DIMS)})")
    if skipped and not args.strict:
        print("note: an optional dimension was skipped — project acceptance requires "
              "`python3 scripts/check_loop.py --strict` (see PLANS.md)")
    if cold_skips:
        print(f"cold start: {len(cold_skips)} state dimension(s) cannot be proven in this tree "
              f"({', '.join(d.ident for d in cold_skips)}). Run the loop once, then re-check.")
    print(f"acceptance: {'cold-start' if cold_skips and ok else ('strict-complete' if ok and not skipped else ('stage-1' if ok else 'not-accepted'))}")

    if args.report:
        payload = {
            "marker": marker,
            "generated_at": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
            "root": root,
            "strict": args.strict,
            "dimensions": [{"id": d.ident, "name": d.title, "status": statuses[i],
                            "ok": d.ok, "skip": d.skip, "detail": d.detail, "reasons": d.reasons}
                           for i, d in enumerate(dims)],
            "failures": [d.ident for d in failures],
        }
        payload["verdict_hash"] = _verdict_hash(payload)
        try:
            with open(args.report, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
            print(f"report: {os.path.relpath(args.report, root)} verdict_hash={payload['verdict_hash']}")
        except OSError as exc:
            # A read-only sandbox (codex --sandbox read-only, agy --sandbox) can forbid this write. The
            # transcript is a convenience, never a prerequisite: the verdict must still be printed.
            print(f"warning: could not write report ({exc.__class__.__name__}: {exc}); verdict unchanged")

    # D05 binding anchor: written whenever every dimension except D05 is green, because the binding
    # dimension must not certify itself. The handoff must quote this hash.
    if not cold_skips and not [d for d in failures if d.ident != BINDING_DIM]:
        anchor = {
            "marker": MARKER_PASS,
            "generated_at": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
            "all_other_dimensions_ok": True,
            "excluded_from_hash": BINDING_DIM,
            "gate_command": GATE_CMD,
            "dimensions": [{"id": d.ident, "name": d.title, "status": statuses[i],
                            "ok": d.ok, "skip": d.skip}
                           for i, d in enumerate(dims)],
        }
        anchor["verdict_hash"] = _verdict_hash(anchor)
        try:
            with open(os.path.join(root, PASS_REL), "w", encoding="utf-8") as fh:
                json.dump(anchor, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
            print(f"anchor: {PASS_REL} verdict_hash={anchor['verdict_hash']}")
        except OSError as exc:
            # Same rule as the report: an unwritable anchor degrades to a warning. Never let a failed
            # write swallow the marker — a gate that dies after "9 ok" is worse than a gate without a
            # transcript, and a red-but-silent run is indistinguishable from a crash.
            print(f"warning: could not write anchor {PASS_REL} ({exc.__class__.__name__}: {exc}); "
                  f"handoff binding will stay unverifiable until this is writable")

    if ok:
        print(marker)
        return 0
    print(f"FAILURES ({len(failures)}):")
    for i, dim in enumerate(failures, 1):
        print(f"  {i}. {dim.ident} {dim.title}: {dim.detail}")
    print(MARKER_STOP)
    return 1


if __name__ == "__main__":
    sys.exit(main())
