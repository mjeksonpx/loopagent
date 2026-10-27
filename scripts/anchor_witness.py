#!/usr/bin/env python3
"""Anchor witness — the gate's verdict leaves the machine, so a silent rewrite is detectable.

The residual risk this closes (``risk=4`` in ``artifacts/loop_state.json``)
-----------------------------------------------------------------------
Every input the technical gate reads is authored on this machine, so a fabricated anchor is *detectable by
a command* but not *impossible*. Two things change that:

1. **Signature.** The binding anchor (``artifacts/loop_gate_pass.json``) is signed with a dedicated,
   purpose-limited SSH key (``~/.ssh/loop_anchor_ed25519``; its public half is committed in
   ``configs/allowed_signers``). Any later edit of the anchor without that key fails verification, so the
   anchor cannot be quietly re-minted to describe a run that did not happen.
2. **External timestamp.** ``publish`` writes the anchor digest + signature as a comment on a GitHub issue,
   where the timestamp is the platform's, not ours. A coordinated rewrite now has to contradict a record we
   do not control.

Honest boundary: the signing key lives on the same machine, so the signature alone proves "written by the
holder of this key", not "written by an independent party". The external publication is what makes a
*coordinated* rewrite visible. The gate checks the signature locally (no network); ``check-external`` is the
operator-run step that verifies the published copy.

Usage
-----
    python3 scripts/anchor_witness.py sign              # sign the current anchor
    python3 scripts/anchor_witness.py verify            # local: signature over the current anchor
    python3 scripts/anchor_witness.py publish           # post digest+signature to the witness log
    python3 scripts/anchor_witness.py check-external    # read the log back and compare digests
    python3 scripts/anchor_witness.py status
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANCHOR_REL = "artifacts/loop_gate_pass.json"
VERDICT_REL = "artifacts/loop_gate_verdict.txt"
# ssh-keygen writes <message>.sig next to the message, so the signature lives beside the signed payload.
SIG_REL = "artifacts/loop_gate_verdict.txt.sig"
WITNESS_REL = "artifacts/anchor_witness.json"
CONFIG_REL = "configs/anchor_witness.json"
PASS_MARKER = "ANCHOR_WITNESS_PASS_0001"
STOP_MARKER = "ANCHOR_WITNESS_STOP_0001"


def load_config(root: str = ROOT) -> dict:
    with open(os.path.join(root, CONFIG_REL), encoding="utf-8") as fh:
        return json.load(fh)


def digest_of(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def verdict_payload(anchor_path: str) -> bytes:
    """What gets signed: the verdict, not the file.

    The anchor carries a fresh ``generated_at`` on every run, so signing the file would invalidate the
    witness after each gate run. The verdict (marker + verdict hash) is stable while the verdict set is
    stable, which is exactly what a witness should pin.
    """
    with open(anchor_path, encoding="utf-8") as fh:
        payload = json.load(fh)
    return f"marker={payload.get('marker')}\nverdict_hash={payload.get('verdict_hash')}\n".encode()


def sign(root: str, config: dict) -> tuple[bool, str]:
    anchor = os.path.join(root, ANCHOR_REL)
    sig = os.path.join(root, SIG_REL)
    message = os.path.join(root, VERDICT_REL)
    key = os.path.expanduser(config["signing_key"])
    with open(message, "wb") as fh:
        fh.write(verdict_payload(anchor))
    # ssh-keygen -Y sign silently keeps an existing signature file (exit 0, old signature left in place),
    # so a stale signature would verify the *previous* verdict while looking freshly signed. Remove it first.
    if os.path.exists(sig):
        os.remove(sig)
    proc = subprocess.run(["ssh-keygen", "-Y", "sign", "-f", key, "-n", config["namespace"], message],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        return False, f"ssh-keygen sign failed: {proc.stderr.strip()[:200]}"
    stale = os.path.join(root, "artifacts", "loop_gate_pass.json.sig")
    if os.path.exists(stale):  # an earlier file-based signature would shadow the verdict one
        os.remove(stale)
    record = {"anchor": ANCHOR_REL, "signed_message": VERDICT_REL,
              "verdict_hash": json.load(open(anchor, encoding="utf-8")).get("verdict_hash"),
              "payload_sha256": digest_of(message), "namespace": config["namespace"],
              "identity": config["identity"], "signature_file": SIG_REL,
              "signer_fingerprint": signer_fingerprint(key)}
    with open(os.path.join(root, WITNESS_REL), "w", encoding="utf-8") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return True, (f"signed the verdict of {ANCHOR_REL} -> {SIG_REL} "
                  f"(verdict_hash {record['verdict_hash']}, payload sha256 {record['payload_sha256'][:16]}…)")


def signer_fingerprint(key: str) -> str:
    proc = subprocess.run(["ssh-keygen", "-l", "-f", key + ".pub"], capture_output=True, text=True)
    parts = proc.stdout.split()
    return parts[1] if len(parts) > 1 else "unknown"


def verify(root: str, config: dict, anchor_path: str | None = None, sig_path: str | None = None) -> tuple[bool, str]:
    """Verify the signature over the *verdict* reconstructed from the anchor on disk.

    Passing a doctored anchor (``anchor_path``) changes the reconstructed message, so a rewrite cannot ride
    on an old signature.
    """
    anchor = anchor_path or os.path.join(root, ANCHOR_REL)
    sig = sig_path or os.path.join(root, SIG_REL)
    signers = os.path.join(root, config["allowed_signers"])
    for path in (anchor, sig, signers):
        if not os.path.exists(path):
            return False, f"{os.path.relpath(path, root)} missing (run `sign` first)"
    proc = subprocess.run(["ssh-keygen", "-Y", "verify", "-f", signers, "-I", config["identity"],
                           "-n", config["namespace"], "-s", sig],
                          input=verdict_payload(anchor), capture_output=True)
    if proc.returncode != 0:
        note = (proc.stdout or proc.stderr or b"").decode("utf-8", "replace").strip()
        return False, note[:200] or "signature does not match this verdict"
    return True, (proc.stdout or b"").decode("utf-8", "replace").strip() + \
        f" (verdict_hash {json.load(open(anchor, encoding='utf-8')).get('verdict_hash')})"


def gh(args: list[str], token: str | None = None) -> tuple[int, str]:
    env = dict(os.environ)
    if token:
        env["GH_TOKEN"] = token
    proc = subprocess.run(["gh"] + args, capture_output=True, text=True, env=env)
    return proc.returncode, (proc.stdout or proc.stderr).strip()


def github_token() -> str | None:
    """The token lives in the operator's env or in ~/.dsh/.env; never echoed."""
    if os.environ.get("GH_TOKEN"):
        return os.environ["GH_TOKEN"]
    env_file = os.path.expanduser("~/.dsh/.env")
    if os.path.exists(env_file):
        for line in open(env_file, encoding="utf-8"):
            if line.startswith(("GITHUB_TOKEN=", "GH_TOKEN=")):
                return line.split("=", 1)[1].strip().strip('"')
    return None


