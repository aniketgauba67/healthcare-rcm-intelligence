.PHONY: setup ingest stage warehouse warehouse-check simulate views train score dashboard api test lint demo-extract

setup:
	uv sync

ingest:
	uv run python -m src.ingestion.run

stage:
	uv run python -m src.validation.run

simulate:
	uv run python -m src.simulation.run

warehouse:
	uv run python -m src.ingestion.load_postgres

warehouse-check:
	uv run python -m src.ingestion.load_postgres --offline-check

views:
	uv run python -m src.ingestion.build_views

train:
	uv run python -m src.models.train

score:
	uv run python -m src.models.score

demo-extract:
	uv run python -m src.ingestion.export_demo_duckdb

dashboard:
	uv run streamlit run dashboard/app.py

api:
	uv run uvicorn src.api.main:app --reload

test:
	uv run pytest -q

lint:
	uv run ruff check . && uv run ruff format --check .
