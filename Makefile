.PHONY: test lint run run-local docker-build docker-run docker-up docker-down

test:
	python -m pytest

lint:
	python -m ruff check .
	python -m mypy .

run:
	PYTHONPATH=src python -m github_auto_maintainer

run-local:
	DEFAULT_PROVIDER=ollama PYTHONPATH=src python -m github_auto_maintainer

docker-build:
	docker build -t github-auto-maintainer .

docker-run:
	docker run -d -p 8000:8000 --env-file .env -v gham-data:/app/data --name gham github-auto-maintainer

docker-up:
	docker compose up -d --build

docker-down:
	docker compose down
