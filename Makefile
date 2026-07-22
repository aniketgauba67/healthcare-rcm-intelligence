.PHONY: setup ingest stage contracts warehouse warehouse-check validate-warehouse simulate simulate-warehouse simulate-check views train score dashboard api test lint demo-extract

setup:
	uv sync

ingest:
	uv run python -m src.ingestion.run

stage:
	uv run python -m src.validation.run

contracts:
	uv run python -m src.validation.contracts_run

# Simulation layer. Order matters: `make warehouse` drops fact_inpatient_claim
# with CASCADE, which drops the sim tables' foreign keys, so the sim layer must
# be rebuilt after any warehouse load. `simulate-warehouse` refuses to run if the
# star schema is missing or has been renumbered underneath it.
#   make warehouse -> make simulate -> make simulate-warehouse
simulate:
	uv run python -m src.simulation.run

simulate-warehouse:
	uv run python -m src.simulation.load_sim

simulate-check:
	uv run python -m src.simulation.load_sim --offline-check

warehouse:
	uv run python -m src.ingestion.load_postgres

warehouse-check:
	uv run python -m src.ingestion.load_postgres --offline-check

validate-warehouse:
	uv run pytest -m integration -q

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
