"""The hosted-demo data path (app-engineer).

LOCKED DECISION (docs/project_rules.md §2): the hosted demo runs off a bundled Parquet/DuckDB
extract, NOT live Postgres. This package is the two halves of that decision:

* `src/demo/spec.py`   — what is in the bundle, and each dataset's provenance
                         classification, stated once so the manifest, the docs
                         and the app cannot disagree about it.
* `src/demo/build.py`  — the build step (`make demo-extract`). Reads the curated
                         PostgreSQL views and the model artifacts, writes
                         `dashboard/demo_data/rcm_demo.duckdb`.
* `src/demo/bundle.py` — the read side, used by the dashboard and the API. Opens
                         the DuckDB file read-only; needs no database, no
                         network and no environment variables.

This package is separate from `src/ingestion/` because the demo extract is an
application artifact, not a warehouse-ingestion input.
"""
