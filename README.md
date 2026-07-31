# Healthcare RCM Intelligence Platform

> **Status:** Phases 1–4 of 5 are complete and QA-accepted. Phase 5's
> API/dashboard/demo integration is in active QA. Clean-clone Docker packaging
> is implemented and awaiting independent QA; screenshots, demo walkthrough
> instructions, clean-SHA bundle regeneration, and final Phase 5 acceptance
> remain open.

An end-to-end healthcare revenue-cycle intelligence platform built on official CMS synthetic Medicare claims. The project ingests and validates source data, builds a PostgreSQL analytics warehouse, adds a transparently simulated adjudication layer, computes revenue-cycle KPIs, and performs statistical analysis across denials, payment timing, appeals, workflow events, and operational costs.

Phase 4 supplies explainable machine-learning models. Phase 5 is integrating
those models with a FastAPI scoring service, a Streamlit analyst dashboard, and
a runnable demo bundle; that phase is not yet accepted or deployed.

## Why this project exists

Healthcare revenue-cycle teams need to understand why claims are denied, where payment delays occur, which appeals are likely to succeed, and how limited analyst capacity should be prioritized. Real claims and denial data are highly sensitive and difficult to share publicly, so this project separates:

- **Official synthetic CMS claims data** used as the source layer
- **Derived analytics** calculated from those claims
- **Official reference data** used for code and provider enrichment
- **Clearly labeled simulated outcomes** for denials, appeals, payments, and workflow activity

No simulated outcome is presented as real. Provenance, assumptions, and limitations are treated as first-class engineering requirements.

## Current capabilities

### Phase 1 — Ingestion, validation, and warehouse ✅

- Downloads and records checksums and source vintages
- Stages raw files into typed Parquet datasets
- Loads a PostgreSQL 16 star schema
- Applies schema, date, money, uniqueness, and referential-integrity contracts
- Quarantines invalid records instead of silently dropping them
- Produces reconciliation reports across raw, staged, and warehouse layers
- Enriches claims with FY2023 ICD-10-CM, ICD-10-PCS, HCPCS Level II, MS-DRG, and project-authored CARC category labels

### Phase 2 — Simulated adjudication layer ✅

- Generates claim submission, acknowledgment, adjudication, payment, denial, appeal, workflow, and operating-cost events
- Uses deterministic named random streams for reproducibility
- Preserves causal ordering between pre-submission facts and post-submission outcomes
- Separates legitimate model-time features from post-outcome and latent fields
- Calibrates simulated ranges against documented industry benchmarks
- Validates directional behavior, distributions, temporal ordering, foreign keys, and provenance

### Phase 3 — Analytics and KPI views ✅

- Builds one enriched claim view and eight metric-contract views
- Reconciles every view to independent control queries
- Produces six reproducible Python analysis modules
- Includes statistical testing, survival analysis, process mining, risk-adjusted facility analysis, and interrupted time-series methodology
- Documents 19 decision-relevant analytical insights

### Phase 4 — Explainable machine learning ✅

Implemented and QA-accepted:

- Point-in-time feature store
- Automated leakage protection
- Logistic-regression baselines
- XGBoost comparison
- Temporal train/test splits
- Probability calibration
- SHAP explanations
- Appeal-success modeling
- Expected Net Recovery work-queue prioritization
- Slice metrics and bootstrap confidence intervals

### Phase 5 — API, dashboard, and packaging 🚧

Phase 5 is in integration QA and is not accepted or deployed.

Implemented and currently under QA:

- Versioned FastAPI endpoints and schemas
- Five-page Streamlit analyst dashboard
- DuckDB demo bundle, generated from a dirty tree and not a final release artifact
- Provenance and simulated-data disclosure protections
- Dashboard reconciliation reporting
- Clean-clone Docker Compose stack, pending independent QA

Still required before Phase 5 acceptance:

- Independent QA acceptance of the clean-clone Docker startup
- Screenshots
- Demo walkthrough script and instructions
- Hosted deployment evidence
- Final clean-SHA DuckDB bundle regeneration
- Final Phase 5 QA acceptance

