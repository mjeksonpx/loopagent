# One entry point for every gate. `make gate` is the same command CI runs.
# PYTHON is overridable so a machine with a broken stdlib interpreter can point at a good one:
#   make gate PYTHON=/opt/homebrew/bin/python3.14

PYTHON ?= python3

.PHONY: help doctor guard ledger health gate product visual regress check ci export test clean

help:
	@echo "make doctor    what this interpreter can run (jsonschema, Chrome, gh, git)"
	@echo "make guard     stop cards over their budget -> CARD_BUDGET_PASS_0001"
	@echo "make ledger    render finished cards into backlog/ -> TASK_LEDGER_PASS_0001"
	@echo "make health    third verdict: did anything get worse -> CHANGE_HEALTH_PASS_0001"
	@echo "make gate      technical gate (strict)     -> LOOP_ENGINEERING_PASS_0001"
	@echo "make product   product acceptance gate     -> PRODUCT_ACCEPTANCE_PASS_0001"
	@echo "make visual    visual check (headless Chrome)"
	@echo "make regress   the gate's own regression suite (injects defects)"
	@echo "make check     what CI runs: compile + code dimensions + regressions"
	@echo "make export    build a publication-ready tree under dist/public"
	@echo "make clean     remove build and export byproducts"

doctor:
	$(PYTHON) scripts/doctor.py

guard:
	$(PYTHON) scripts/guard_card.py

ledger:
	$(PYTHON) scripts/render_task_ledger.py --all-done

health:
	$(PYTHON) scripts/change_health.py

gate:
	$(PYTHON) scripts/check_loop.py --strict

product:
	$(PYTHON) scripts/check_product.py

visual:
	$(PYTHON) scripts/check_visual.py

regress:
	$(PYTHON) scripts/regressions.py

check:
	$(PYTHON) -m compileall -q loopgate scripts
	$(PYTHON) scripts/assert_dims.py --require D01 D02 D06 D07 D10 D11 D13
	$(PYTHON) scripts/regressions.py
	$(PYTHON) scripts/review_pr.py --selftest

ci: check

export:
	$(PYTHON) scripts/export_public.py --out dist/public --git-init

test: check

clean:
	rm -rf dist build *.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
