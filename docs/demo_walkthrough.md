# Local Phase 5 Demo Walkthrough

This walkthrough demonstrates the accepted local Docker Compose workflow. It is
not hosted-deployment evidence, and it does not grant final Phase 5 acceptance.
The committed DuckDB bundle was generated from a dirty tree and remains a
non-final artifact until clean-SHA regeneration.

## Prerequisites

- A fresh clone of this repository
- Docker Engine with Docker Compose v2
- Enough local memory and disk for the shared Python application image

No host Python, PostgreSQL, `.venv`, or `.env` is required.

## Start and verify

From the repository root, start the complete stack:

```bash
docker compose up --build
```

Leave that terminal open. In a second terminal, verify application readiness:

```bash
curl --fail http://localhost:8000/live
curl --fail http://localhost:8000/ready
curl --fail http://localhost:8501/_stcore/health
curl --fail http://localhost:8502/ready
```

The local surfaces are:

| Surface | URL |
|---|---|
| FastAPI OpenAPI/Swagger | <http://localhost:8000/docs> |
| FastAPI OpenAPI JSON | <http://localhost:8000/openapi.json> |
| Streamlit dashboard | <http://localhost:8501> |
| Dashboard dependency readiness | <http://localhost:8502/ready> |
| PostgreSQL | `localhost:5432` |

`/_stcore/health` is Streamlit process liveness only. Port 8502 is the
dependency-aware dashboard readiness surface and also requires API readiness.

## Demonstration order

1. **Compose services.** Show PostgreSQL healthy, `warehouse-init` completed,
   and the API and dashboard healthy. Explain that initialization validates the
   exact 24-base-table and 9-view PostgreSQL contract before the API starts.
2. **FastAPI.** Open Swagger, call `/ready`, `/metrics/executive`, and one
   `/work-queue` mode. Point out the `sim_` names and the queue disclosure: queue
   membership already selects simulated denied or open-A/R claims.
3. **Executive Overview.** Show the synthetic-data banner, source-versus-
   simulated KPI labels, monthly trend, and cost/recovery summary.
4. **Denial Prevention.** Show simulated denial causes and provider analysis.
   Emphasize that synthetic provider identifiers remain the analytical keys and
   real CMS names are display-only crosswalk enrichment.
5. **A/R Recovery.** Show the simulated A/R aging, payer, and appeal-recovery
   sections. Every payer and every post-submission outcome is simulated.
6. **Work Queue.** Show Model C Expected Net Recovery, Model A prevention, and
   the heuristic scaffold as separate modes. Explain that the advanced model did
   not beat the logistic baseline and that the negative result is preserved.
7. **Model & Data Quality.** Show bundle provenance, the dirty generating-tree
   disclosure, monitoring context, and the reconciliation table.
8. **17/17 reconciliation.** Close on the explicit result: all 17 declared
   dashboard checks were evaluated and passed their independent control totals.
   This verifies consistency inside the current bundle; it does not make the
   dirty-tree bundle a final release artifact or validate outcomes against real
   claims.

## Suggested spoken walkthrough (5–8 minutes)

**0:00–0:45 — Runtime.** “This is a clean local Compose start. PostgreSQL is
healthy, the one-shot warehouse contract check completed, and both application
services are dependency-ready. No host Python, host database, or `.env` was
needed.”

**0:45–1:30 — API.** “The same versioned FastAPI contract serves executive
metrics and three queue modes. The OpenAPI response names preserve `sim_`
provenance, and the work queue states that its population is already selected
using simulated denial or open-A/R outcomes.”

**1:30–2:30 — Executive Overview.** “CMS supplies synthetic claim records, but
denials, payments, appeals, workflow cost, and payers are generated here. The
banner and per-metric provenance labels keep those classes visible.”

**2:30–3:30 — Denial Prevention.** “This page explains the simulated denial mix
and model inputs. Provider performance remains keyed by synthetic identifiers;
newer real-CMS names are display-only and cannot be model features.”

**3:30–4:30 — A/R Recovery.** “Aging, payer performance, recovery probability,
deadlines, and dollars at stake are simulated or simulated-derived. The display
labels say so rather than presenting them as real operational measures.”

**4:30–5:45 — Work Queue.** “The recovery, prevention, and heuristic queues are
different analytical modes. Membership is not a neutral pre-outcome cohort, and
the model comparison retains its unfavorable advanced-versus-baseline result.”

**5:45–7:00 — Quality and reconciliation.** “The bundle records its generating
commit and dirty-tree state. All 17 declared dashboard figures reconcile to
independent control totals. That is a QA result for this artifact, not evidence
about real provider or payer performance.”

**7:00–7:30 — Close.** “This is reproducible local demo evidence. Hosted
deployment, clean-SHA bundle regeneration, and final Phase 5 acceptance remain
open.”

## Backend modes and limitations

The default Compose application path is the populated committed DuckDB bundle.
PostgreSQL is required and initialized with the warehouse schema and published
views, but the clean-clone database is not populated with the full application,
model-scoring, or monitoring datasets. The application does not silently switch
between these backends.

When explicitly run with `RCM_DATA_SOURCE=postgres`, the initialized-but-unloaded
workflow keeps all five pages renderable and reports unavailable data rather than
fabricating zero metrics. Reconciliation uses `MISSING_INPUT` or `ERROR`, does
not show 17/17 success, and monitoring cohorts are reported as unavailable.

The dashboard’s role-like selector is a demonstration, not authentication or an
access-control boundary. The current bundle is dirty-tree generated and non-final.
This workflow is local only; no hosted deployment is claimed.

## Stop or reset

Stop the foreground Compose process with `Ctrl+C`, then remove containers and the
network while retaining the PostgreSQL volume:

```bash
docker compose down
```

For a destructive local database reset:

```bash
docker compose down -v
```

## Screenshot evidence manifest

All screenshots were captured on **2026-07-31** from the Docker image built from
source SHA `9899c0417194906062daf97af956f9d4cef48c11`. “Bundle” below means the
populated DuckDB application path; the same Compose stack also ran the required
initialized PostgreSQL service. The SHA identifies the application source used
for capture, not the older dirty-tree SHA recorded inside the non-final bundle.

| Screenshot | Surface | Backend mode | Synthetic banner visible | Simulated-derived fields visible |
|---|---|---|---:|---:|
| [`docker-services-healthy.png`](images/docker-services-healthy.png) | Compose service state | Bundle + initialized PostgreSQL | N/A | No |
| [`api-openapi.png`](images/api-openapi.png) | `GET /docs` | Bundle | N/A | No, endpoints collapsed |
| [`dashboard-executive-overview.png`](images/dashboard-executive-overview.png) | Executive Overview | Bundle | Yes | Yes |
| [`dashboard-denial-prevention.png`](images/dashboard-denial-prevention.png) | Denial Prevention | Bundle | Yes | Yes |
| [`dashboard-ar-recovery.png`](images/dashboard-ar-recovery.png) | A/R Recovery | Bundle | Yes | Yes |
| [`dashboard-work-queue.png`](images/dashboard-work-queue.png) | Work Queue | Bundle | Yes | Yes |
| [`dashboard-model-data-quality.png`](images/dashboard-model-data-quality.png) | Model & Data Quality | Bundle | Yes | Yes |
| [`dashboard-reconciliation-17-of-17.png`](images/dashboard-reconciliation-17-of-17.png) | 17/17 reconciliation detail | Bundle | No, focused crop | Yes |

Each image is a browser capture of the running local stack. No credentials,
tokens, usernames, unrelated desktop content, or raw browser profiles are stored
in the repository.
