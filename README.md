# loopagent — gates that accept agent work by proof, not by confidence

The repository is `loopagent`; the CLI and the Python package are `loopgate` (the name it grew under).
Same project, two spellings, stated once so nobody hunts for a missing package.

**Accept agent work by proof, not by confidence.** A small gate that refuses to take an agent's word:
it runs the commands, prints a marker, and exits non-zero until the evidence matches the claim.

```
$ python3 scripts/check_loop.py --strict
D01 contract_skill             [OK] contract, marker and the single-door CLI rule are stated
D02 sources_of_truth           [OK] AGENTS.md + PLANS.md + README.md carry the contract
...
dims: 13 ok, 0 fail, 1 n/a (D08) (mandatory: 12, optional: 2)
LOOP_ENGINEERING_PASS_0001
```

No dependencies beyond the Python standard library. Everything runs from a checkout.

## The problem it exists for

An agent now produces a plausible pull request faster than a human can read it. "Tests pass" is the
sentence that ends review, and it is not evidence: the tests may not have run, may have run against the
old tree, may have been skipped, or may cover nothing that changed. The second failure is subtler —
everything is green and the product does not start.

This repository is the discipline that answers both, written as commands:

| Verdict | Question it answers | Command |
|---|---|---|
| technical gate | was the work actually done, on every gate this repository requires? | `python3 scripts/check_loop.py --strict` |
| product gate | can a user do their job with it? | `python3 scripts/check_product.py --strict` |
| change health | did this change make the code worse, even if every gate stayed green? | `python3 scripts/change_health.py --base main` |

Each verdict prints exactly one marker and one exit code. A marker is printed **only** by the gate that
earned it — an agent that prints `..._PASS_...` itself is a bug, and one of the dimensions exists to catch
that class of cheating.

## What it is not

- not a test runner: it does not replace your tests, it demands their transcripts;
- not a linter: it does not judge style, it judges whether the claim matches the run;
- not a framework: no daemon, no database, no schema to migrate — scripts and JSON;
- not a proof of correctness: a gate proves the checks it names actually ran. Nothing more.

## Install

```bash
git clone <this repo> && cd loopagent
python3 scripts/doctor.py              # what this interpreter can run, before a yellow check is a mystery
python3 scripts/bootstrap_state.py --init   # declare the cold start instead of inventing a history
python3 scripts/check_loop.py --strict # -> LOOP_ENGINEERING_COLD_0001: code green, history unprovable
make check                             # what CI runs: compile + code dimensions + regressions
pip install -e ".[contracts]"          # optional: the `loopgate` command + jsonschema for one scenario
loopgate gate --strict
```

Python 3.9+ on paper, 3.12 in CI: one interpreter, so a green badge means "this runs", not "this may
run somewhere". Verify your own version with `python3 scripts/doctor.py`, then `make check`. `loopgate` is a thin dispatcher: each subcommand forwards to the
script of the same name, so you never need the wrapper to use the gates.

## Sixty seconds

```bash
python3 scripts/check_loop.py
#  -> prints every dimension. States that belong to a *run* (rounds, handoff, a signed anchor) are red
#     here, because this tree has never been run: the strict marker is not something you can clone.

python3 scripts/assert_dims.py --require D01 D02 D06 D07 D10 D11 D13
#  -> CI_DIMS_PASS_0001. These dimensions depend only on the code, which is what CI can honestly assert.

python3 scripts/regressions.py
#  -> REGRESSION_SUITE_PASS_0001. The suite injects 26 defects into copies of this repository and proves
#     the gate reddens on the expected dimension each time. A gate nobody attacks is a slogan.
#     Honest accounting: 11 of the 26 target loop *state* (rounds, handoff, the signed anchor) that a
#     fresh clone does not carry, so they print [skip] with the reason — never counted as a pass, never
#     a fail. The other 15 attack code dimensions and must redden here, in any tree.
```

The regression suite is the point of the project. It is easy to write a check that prints OK; it is rare to
prove that the check can fail.

## Wiring it into your repository

1. **Describe your repository's gates** in `configs/repo_profiles.json` (copy
   `configs/repo_profiles.example.json`). The evidence bundle is the machine-readable form of "a worker
   must prove *every* gate this repository requires, not a subset".
2. **Turn issues into cards** with the bundle inside them:

   ```bash
   python3 scripts/sync_issues_to_kanban.py --profile sample-service            # dry run: what it would queue
   python3 scripts/sync_issues_to_kanban.py --profile sample-service --target backlog --apply
   #   -> writes backlog/<key>.md: task spec + mandatory commands + transcript contract, in git
   ```

   `--target kanban` hands the card to the Hermes dispatcher instead (see `AGENTS.md`); `--target backlog`
   needs no execution backend at all. Issues do not have to come from GitHub: `--issues-file issues.json
   --exported-by jira` declares a file the authoritative export of a named tracker, and its idempotency
   keys carry that name (`jira-<owner>-<repo>-<n>`). Without `--exported-by` a file is only a fixture and
   `--apply` is refused; `--target kanban` stays restricted to `--exported-by gh`, because starting
   unattended workers is a claim about real issue numbers.
