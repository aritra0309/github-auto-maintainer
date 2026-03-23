.PHONY: test lint run run-local

test:
	python -m pytest

lint:
	python -m ruff check .
	python -m mypy .

run:
	PYTHONPATH=src python -m github_auto_maintainer

run-local:
	DEFAULT_PROVIDER=ollama PYTHONPATH=src python -m github_auto_maintainer
