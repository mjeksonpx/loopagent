#!/usr/bin/env python3
"""Product acceptance gate (PLANS.md stage 7) — the user-facing criterion, kept separate from the tech gate.

Why a second gate
-----------------
``scripts/check_loop.py`` answers "is this loop disciplined?" — contracts, evidence, state, guardrails.
It cannot answer "can a user do their job with this product?", and merging the two lets a green technical
gate hide a product that does not start. The chief accepts by this gate; the tech gate stays a separate
verdict with a separate owner.

Contract
--------
* Every scenario in ``configs/product_scenarios.json`` states what a user/operator observes (HTTP status,
  body marker, exit code, shutdown time) and a ``why`` line naming the user-facing consequence.
* A scenario whose prerequisite is missing is **SKIP with the reason**, never a silent pass; ``--strict``
  turns SKIP into FAIL, mirroring the tech gate.
* Prints ``PRODUCT_ACCEPTANCE_PASS_0001`` (exit 0) or ``PRODUCT_ACCEPTANCE_STOP_0001`` (exit 1) and writes
  a machine report.
* ``evaluate(scenario, observation)`` is pure: the tech gate (D11) attacks it with falsified observations
  without starting a product process.

Usage
-----
    python3 scripts/check_product.py --list
    python3 scripts/check_product.py                 # normal mode: optional scenarios may SKIP
    python3 scripts/check_product.py --strict        # SKIP becomes FAIL (product acceptance)
    python3 scripts/check_product.py --scenario P06 --verbose
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PASS_MARKER = "PRODUCT_ACCEPTANCE_PASS_0001"
STOP_MARKER = "PRODUCT_ACCEPTANCE_STOP_0001"
SCENARIOS_REL = "configs/product_scenarios.json"
PROFILES_REL = "configs/repo_profiles.json"
READINESS_TIMEOUT_S = 8.0
DEFAULT_REPORT = "artifacts/product_acceptance_report.json"


# ---------------------------------------------------------------- config

def load_scenarios(root: str = ROOT) -> dict:
    with open(os.path.join(root, SCENARIOS_REL), encoding="utf-8") as fh:
        return json.load(fh)


def load_profile(root: str, name: str) -> dict:
    with open(os.path.join(root, PROFILES_REL), encoding="utf-8") as fh:
        profiles = json.load(fh)["profiles"]
    if name not in profiles:
        raise KeyError(f"unknown repo profile {name!r}; known: {', '.join(sorted(profiles))}")
    return profiles[name]


def repo_path(root: str, profile: dict) -> str:
    """Where the product's source lives: the profile's env var, $PRODUCT_REPO_PATH, or its clone.

    The env override exists so a clone can carry a profile without carrying someone else's absolute paths:
    the shipped example declares `local_path_env` and leaves `local_path` null.
    """
    env_name = profile.get("local_path_env")
    return (os.environ.get(env_name) if env_name else None) \
        or os.environ.get("PRODUCT_REPO_PATH") \
        or profile.get("local_path") \
        or ""


# ---------------------------------------------------------------- evaluation (pure)

def evaluate(scenario: dict, observation: dict) -> list[str]:
    """Reasons the observation violates the scenario; empty list = the scenario passed.

    Pure on purpose: this is what the technical gate attacks with falsified observations, so the product
    gate cannot degrade into a script that only ever prints PASS.
    """
    exp = scenario.get("expect") or {}
    reasons: list[str] = []
    kind = scenario.get("kind")

    if kind in {"http", "signal"}:
        if "status" in exp:
            got = observation.get("status")
            if got != exp["status"]:
                reasons.append(f"expected HTTP {exp['status']}, got {got!r} on {scenario.get('path')}")
        if exp.get("json_ok_true"):
            body = observation.get("body") or ""
            try:
                payload = json.loads(body)
            except (TypeError, ValueError):
                reasons.append(f"expected a JSON body with ok=true, got {body[:60]!r}")
            else:
                if payload.get("ok") is not True:
                    reasons.append(f"expected ok=true in the body, got {payload!r}")
        if "body_contains" in exp and exp["body_contains"] not in (observation.get("body") or ""):
            reasons.append(f"body does not contain {exp['body_contains']!r}")
        if "exit_code" in exp and observation.get("exit_code") != exp["exit_code"]:
            reasons.append(f"expected exit code {exp['exit_code']}, got {observation.get('exit_code')!r}")
        if "max_shutdown_ms" in exp:
            got = observation.get("shutdown_ms")
            if got is None:
                reasons.append("shutdown time was not measured")
            elif got > exp["max_shutdown_ms"]:
                reasons.append(f"shutdown took {got} ms, want at most {exp['max_shutdown_ms']} ms")

    elif kind == "startup_failure":
        code = observation.get("exit_code")
        if exp.get("exit_nonzero") and (code == 0 or code is None):
            reasons.append(f"expected a non-zero exit for an invalid configuration, got {code!r}")
        needle = exp.get("stderr_contains")
        if needle and needle not in (observation.get("stderr") or ""):
            reasons.append(f"the error message does not name {needle!r}")

    elif kind == "json_schema":
        if observation.get("errors"):
            reasons.append(f"the validator could not judge {len(observation['errors'])} fixture(s): "
                           f"{observation['errors'][:2]}")
        if exp.get("valid_fixtures_validate") and observation.get("valid_failures"):
            reasons.append(f"valid fixtures rejected: {observation['valid_failures'][:3]}")
        if exp.get("invalid_fixtures_rejected") and observation.get("invalid_accepted"):
            reasons.append(f"invalid fixtures accepted: {observation['invalid_accepted'][:3]}")

    else:
        reasons.append(f"unknown scenario kind {kind!r}")

    return reasons


# ---------------------------------------------------------------- process control

def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def port_env(scenario: dict, ports: dict) -> dict:
    return dict(ports)


def build_product(path: str) -> tuple[bool, str]:
    """Build the product binary if it is missing. Returns (ok, note)."""
    binary = os.path.join(path, "bin", "sample-service")
    if os.path.exists(binary):
        return True, f"using {os.path.relpath(binary, path)}"
    os.makedirs(os.path.dirname(binary), exist_ok=True)
    proc = subprocess.run(["go", "build", "-mod=vendor", "-o", binary, "./cmd"],
                          cwd=path, capture_output=True, text=True)
    if proc.returncode != 0:
        return False, f"go build failed: {proc.stderr.strip()[:200]}"
    return True, "built bin/sample-service"


def start_process(path: str, env: dict, ports: dict) -> subprocess.Popen:
    full_env = {k: str(v) for k, v in env.items()}
    # Ports come from the caller unless the scenario deliberately sets one (that is how the
    # startup_failure scenario injects an invalid value instead of a free port).
    full_env.setdefault("HTTP_SERVER_LISTEN_PORT", str(ports["api"]))
    full_env.setdefault("HTTP_SERVER_PROBES_PORT", str(ports["probes"]))
    full_env.setdefault("HTTP_SERVER_METRICS_PORT", str(ports["metrics"]))
    full_env.setdefault("PATH", os.environ.get("PATH", "/usr/bin:/bin"))
    return subprocess.Popen([os.path.join(path, "bin", "sample-service")], cwd=path, env=full_env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def wait_for_port(port: int, proc: subprocess.Popen, timeout: float = READINESS_TIMEOUT_S) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                return True
        except OSError:
            time.sleep(0.15)
    return False


def http_get(port: int, path: str, timeout: float = 4.0) -> dict:
    import urllib.error
    import urllib.request
    url = f"http://127.0.0.1:{port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - localhost only
            return {"status": resp.status, "body": resp.read().decode("utf-8", "replace")}
    except urllib.error.HTTPError as exc:
        return {"status": exc.code, "body": exc.read().decode("utf-8", "replace")}
    except Exception as exc:  # noqa: BLE001 - a refused connection is an observation, not a crash
        return {"status": None, "error": f"{exc.__class__.__name__}: {exc}"}


# ---------------------------------------------------------------- runners

def run_http_group(scenarios: list[dict], path: str, env: dict, verbose: bool) -> dict[str, dict]:
    """Start the product once and observe every http/signal scenario against that process."""
    ports = {"api": free_port(), "probes": free_port(), "metrics": free_port()}
    proc = start_process(path, env, ports)
    observations: dict[str, dict] = {}
    try:
        ready_port = ports["probes"]
        if not wait_for_port(ready_port, proc):
            note = f"the process did not bind the probes port within {READINESS_TIMEOUT_S:.0f}s"
            for sc in scenarios:
                observations[sc["id"]] = {"startup_failed": note, "status": None, "exit_code": proc.poll()}
            return observations
        for sc in scenarios:
            if sc["kind"] == "http":
                observations[sc["id"]] = http_get(ports[sc["port"]], sc["path"])
                if verbose:
                    print(f"      {sc['id']} {sc['path']} -> {observations[sc['id']].get('status')}")
            elif sc["kind"] == "signal":
                started = time.time()
                proc.terminate()
                try:
                    code = proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    code = proc.wait(timeout=5)
                observations[sc["id"]] = {"exit_code": code,
                                          "shutdown_ms": round((time.time() - started) * 1000)}
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
    return observations


def run_startup_failure(scenario: dict, path: str, env: dict, verbose: bool) -> dict:
    merged = dict(env)
    merged.update(scenario.get("env") or {})
    ports = {"api": free_port(), "probes": free_port(), "metrics": free_port()}
    proc = start_process(path, merged, ports)
    try:
        code = proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        code = None
    stderr = (proc.stderr.read() if proc.stderr else "") or ""
    if verbose:
        print(f"      {scenario['id']} exit={code} stderr={stderr.strip()[:120]}")
    return {"exit_code": code, "stderr": stderr}


def run_json_schema(scenario: dict, path: str, verbose: bool) -> dict:
    """Validate the published contracts against their own fixtures.

    The v1 fixtures are *bundles* (context, plan, result, manifest, worker_events, integration_events), so a
    fixture is only meaningfully judged when each member is validated against its own published schema — the
    version pins live in those member schemas as ``const``, which is exactly what the invalid fixtures break.
    """
    try:
        import jsonschema
        import referencing
        import referencing.jsonschema
    except ImportError as exc:
        # "Not installed" and "installed but broken" are different problems with different fixes, and
        # conflating them sends the reader looking for a package that is already there (rpds-py ships as
        # a binary wheel; a damaged install raises ModuleNotFoundError from inside jsonschema itself).
        detail = str(exc)
        if "rpds" in detail or "referencing" in detail:
            return {"skip": f"python:jsonschema is installed but damaged ({detail}); repair with "
                            "`python3 -m pip install --force-reinstall rpds-py referencing`"}
        return {"skip": f"python:jsonschema is not installed ({detail})"}

    schema_dir = os.path.join(path, os.path.dirname(scenario["schema"]))
    schemas, resources = {}, []
    for name in sorted(os.listdir(schema_dir)):
        if not name.endswith(".schema.json"):
            continue
        doc = json.load(open(os.path.join(schema_dir, name), encoding="utf-8"))
        schemas[name] = doc
        for uri in filter(None, (name, doc.get("$id"))):
            resources.append((uri, referencing.Resource.from_contents(
                doc, default_specification=referencing.jsonschema.DRAFT202012)))
    registry = referencing.Registry().with_resources(resources)
    members = scenario.get("members") or {}

    valid_failures, invalid_accepted, errors = [], [], []
    for folder, should_pass in ((scenario["fixtures_valid"], True), (scenario["fixtures_invalid"], False)):
        folder_path = os.path.join(path, folder)
        names = sorted(n for n in os.listdir(folder_path) if n.endswith(".json")) \
            if os.path.isdir(folder_path) else []
        for name in names:
            bundle = json.load(open(os.path.join(folder_path, name), encoding="utf-8"))
            rejected: list[str] = []
            for member, schema_name in members.items():
                is_list = member.endswith("[]")
                key = member[:-2] if is_list else member
                value = bundle.get(key)
                if value is None:
                    continue
                items = value if is_list and isinstance(value, list) else [value]
                for index, item in enumerate(items):
                    if not isinstance(item, dict):
                        continue
                    where = f"{member}[{index}]" if is_list else member
                    try:
                        jsonschema.validate(item, schemas[schema_name], registry=registry)
                    except jsonschema.ValidationError as exc:
                        rejected.append(f"{where}: {exc.message[:70]}")
                    except Exception as exc:  # noqa: BLE001 - a resolver error is a failed check
                        errors.append(f"{name}:{where}: {exc.__class__.__name__}: {str(exc)[:70]}")
            if verbose:
                print(f"      {scenario['id']} {folder}/{name} rejected_members={len(rejected)} "
                      f"(want {'0' if should_pass else '>0'})")
            if should_pass and rejected:
                valid_failures.append(f"{name} ({rejected[0]})")
            if not should_pass and not rejected:
                invalid_accepted.append(name)
    return {"valid_failures": valid_failures, "invalid_accepted": invalid_accepted, "errors": errors}


# ---------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Product acceptance gate (user-facing scenarios).")
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--repo-path", help="path to the product source (default: the profile's local_path)")
    ap.add_argument("--list", action="store_true", help="print the scenario inventory and exit")
    ap.add_argument("--scenario", action="append", help="run only these scenario ids (repeatable)")
    ap.add_argument("--strict", action="store_true", help="treat SKIP as FAIL (product acceptance)")
    ap.add_argument("--report", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    try:
        config = load_scenarios(args.root)
        profile = load_profile(args.root, config["profile"])
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        print(STOP_MARKER)
        return 2

    scenarios = config["scenarios"]
    if args.list:
        print(f"product: {config['product']}  ({len(scenarios)} scenarios, "
              f"{sum(1 for s in scenarios if s.get('optional'))} optional)")
        for sc in scenarios:
            flag = "optional" if sc.get("optional") else "mandatory"
            print(f"  {sc['id']}  [{flag}] {sc['kind']:<16} {sc['title']}")
        return 0

    if args.scenario:
        wanted = set(args.scenario)
        scenarios = [s for s in scenarios if s["id"] in wanted]
        if not scenarios:
            print(f"no scenario matches {sorted(wanted)}", file=sys.stderr)
            return 2

    path = args.repo_path or repo_path(args.root, profile)
    print("PRODUCT ACCEPTANCE GATE — scripts/check_product.py")
    print(f"product: {config['product']}  source: {path or '(not configured)'}")
    print(f"mode: {'strict' if args.strict else 'normal'}")
    print()

    if not path or not os.path.isdir(path):
        print(f"[FAIL] the product source is missing: {path!r} "
              f"(set local_path in {PROFILES_REL} or pass --repo-path)")
        print(STOP_MARKER)
        return 1

    ok, note = build_product(path)
    print(f"build: {note}")
    if not ok:
        print(f"[FAIL] {note}")
        print(STOP_MARKER)
        return 1
    print()

    results: list[dict] = []
    http_group = [s for s in scenarios if s["kind"] in {"http", "signal"}]
    observations: dict[str, dict] = {}
    if http_group:
        observations.update(run_http_group(http_group, path, config["env_base"], args.verbose))

    for sc in scenarios:
        if sc["kind"] in {"http", "signal"}:
            obs = observations.get(sc["id"], {})
        elif sc["kind"] == "startup_failure":
            obs = run_startup_failure(sc, path, config["env_base"], args.verbose)
        elif sc["kind"] == "json_schema":
            obs = run_json_schema(sc, path, args.verbose)
        else:
            obs = {"skip": f"unknown kind {sc['kind']!r}"}

        skip_reason = obs.get("skip")
        if not skip_reason and obs.get("startup_failed"):
            skip_reason = None  # a product that does not start is a FAIL, not a skip
        reasons = [] if skip_reason else evaluate(sc, obs)
        if not reasons and obs.get("startup_failed"):
            reasons = [obs["startup_failed"]]
        status = "SKIP" if skip_reason else ("OK" if not reasons else "FAIL")
        if skip_reason and args.strict:
            status = "FAIL"
            reasons = [f"prerequisite missing: {skip_reason}"]
        results.append({"id": sc["id"], "title": sc["title"], "kind": sc["kind"],
                        "status": status, "reasons": reasons, "observation": obs,
                        "optional": bool(sc.get("optional"))})
        print(f"{sc['id']} [{status}] {sc['title']}")
        if skip_reason:
            print(f"       - SKIP: {skip_reason}")
        for reason in reasons:
            print(f"       - {reason}")

    passed = sum(1 for r in results if r["status"] == "OK")
    failed = [r for r in results if r["status"] == "FAIL"]
    skipped = [r for r in results if r["status"] == "SKIP"]
    ok = not failed
    print()
    print(f"scenarios: {passed} ok, {len(failed)} fail, {len(skipped)} skip "
          f"(mandatory: {sum(1 for s in scenarios if not s.get('optional'))}, "
          f"optional: {sum(1 for s in scenarios if s.get('optional'))})")
    print(f"verdict: {'product accepted' if ok else 'product NOT accepted'}")

    report = {
        "marker": PASS_MARKER if ok else STOP_MARKER,
        "product": config["product"],
        "source": path,
        "strict": args.strict,
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "scenarios": [{k: v for k, v in r.items() if k != "observation"} | {"observation": r["observation"]}
                      for r in results],
        "failures": [r["id"] for r in failed],
        "skips": [r["id"] for r in skipped],
    }
    report_path = args.report or config.get("report") or DEFAULT_REPORT
    if not os.path.isabs(report_path):
        report_path = os.path.join(args.root, report_path)
    try:
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        print(f"report: {os.path.relpath(report_path, args.root)}")
    except OSError as exc:
        print(f"warning: could not write the report ({exc.__class__.__name__}: {exc}); verdict unchanged")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    if ok:
        print(PASS_MARKER)
        return 0
    print(f"FAILURES ({len(failed)}):")
    for i, r in enumerate(failed, 1):
        print(f"  {i}. {r['id']} {r['title']}: {r['reasons'][0] if r['reasons'] else 'see report'}")
    print(STOP_MARKER)
    return 1


if __name__ == "__main__":
    sys.exit(main())
