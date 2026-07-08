# UndoLog LangChain Example: local development helpers.
#
# Usage:
#   make test                Run all example tests (requires pytest).
#   make test-unit           Run only unit tests (no live stack).
#   make test-live           Run only integration tests (needs live stack).
#   make test-all            Run everything (SDK + examples).
#   make run                 Run the agent against a live stack (needs OPENAI_API_KEY).
#   make run-approval        Run the auto-approval lifecycle demo.
#   make run-compensation    Run the compensation rollback demo.
#   make run-replay          Run the replay / dedup exactly-once demo.
#   make lint                Run ruff check and format check on Python files.
#   make format              Auto-format all Python files with ruff.
#
# All targets expect the UndoLog stack to be running:
#   docker compose up -d postgres engine tool-server proxy

.PHONY: test test-unit test-live test-all run run-approval run-compensation run-replay run-vanilla run-crewai run-semantic-kernel run-llama-index lint format

test:
	python -m pytest -v

test-unit:
	python -m pytest -v -m "not integration"

test-live:
	python -m pytest -v -m integration

test-all:
	python -m pytest ../../sdks/undolog-py/tests/ . -v

lint:
	ruff check .
	ruff format --check .

format:
	ruff format .

run:
	python run_against_stack.py

run-approval:
	python approval_demo.py

run-compensation:
	python compensation_demo.py

run-replay:
	python replay_demo.py

run-vanilla:
	python vanilla_agent.py

run-crewai:
	python crewai_agent.py

run-semantic-kernel:
	python semantic_kernel_agent.py

run-llama-index:
	python llama_index_agent.py