## Verified project scale

| Component | Verified result |
|---|---:|
| Beneficiary records staged | 9,660 |
| Inpatient source rows staged | 58,066 |
| Claim headers loaded | 20,867 |
| Revenue lines loaded | 58,066 |
| Diagnosis records loaded | 338,024 |
| Simulated adjudication records | 20,867 |
| SQL analytics views | 9 |
| Analytics modules | 6 |
| Documented analytical insights | 19 |
| View reconciliation checks | 21/21 passing |
| Current clean test result | 74 passed, 19 environment-dependent skips, 0 failures |

Environment-dependent skips require local raw files or the live PostgreSQL service. The project also contains separate live-database acceptance checks for warehouse and simulation integrity.

## Architecture

```text
Official CMS synthetic claims + official reference datasets
                         |
                         v
              Download + manifest layer
                         |
                         v
                Typed Parquet staging
                         |
                         v
          Data contracts + quarantine handling
                         |
                         v
              PostgreSQL 16 star schema
                         |
          +--------------+---------------+
          |                              |
          v                              v
Simulated adjudication layer      Reference enrichment
          |                              |
          +--------------+---------------+
                         |
                         v
             Reconciled analytics views
                         |
                         v
       Statistical analysis and KPI notebooks
                         |
                         v
        ML models -> FastAPI -> Streamlit dashboard
                  (in development)
```

## Data provenance

Every curated field is classified as one of:

| Classification | Meaning |
|---|---|
| `SOURCE` | Preserved from official CMS synthetic source files |
| `DERIVED` | Calculated from source or reference fields |
| `REFERENCE` | Taken from official code, facility, or provider datasets |
| `SIMULATED` | Generated by this project’s adjudication model |

Additional safeguards include:

- Simulated tables and generated fields use the `sim_` prefix
- Raw CMS files are never modified or committed
- Source checksums and vintages are recorded
- Payer-level analysis is explicitly labeled as simulated
- Synthetic provider identifiers remain the analytical grain
- Real provider and facility names are display-only enrichment
- The provenance register and data dictionary are updated with schema changes
- Post-submission and latent values are forbidden from pre-submission ML features

### Crosswalk limits: display enrichment, not identity

The synthetic claims source is vintage **2023-04**. The reference files used
only to decorate the seeded display crosswalk are newer: CMS Hospital General
Information is vintage **2026-04**, and Medicare Physician & Other Practitioners
uses data year **2024**, released **2026-05**. This is a temporal mismatch, not a
historically accurate 2023 provider/facility assignment. Hospitals may open,
close, change type, or change attributes; providers may change state or
specialty between the claim and the later reference file.

The seeded crosswalk maps **4,876** synthetic providers onto **2,857** real
CCNs, with a worst CCN collision of **8:1**. Grouping by displayed facility name
is less unique still: the worst name collision is **15:1**. Displayed real-CMS
provider/facility names and CCNs are display enrichment, not unique analytical
identifiers. Use `claim_sk` for claim grain and the synthetic `prvdr_num` for
provider/facility analysis; do not group, join, deduplicate, or evaluate
performance by a displayed name. Crosswalked real provider/facility names, CCNs,
NPIs, and display attributes are forbidden as a feature in every ML model. The
seeded synthetic association is not a real provider/facility relationship.

See:

- [`docs/model_card.md`](docs/model_card.md)
- [`docs/provenance_register.md`](docs/provenance_register.md)
- [`docs/data_dictionary.md`](docs/data_dictionary.md)
- [`docs/assumptions.md`](docs/assumptions.md)
- [`docs/simulated_forbidden_columns.md`](docs/simulated_forbidden_columns.md)

## Coding-agent development workflow

This repository was developed with a role-based coding-agent workflow coordinated and reviewed by a human project owner.

Agents were assigned bounded responsibilities for:

- Data engineering
- Simulation engineering
- Analytics engineering
- Machine learning
- Application engineering
- QA review

The workflow uses:

