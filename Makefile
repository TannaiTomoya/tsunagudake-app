.PHONY: check format secrets

check:
	uv run --frozen ruff format --check .
	uv run --frozen ruff check .
	uv run --frozen mypy
	uv run --frozen pytest

format:
	uv run --frozen ruff format .

secrets:
	uv run --frozen python scripts/check_secrets.py
