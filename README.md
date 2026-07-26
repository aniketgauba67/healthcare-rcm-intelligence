# Healthcare RCM Intelligence Platform

> **Status:** In active development — Phases 1–3 of 5 are complete. Machine learning and application packaging are in progress.

An end-to-end healthcare revenue-cycle intelligence platform built on official CMS synthetic Medicare claims. The project ingests and validates source data, builds a PostgreSQL analytics warehouse, adds a transparently simulated adjudication layer, computes revenue-cycle KPIs, and performs statistical analysis across denials, payment timing, appeals, workflow events, and operational costs.

The remaining phases add explainable machine-learning models, a FastAPI scoring service, and a Streamlit analyst dashboard.

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

### Phase 4 — Explainable machine learning 🚧

Planned and currently in development:

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

Planned and currently in development:

- Versioned FastAPI scoring endpoints
- Five-page Streamlit analyst dashboard
- DuckDB/Parquet demo extract
- Clean-clone Docker startup
- Screenshots, demo script, and hosted deployment

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

See:

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

## Local setup

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

### 4. Start PostgreSQL

```bash
docker compose up -d
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

## License

A project license has not yet been selected. Until one is added, the repository remains publicly viewable but does not grant permission for reuse, modification, or redistribution.