- Explicit file-ownership boundaries
- Separate feature branches and worktrees
- A shared task board with acceptance criteria
- Tests added alongside new modules
- Independent QA review before phase acceptance
- Reconciliation and reproducibility gates
- Human review of generated code, assumptions, and merge decisions

Coding agents are treated as implementation collaborators, not as a substitute for validation. The repository records several cases where generated work was corrected after tests, review, or reconciliation exposed issues such as test-order dependencies, incomplete leakage protection, misleading analytical output, and unsafe warehouse reload ordering.

See [`CLAUDE.md`](CLAUDE.md) and [`tasks.md`](tasks.md) for the development rules and phase history.

## Technology stack

### Implemented

- Python 3.11+
- PostgreSQL 16
- DuckDB
- Pandas and NumPy
- PyArrow and Parquet
- SQLAlchemy and psycopg2
- SciPy and statsmodels
- lifelines
- Pytest
- Ruff
- Docker Compose
- `uv` dependency management

### Configured for upcoming phases

- scikit-learn
- XGBoost
- SHAP
- FastAPI and Pydantic
- Streamlit and Plotly

Configured dependencies are not presented as completed product features until the corresponding phase is accepted.

## Repository structure

```text
.
├── config/                 # Source, simulation, and model configuration
├── docs/                   # Assumptions, provenance, dictionary, leakage rules
├── notebooks/              # Reproducible statistical analysis modules
├── sql/
│   ├── ddl/                # Warehouse, simulation, quarantine, reference schemas
│   ├── quality/            # View reconciliation gates
│   └── views/              # Enriched and KPI contract views
├── src/
│   ├── ingestion/          # Download, transform, reference, and warehouse loaders
│   ├── simulation/         # Reproducible adjudication generator
│   └── validation/         # Staging, contracts, quarantine, reconciliation
├── tests/                  # Unit, contract, simulation, and integration tests
├── CLAUDE.md               # Agent roles and engineering rules
├── tasks.md                # Phase board, QA findings, and acceptance evidence
├── Makefile                # Reproducible project commands
└── pyproject.toml          # Python dependencies and tooling configuration
```

## Docker demo

### Prerequisites

- Docker Engine with Docker Compose v2
- Enough local memory and disk for the Python application image

No host Python, PostgreSQL, `.venv`, or `.env` is required. From a fresh clone:

```bash
docker compose up --build
```

Compose builds one reproducible Python 3.11 image and starts:

| Service | URL | Ready when |
|---|---|---|
| PostgreSQL 16 | `localhost:5432` | `pg_isready` succeeds and `warehouse-init` verifies the exact 24 base tables and 9 views |
| FastAPI | <http://localhost:8000> | `/ready` verifies PostgreSQL initialization plus the bundled data source |
| Streamlit | <http://localhost:8501> | dependency readiness at <http://localhost:8502/ready> verifies Streamlit and the API |

OpenAPI is available at <http://localhost:8000/docs> and as JSON at
<http://localhost:8000/openapi.json>.

The API separates process liveness (`GET /live`) from dependency readiness
(`GET /ready`; `GET /health` is a compatibility alias for readiness). Streamlit's
built-in `/_stcore/health` is process liveness only. The dashboard's externally
requestable dependency readiness endpoint is `GET http://localhost:8502/ready`.
These commands verify each surface:

```bash
curl --fail http://localhost:8000/live
curl --fail http://localhost:8000/ready
curl --fail http://localhost:8501/_stcore/health
curl --fail http://localhost:8502/ready
```

Every API readiness request verifies the configured bundle path as it exists at
that moment: it checks the committed SHA-256 identity, opens a new temporary
read-only DuckDB connection, validates the declared dataset and provenance
inventory, and closes that probe connection. This is intentionally independent
of the cached connection serving application requests, because an already-open
Unix file descriptor remains readable after its pathname is deleted. The default
stack serves `/app/dashboard/demo_data/rcm_demo.duckdb`, pinned to SHA-256
`ef9d8013d84f74133153033a5e68f950cf51cc5e1e559cf80175f93a94c3e7e0`.
Unset or blank overrides retain those committed defaults.

