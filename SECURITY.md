# Security

## What this project runs

The gates are read-only by design: they inspect files, run a product's own binary or a headless browser,
and print a verdict. `scripts/check_product.py` starts the target product locally and terminates it;
`scripts/check_visual.py` launches Chrome headless against a local static page. Nothing in the core talks
to the network except optional, explicit steps (`anchor_witness.py publish`, `sync_issues_to_kanban.py`
reading GitHub issues through `gh`).

## Reporting a vulnerability

Open a private security advisory on the repository, or email the maintainers listed in `pyproject.toml`.
Please do not open a public issue for anything that could leak credentials, allow arbitrary command
execution from an untrusted repository profile, or let a check print a PASS marker without running.

## What counts as a vulnerability here

Because the product's value is the verdict, these are security-relevant, not cosmetic:

- a check that prints `..._PASS_...` while its underlying step did not run, was skipped, or failed;
- a guardrail (`D06` no direct CLI-agent invocation, `D07` no credential-shaped strings) that can be
  bypassed by formatting, aliasing or quoting;
- a profile file (`configs/repo_profiles.json`) that can make a gate execute attacker-controlled
  commands without an explicit opt-in;
- the anchor witness (`D12`) accepting a signature over a state other than the one it reports.

## Guardrails you should know about

- `D07` scans repository files for credential-shaped strings. It is a tripwire, not a guarantee: do not
  paste tokens into evidence, round artifacts or PR bodies.
- Evidence artifacts are expected to contain commands and outputs. If a command prints a secret, the
  transcript is the leak — redact before committing, and prefer `--report` paths inside gitignored
  `artifacts/`.
- The sample configs are examples. Replace the identity, the signing key and the external witness repo
  with your own before signing anything real.
