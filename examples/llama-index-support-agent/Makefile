# UndoLog llama-index Example: local development helpers.

.PHONY: test test-unit lint format run

test:
	python -m pytest -v

test-unit:
	python -m pytest -v -m "not integration"

lint:
	ruff check .
	ruff format --check .

format:
	ruff format .

run:
	python llama-index_agent.py
