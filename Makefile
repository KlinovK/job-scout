.PHONY: format lint typecheck test quality

format:
	.venv/bin/ruff format .

lint:
	.venv/bin/ruff check .
	.venv/bin/ruff format --check .

typecheck:
	.venv/bin/mypy

test:
	.venv/bin/pytest

quality: lint typecheck test