An approved replacement must set both `RCM_DEMO_BUNDLE` and its matching
`RCM_DEMO_BUNDLE_SHA256`. The path is inside the container: setting it to an
arbitrary host path does not make that file visible, so an external bundle needs
an image rebuild or a read-only bind mount. The API and dashboard cache their
serving DuckDB connection at process startup. After changing the path or pin,
recreate both application containers; readiness detects deletion, corruption,
replacement and pin mismatch, but it does not hot-swap the serving connection.
Restoring the exact original artifact can recover readiness without a restart;
switching to a genuinely different approved artifact requires recreation.

For example, this temporary Compose override mounts one host bundle at the same
container path in both services:

```yaml
# docker-compose.bundle-override.yml
services:
  api:
    volumes:
      - "${RCM_DEMO_BUNDLE_HOST:?set an absolute host path}:/opt/rcm/override.duckdb:ro"
  dashboard:
    volumes:
      - "${RCM_DEMO_BUNDLE_HOST:?set an absolute host path}:/opt/rcm/override.duckdb:ro"
```

```bash
export RCM_DEMO_BUNDLE_HOST=/absolute/host/path/approved.duckdb
export RCM_DEMO_BUNDLE=/opt/rcm/override.duckdb
export RCM_DEMO_BUNDLE_SHA256="$(shasum -a 256 "$RCM_DEMO_BUNDLE_HOST" | awk '{print $1}')"
docker compose -f docker-compose.yml -f docker-compose.bundle-override.yml \
  up -d --force-recreate api dashboard
```

Remove the temporary override file and unset the three variables before
recreating the services to return to the committed default.

The containerized API and dashboard deliberately use the committed DuckDB demo
bundle (`RCM_DATA_SOURCE=bundle`). PostgreSQL is still a required, health-checked
service. Its existing DDL initializes 24 base tables and 9 views deterministically
on the first volume start. On every later Compose startup, the one-shot
`warehouse-init` service validates every expected object name and relation type,
required sentinel columns, and view queryability before the API can start. It
does not mutate the database from a health-check loop: a partial retained volume
fails readiness with the missing or mistyped objects named.

### Initialized PostgreSQL mode

The clean-clone database contains the tracked schema and published views, but it
is not a fully populated production warehouse and may lack application or model
output datasets. All five pages remain renderable without exceptions in this
initialized-but-unloaded mode and report what is unavailable:

- **Executive Overview** shows an unavailable-data state, not a zero KPI book.
- **Denial Prevention** shows unavailable-data disclosures, not zero denial or
  provider metrics.
- **A/R Recovery** reports aging, payer and appeal-recovery inputs separately and
  does not fabricate zero-dollar or zero-day measures.
- **Work Queue** identifies missing model and heuristic queue inputs.
- **Model & Data Quality** reports incomplete reconciliation with `MISSING_INPUT`
  or `ERROR` results, suppresses 17/17 success and reports unavailable monitoring
  cohorts.

To exercise the optional live-warehouse backend with populated data, load it using
the supported developer workflow below and explicitly set `RCM_DATA_SOURCE=postgres`.

The Compose credentials are non-secret local-demo defaults. Optional overrides
can be supplied in the shell or an untracked `.env` file:

| Variable | Default |
|---|---|
| `POSTGRES_USER` | `rcm` |
| `POSTGRES_PASSWORD` | `rcm_demo_only` |
| `POSTGRES_DB` | `rcm_warehouse` |
| `RCM_POSTGRES_PORT` | `5432` |
| `RCM_API_PORT` | `8000` |
| `RCM_DASHBOARD_PORT` | `8501` |
| `RCM_DASHBOARD_READINESS_PORT` | `8502` |
| `RCM_DEMO_BUNDLE` | `/app/dashboard/demo_data/rcm_demo.duckdb` |
| `RCM_DEMO_BUNDLE_SHA256` | committed artifact SHA-256 shown above |

Stop the stack without deleting its database volume:

```bash
docker compose down
```

For a destructive local reset, including the named PostgreSQL volume:

```bash
docker compose down -v
```

