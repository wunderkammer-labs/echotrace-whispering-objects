PYTHON ?= python3

.PHONY: install lint typecheck test run-hub run-node

install:
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements.txt -r requirements-dev.txt

lint:
	ruff check .
	flake8

typecheck:
	mypy .

test:
	$(PYTHON) -m pytest

verify: lint typecheck test

run-hub:
	$(PYTHON) -m hub.run_hub

run-node:
	$(PYTHON) -m pi_nodes.node_service
