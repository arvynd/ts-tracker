default:
    @just --list

setup:
    uv sync

data:
    uv run ts-tracker data

ingest:
    uv run ts-tracker ingest

validate:
    uv run ts-tracker validate

bench: data
    uv run ts-tracker bench

test:
    uv run pytest

lint:
    uv run ruff check .
    uv run ruff format --check .

fmt:
    uv run ruff check --fix .
    uv run ruff format .

deploy-local:
    @echo "deploy-local arrives in Phase 5"; exit 1