This is local demo packaging, not hosted deployment evidence or final Phase 5
acceptance. The committed DuckDB bundle was generated from a dirty tree and
remains non-final until it is regenerated from the final clean integration SHA.

## Full warehouse developer setup

### Prerequisites

- Python 3.11 or 3.12
- [`uv`](https://docs.astral.sh/uv/)
- Docker and Docker Compose
- Sufficient disk space for public CMS and provider reference downloads

### 1. Clone the repository

```bash
git clone https://github.com/aniketgauba67/healthcare-rcm-intelligence.git
cd healthcare-rcm-intelligence
```

### 2. Install dependencies

```bash
make setup
```

### 3. Configure the environment

Create a local `.env` file using the repository’s environment template when available. Secrets and local database credentials must not be committed.

### 4. Start PostgreSQL only

```bash
docker compose up -d postgres
```

### 5. Download and stage source data

```bash
make ingest
make stage
make contracts
```

### 6. Build the complete current warehouse

Run the warehouse and simulation layers in the required order:

```bash
make warehouse-all
make reference-codes
make views
```

`make warehouse-all` should be preferred over running `make warehouse` alone because rebuilding the warehouse changes surrogate keys and requires the simulation layer to be regenerated and reloaded.

## Validation and quality checks

Run the fast test suite:

```bash
make test
```

Run formatting and lint checks:

```bash
make lint
```

Run live PostgreSQL integration checks:

```bash
make validate-warehouse
```

Run simulation validation without writing output:

```bash
make validate-simulation
```

Run offline warehouse and simulation checks:

```bash
make warehouse-check
make simulate-check
```

Apply analytics views and execute their reconciliation gate:

```bash
make views
```

## Analytical coverage

The current analysis layer includes:

- Data-quality and provenance scorecards
- Denial root-cause analysis
- Authorization and eligibility relationships
- Chi-square tests and Cramér’s V
- Adjusted logistic analysis
- Payment-time comparisons
- Kaplan–Meier survival curves
- Cox proportional-hazards modeling
- Schoenfeld proportional-hazards checks
- Risk-adjusted facility performance
- Indirect standardization and Poisson funnel logic
- Process variants, rework, and bottleneck analysis
- Illustrative interrupted time-series methodology

The interrupted time-series example is explicitly labeled illustrative and does not claim a real or simulated operational intervention occurred.

## Engineering principles

1. **Data honesty over impressive results**  
   Simulated outcomes are always disclosed, and models will not be tuned simply to manufacture stronger headline performance.

2. **Reproducibility over convenience**  
   Seeded named random streams, canonical table hashes, pinned dependencies, and controlled build order make outputs repeatable.

3. **Tests over assumptions**  
   Schema checks, foreign-key checks, reconciliation gates, leakage tests, and independent QA determine acceptance.

4. **Point-in-time correctness**  
   Future ML features must contain only information available at the modeled decision time.

5. **Reviewable agent-assisted development**  
   AI-generated code must remain understandable, testable, and subject to human and automated review.

## Roadmap

- [x] Phase 1: ingestion, validation, warehouse, and reference data
- [x] Phase 2: reproducible adjudication simulation
- [x] Phase 3: analytics views and statistical analysis
- [ ] Phase 4: explainable denial and appeal modeling
- [ ] Phase 5: FastAPI, Streamlit, demo extract, and deployment packaging

## Limitations

- Source claims are synthetic Medicare records, not real patient claims
- Denials, appeals, payer assignments, workflow events, and operating costs are simulated
- Real facility and provider data are used only as display enrichment through a simulated crosswalk
- Current analytical findings demonstrate methodology and system behavior; they should not be interpreted as evidence about real provider performance
- The public project is educational and portfolio-oriented and is not intended for clinical, billing, reimbursement, or financial decision-making

## Author

**Aniket Gauba**  
Computer Science graduate with minors in Data Analytics and Physics  
[LinkedIn](https://www.linkedin.com/in/aniket-gauba/) | [GitHub](https://github.com/aniketgauba67) | [Portfolio](https://aniketgauba.com)