3. **Check the transcript the worker sends back**:

   ```bash
   python3 scripts/worker_brief.py verify --profile sample-service --evidence /tmp/transcript.md
   #  -> WORKER_EVIDENCE_PASS_0001 only when every mandatory command appears with its own exit code
   ```

The transcript format is deliberately dull and unforgiving: command, verbatim output, `exit=0` on its own
line, per command. A borrowed exit code at the end of the file proves nothing and is rejected.

## Honest comparison

The "evidence bundle for an agent PR" idea is no longer novel, and this project is not the first:

| Project | Focus | What loopgate adds, if anything |
|---|---|---|
| [Pramaan](https://github.com/marketplace/actions/pramaan-evidence-bundle-verifier) | receipt-first verification of a PR, GitHub Action, Rust CLI | our gate also attacks *itself* (26 injections), and it does not only look at the change |
| [Assay](https://haserjian.github.io/assay/) | proof packs + a signed review decision per PR | we sign the **verdict over the loop's state**, not only over an artifact |
| [hashgate](https://pypi.org/project/hashgate/) | fail-closed approval gate bound to a canonical hash | different question: ours is "did the work get proven", theirs is "is this the action I approved" |
| [aelitium](https://github.com/aelitium-dev/aelitium-v3) | offline-verifiable bundles for recorded LLM interactions | ours is about code acceptance, not model interaction records |
| [Backlog.md](https://github.com/MrLesk/Backlog.md) | markdown task ledger in git, three review checkpoints | complementary: `--target backlog` is a ledger; the gate is the proof layer on top |
| [worktrunk](https://github.com/max-sixty/worktrunk) | git worktree lifecycle for parallel agents | complementary: isolation is their job, acceptance is ours |

The three things that are actually ours: **a second verdict about the product** (start it, probe it, kill
it, refuse a bad config — "green CI, broken product" is the failure nobody else gates on), **a third verdict
about the direction of the tree** (a change that stays green while losing gate dimensions or gaining
untested surface is refused, because "nothing broke" and "nothing got worse" are different sentences), and
**a gate that proves it can fail**. If those do not matter to you, use one of the projects above.

## Limits, stated plainly

- **The strict marker is not reproducible from a clone, and a clone says so.** Round artifacts, the
  handoff and the signed anchor live in gitignored state, because they are evidence of *a run*, not
  source. `scripts/bootstrap_state.py --init` writes a state that declares `cold`; the gate prints those
  dimensions as COLD and answers `LOOP_ENGINEERING_COLD_0001` — never PASS. As soon as one round exists
  the declaration is refused, so it cannot hide a broken loop behind an empty directory.
- **The product gate needs a product.** It is fail-closed: no product path → STOP, not a silent PASS. Point
  it at your own service with `--repo-path` or the profile's `local_path_env`.
- **The visual gate needs Chrome.** Headless Chrome/Chromium is required; absent → the check fails loudly
  rather than skipping quietly. The paths are macOS-first (`/Applications/...`) — see `scripts/check_visual.py`.
- **Money is not measured.** Token and call counts are readable per run; a price is not, because most
  agent backends do not report it.
- **`AGENTS.md`, `PLANS.md` and the skill under `.agents/` are the author's operator notes (Russian).**
  They are shipped because they are the contract this gate grew out of; the tool itself is documented in
  English.

## Layout

```
scripts/check_loop.py           technical gate: 14 dimensions, STOP/PASS marker, read-only
scripts/change_health.py        third verdict: countable metrics against a baseline or a git ref
scripts/check_product.py        product gate: user-facing scenarios with a stated consequence
scripts/regressions.py          26 injections: proves the gate reddens on real defects
scripts/worker_brief.py         renders/verifies the worker brief (evidence bundle inside)
scripts/sync_issues_to_kanban.py issues -> queue (--target kanban | backlog)
scripts/anchor_witness.py       signs the verdict and publishes its digest to an external journal
scripts/loop_watch.py           fingerprints the gate; silent unless something changed (cron-friendly)
scripts/export_public.py        assembles a publication-ready tree, fail-closed
scripts/assert_dims.py          CI: assert named dimensions are OK
configs/                        repository profiles, scenarios, rules, example identities
loopgate/                       the `loopgate` command: names for the scripts above
```

## License

MIT. See `LICENSE`.
