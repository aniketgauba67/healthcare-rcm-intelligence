# Zero-Cost Hosted Deployment

This guide prepares the accepted application for a portfolio deployment on Neon
Free, Render Free, and Streamlit Community Cloud. No hosted deployment is claimed
until the public URLs and independent QA evidence are added to this document.

## Architecture

- **Neon Free PostgreSQL:** the initialized `rcm` schema, 24 base tables, and 9
  published views. It contains no patient data and is not presented as a populated
  model warehouse.
- **Render Free:** the existing FastAPI application at `src.api.main:app`. It
  serves the committed DuckDB bundle and requires Neon contract validation for
  readiness.
- **Streamlit Community Cloud:** the existing `dashboard/app.py`. It reads the
  same committed DuckDB bundle directly; it does not silently switch to Neon or
  depend on the API for page data.

The default artifact remains `dashboard/demo_data/rcm_demo.duckdb`, pinned by
`RCM_DEMO_BUNDLE_SHA256`. Credentials belong only in provider environment or
secret controls.

## Neon initialization

Create a dedicated Free project, obtain its SSL-required connection string, and
place it directly in the invoking shell or provider secret control. Never write
it to this repository.

```bash
DATABASE_URL="<set outside git>" uv run python scripts/initialize_hosted_postgres.py
DATABASE_URL="<set outside git>" uv run python scripts/initialize_hosted_postgres.py
```

The first command initializes only when the `rcm` schema is absent. The second
validates and reuses the complete schema. An empty or partial existing `rcm`
schema fails without applying the drop-and-recreate DDL. Both runs print only the
PostgreSQL version and non-secret object counts.

## Render Free API settings

Create one **Web Service** from `feat/phase5-hosted-deployment` with:

| Setting | Value |
|---|---|
| Runtime | Python |
| Region | Virginia |
| Instance type | Free |
| Build command | `uv sync --frozen --no-dev` |
| Start command | `uv run --no-sync uvicorn src.api.main:app --host 0.0.0.0 --port $PORT` |
| Health check | `/ready` |
| Auto-deploy | On for the selected branch |

Set these environment values in Render, not in git:

| Name | Value |
|---|---|
| `DATABASE_URL` | Neon connection string, including required SSL parameters |
| `RCM_DATA_SOURCE` | `bundle` |
| `RCM_REQUIRE_POSTGRES_READY` | `true` |
| `RCM_DEMO_BUNDLE_SHA256` | SHA-256 of the committed approved bundle |

The in-repository bundle path is the default, so `RCM_DEMO_BUNDLE` does not need
an override. The dashboard performs no browser-side API requests, so a permissive
CORS policy is neither required nor enabled.

Render Free has 512 MB RAM and sleeps after 15 minutes without inbound traffic.
The first request after sleep can take about a minute. The service is portfolio
evidence, not production or always-on infrastructure. Do not use uptime pingers
to evade the free-plan limits.

## Streamlit Community Cloud settings

Create one public app with:

| Setting | Value |
|---|---|
| Repository | `aniketgauba67/healthcare-rcm-intelligence` |
| Branch | `feat/phase5-hosted-deployment` |
| Entrypoint | `dashboard/app.py` |
| Python | 3.11 |

Community Cloud discovers the committed `uv.lock` and installs the locked
environment. No Streamlit secret is required for the supported bundle-backed
dashboard. Do not commit `.streamlit/secrets.toml`.

## Current release state

Hosted URLs, provider status evidence, and screenshots are pending. Phase 5
remains under QA, and the current DuckDB bundle remains dirty-tree generated and
non-final until the clean integration-SHA regeneration gate is completed.
