.PHONY: setup ingest stage contracts warehouse warehouse-all warehouse-check validate-warehouse simulate simulate-warehouse simulate-check validate-simulation reference-codes views features train train-appeal score dashboard api test lint demo-extract

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

# Directional / distributional / temporal / referential / provenance checks on the
# generated frames, without writing any files. Fails non-zero on any violation.
validate-simulation:
	uv run python -m src.simulation.run --no-write

# The whole warehouse in the one order that is correct. Use this rather than
# running `make warehouse` on its own, which leaves the sim layer stale.
warehouse-all: warehouse simulate simulate-warehouse

warehouse:
	uv run python -m src.ingestion.load_postgres

warehouse-check:
	uv run python -m src.ingestion.load_postgres --offline-check

validate-warehouse:
	uv run pytest -m integration -q

# REFERENCE code sets (FY2023): download + ADDITIVE load. Safe to run any time —
# applies only sql/ddl/60_reference_codes.sql (create-if-not-exists) and enriches
# dim_drg.drg_desc; never drops/reloads fact_* or sim_*. Re-run after `warehouse`
# (a full reload recreates dim_drg with a null drg_desc).
reference-codes:
	uv run python -m src.ingestion.reference_codes

views:
	uv run python sql/views/apply_views.py
	uv run python sql/quality/view_reconciliation.py

# Rebuild the committed Model A feature matrix under artifacts/features/. That
# file is what the docs/project_rules.md §4.1 leakage probes run against on a clean clone,
# so it has to be regenerated (and re-committed) whenever the feature store or
# the warehouse changes. `make train` writes it as a side effect too.
#
# The write REFUSES if the content deviates from the committed manifest (row
# count, column set, per-column null rates) — a run against a degraded warehouse
# once overwrote this file with an all-null column and only failed afterwards.
# For an intended change: `make features ALLOW_CHANGE=1`.
features:
	uv run python -m src.features $(if $(ALLOW_CHANGE),--allow-change,)

train:
	uv run python -m src.models.train

train-appeal:
	uv run python -m src.models.model_c

score:
	uv run python -m src.models.score

# Build the bundled DuckDB demo extract (docs/project_rules.md §2 locked decision: the hosted
# demo reads a bundle, not live Postgres). READS the curated views and the model
# artifacts; writes dashboard/demo_data/rcm_demo.duckdb, which is COMMITTED — a
# deployed app has no other way to get data. Needs `make views`, `make train` and
# `make train-appeal` to have run. `--skip-models` builds a warehouse-only bundle
# for iterating on the extract; it is not shippable and says so on stderr.
demo-extract:
	uv run python -m src.demo.build

dashboard:
	uv run streamlit run dashboard/app.py

api:
	uv run uvicorn src.api.main:app --reload

test:
	uv run pytest -q

lint:
	uv run ruff check . && uv run ruff format --check .
