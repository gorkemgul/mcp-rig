VENV ?= .venv
BIN := $(VENV)/bin

.PHONY: dev lint test examples check

dev:
	python3 -m venv $(VENV)
	$(BIN)/pip install -e ".[dev]"

lint:
	$(BIN)/ruff check src tests scripts

test:
	$(BIN)/pytest -q

examples:
	PATH="$(abspath $(BIN)):$$PATH" $(BIN)/mcp-rig run examples/fixture.yaml examples/feature-tour/

check:
	$(BIN)/mcp-rig check "$(BIN)/python tests/fixtures/fixture_server.py"
