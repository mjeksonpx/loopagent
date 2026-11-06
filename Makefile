# One entry point for every gate. `make gate` is the same command CI runs.
# PYTHON is overridable so a machine with a broken stdlib interpreter can point at a good one:
#   make gate PYTHON=/opt/homebrew/bin/python3.14

PYTHON ?= python3

.PHONY: help doctor guard ledger health gate product visual parity regress check ci export test clean

help:
	@echo "make doctor    what this interpreter can run (jsonschema, Chrome, gh, git)"
	@echo "make guard     stop cards over their budget -> CARD_BUDGET_PASS_0001"
	@echo "make ledger    render finished cards into backlog/ -> TASK_LEDGER_PASS_0001"
	@echo "make health    third verdict: did anything get worse -> CHANGE_HEALTH_PASS_0001"
	@echo "make gate      technical gate (strict)     -> LOOP_ENGINEERING_PASS_0001"
	@echo "make product   product acceptance gate     -> PRODUCT_ACCEPTANCE_PASS_0001"
	@echo "make visual    visual check (headless Chrome)"
	@echo "make parity    two checkouts must not drift -> TREE_PARITY_PASS_0001 (POLICY=<json>)"
	@echo "make regress   the gate's own regression suite (injects defects)"
	@echo "make check     what CI runs: compile + code dimensions + regressions"
	@echo "make export    build a publication-ready tree under dist/public"
	@echo "make clean     remove build and export byproducts"

doctor:
	$(PYTHON) -m loopgate.gates.doctor

guard:
	$(PYTHON) -m loopgate.gates.guard_card

ledger:
	$(PYTHON) -m loopgate.gates.render_task_ledger --all-done

health:
	$(PYTHON) -m loopgate.gates.change_health

gate:
	$(PYTHON) -m loopgate.gates.check_loop --strict

product:
	$(PYTHON) -m loopgate.gates.check_product

visual:
	$(PYTHON) -m loopgate.gates.check_visual

parity:
	@test -n "$(POLICY)" || { echo "make parity needs POLICY=<json>; see configs/tree_parity.example.json"; exit 2; }
	$(PYTHON) -m loopgate.gates.tree_parity --policy $(POLICY)

regress:
	$(PYTHON) -m loopgate.gates.regressions

check:
	$(PYTHON) -m compileall -q loopgate
	$(PYTHON) -m loopgate.gates.assert_dims --require D01 D02 D06 D07 D10 D11 D13
	$(PYTHON) -m loopgate.gates.regressions
	$(PYTHON) -m loopgate.gates.review_pr --selftest
	$(PYTHON) -m loopgate.gates.guard_card --selftest
	$(PYTHON) -m loopgate.gates.tree_parity --selftest
	@if [ -f configs/tree_parity.json ]; then \
		$(PYTHON) -m loopgate.gates.tree_parity --policy configs/tree_parity.json; \
	else \
		echo "tree parity: no configs/tree_parity.json here - this checkout is not compared against another"; \
	fi

ci: check

export:
	$(PYTHON) -m loopgate.gates.export_public --out dist/public --git-init

test: check

clean:
	rm -rf dist build *.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