def publish(root: str, config: dict, verbose: bool = False) -> tuple[bool, str]:
    dest = config["destination"]
    repo, issue = dest["repo"], str(dest["issue"])
    witness = json.load(open(os.path.join(root, WITNESS_REL), encoding="utf-8"))
    body = (f"<!-- {config['marker']} -->\n"
            f"**{config['marker']}**\n\n"
            f"- verdict_hash: `{witness['verdict_hash']}`\n"
            f"- signed message: `{witness['signed_message']}` (sha256 `{witness['payload_sha256']}`)\n"
            f"- signer: `{witness['signer_fingerprint']}` ({witness['identity']}, namespace "
            f"`{witness['namespace']}`)\n"
            f"- signature: `{witness['signature_file']}` (verifiable against `{config['allowed_signers']}`)\n")
    token = github_token()
    code, existing = gh(["api", f"repos/{repo}/issues/{issue}/comments", "--paginate",
                         "--jq", ".[].body"], token)
    if code != 0:
        return False, f"cannot read the witness log: {existing[:160]}"
    if witness["verdict_hash"] in existing:
        return True, f"already published to {repo}#{issue} (verdict already witnessed, nothing to do)"
    code, out = gh(["issue", "comment", issue, "-R", repo, "--body", body], token)
    if code != 0:
        return False, f"cannot publish: {out[:160]}"
    return True, f"published to {repo}#{issue}: {out.strip()}"


def check_external(root: str, config: dict) -> tuple[bool, str]:
    dest = config["destination"]
    witness = json.load(open(os.path.join(root, WITNESS_REL), encoding="utf-8"))
    code, out = gh(["api", f"repos/{dest['repo']}/issues/{dest['issue']}/comments", "--paginate",
                    "--jq", ".[].body"], github_token())
    if code != 0:
        return False, f"cannot read the witness log: {out[:160]}"
    if witness["verdict_hash"] not in out:
        return False, (f"the current verdict {witness['verdict_hash']} is not published in "
                       f"{dest['repo']}#{dest['issue']} — run `publish`")
    current = json.load(open(os.path.join(root, ANCHOR_REL), encoding="utf-8")).get("verdict_hash")
    if current != witness["verdict_hash"]:
        return False, (f"the anchor now carries verdict {current} but the published witness describes "
                       f"{witness['verdict_hash']} — re-sign and re-publish")
    return True, f"witnessed: {dest['repo']}#{dest['issue']} carries verdict {witness['verdict_hash']}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Anchor witness: sign the gate anchor, publish its digest.")
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--verify-anchor", help="verify this anchor file instead of the current one (attack mode)")
    ap.add_argument("--verify-signature", help="verify this signature file instead of the current one")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sign")
    sub.add_parser("verify")
    sub.add_parser("publish")
    sub.add_parser("check-external")
    sub.add_parser("status")
    args = ap.parse_args(argv)

    try:
        config = load_config(args.root)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        print(STOP_MARKER)
        return 2

    if args.cmd == "sign":
        ok, note = sign(args.root, config)
    elif args.cmd == "verify":
        ok, note = verify(args.root, config, args.verify_anchor, args.verify_signature)
    elif args.cmd == "publish":
        ok, note = publish(args.root, config)
    elif args.cmd == "check-external":
        ok, note = check_external(args.root, config)
    else:
        anchor = os.path.join(args.root, ANCHOR_REL)
        witness_path = os.path.join(args.root, WITNESS_REL)
        note = (f"anchor {'present' if os.path.exists(anchor) else 'missing'}, "
                f"signature {'present' if os.path.exists(os.path.join(args.root, SIG_REL)) else 'missing'}, "
                f"witness {'present' if os.path.exists(witness_path) else 'missing'}; "
                f"destination {config['destination']['repo']}#{config['destination']['issue']}")
        ok = True
        if os.path.exists(witness_path):
            note += f"; signed verdict {json.load(open(witness_path))['verdict_hash']}"
    print(note)
    print(PASS_MARKER if ok else STOP_MARKER)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
