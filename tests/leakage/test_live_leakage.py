"""The leakage boundary, re-checked against the warehouse rather than the generator.

The rest of the suite runs against the in-memory generated layer so it works in CI. That
proves the boundary holds for what the generator produces; it does not prove the
warehouse holds the same columns. Those can diverge — a DDL edit, a view, a migration —
and a blacklist that matches the generator while the warehouse has moved on is a
blacklist with a hole in it.

This module also carries the value-based half of the training-matrix guard. Comparing a
feature against the generator's post-submission columns needs both the real matrix and
the real warehouse, so it lives here, marked `integration`, rather than pretending to
run in CI.

Read-only throughout: SELECTs against `rcm`, no DDL, no writes. Safe to run in another
agent's write window, though the shared-Postgres rule in tasks.md still applies to
anything that reloads the warehouse underneath it.
"""

from __future__ import annotations

import fnmatch

import pandas as pd
import pytest
from sqlalchemy import text

from tests.leakage import detectors
from tests.leakage.test_training_matrix_guard import _discover, _feature_columns

pytestmark = pytest.mark.integration

LABEL_COLUMN = "sim_denial_flag"


@pytest.fixture(scope="module")
def pg_engine():
    from src.ingestion.load_postgres import database_url

    url = database_url()
    if not url:
        pytest.skip("no Postgres configured (set POSTGRES_* in .env)")

    from sqlalchemy import create_engine

    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            conn.execute(text("select 1"))
    except Exception as exc:  # noqa: BLE001 - any connection error means skip
        pytest.skip(f"Postgres unreachable ({exc}); run `docker compose up -d`")
    return engine


@pytest.fixture(scope="module")
def live_schema(pg_engine) -> dict[str, list[str]]:
    """Every `sim_` table in the warehouse and its columns.

    The crosswalk tables are excluded: they are data-engineer's simulated linkage
    layer, not generator output, and the firewall document does not classify them. That
    exclusion is itself asserted below so it stays a decision rather than an oversight.
    """
    with pg_engine.connect() as conn:
        tables = [
            row[0]
            for row in conn.execute(
                text(
                    "select table_name from information_schema.tables "
                    "where table_schema = 'rcm' and table_name like 'sim\\_%' "
                    "and table_name not like '%crosswalk' order by table_name"
                )
            )
        ]
        return {
            table: [
                row[0]
                for row in conn.execute(
                    text(
                        "select column_name from information_schema.columns "
                        "where table_schema = 'rcm' and table_name = :t "
                        "order by ordinal_position"
                    ),
                    {"t": table},
                )
            ]
            for table in tables
        }


def test_live_schema_matches_the_generated_schema(live_schema, generated_schema):
    """The warehouse and the generator must hold the same simulated columns.

    If they drift, every CI-side leakage assertion is being made about a schema that is
    not the one anybody trains on.
    """
    differences = {
        table: {
            "in warehouse only": sorted(set(live_schema[table]) - set(columns)),
            "in generator only": sorted(set(columns) - set(live_schema[table])),
        }
        for table, columns in generated_schema.items()
        if table in live_schema and set(live_schema[table]) != set(columns)
    }
    assert not differences, f"warehouse and generator schemas disagree: {differences}"

    missing = sorted(set(generated_schema) - set(live_schema))
    assert not missing, f"generated tables absent from the warehouse: {missing}"


def test_every_live_simulated_column_is_classified(live_schema, firewall):
    """A warehouse column nobody has classified is forbidden by default.

    The firewall document says so itself in its closing line. This asserts the
    warehouse gives that rule nothing to catch.
    """
    unclassified = [
        f"{table}.{column}"
        for table, columns in sorted(live_schema.items())
        for column in columns
        if firewall.classify(table, column) == "unclassified"
    ]
    assert not unclassified, (
        f"live warehouse columns absent from docs/simulated_forbidden_columns.md: {unclassified}"
    )


def test_config_covers_every_forbidden_column_in_the_warehouse(live_schema, firewall, model_config):
    """The config-vs-document agreement, resolved against the live schema.

    The CI test resolves the same patterns against the generator. This one is what
    actually protects a training run.
    """
    patterns = model_config.get("forbidden_features", [])
    universe = {c for columns in live_schema.values() for c in columns}
    resolved = {c for c in universe for p in patterns if fnmatch.fnmatchcase(c, p)}
    missing = sorted(firewall.model_a_forbidden(live_schema) - resolved)
    assert not missing, (
        f"{len(missing)} column(s) present in the warehouse and forbidden by the "
        f"firewall document are not blocked by config/model.yaml: {missing}"
    )


def test_crosswalk_tables_are_deliberately_out_of_scope(pg_engine, firewall):
    """The exclusion in `live_schema` is recorded, not assumed.

    The crosswalk carries the simulated facility/provider linkage. It is not generator
    output and the firewall document does not classify it, so it is excluded above —
    but it *is* joinable into a feature matrix, and its columns are checked by the
    name and value probes like any other. This test fails if the crosswalk ever starts
    carrying a column the document does forbid, which would mean the two layers have
    started to overlap and the exclusion needs revisiting.
    """
    with pg_engine.connect() as conn:
        columns = {
            row[0]
            for row in conn.execute(
                text(
                    "select column_name from information_schema.columns "
                    "where table_schema = 'rcm' and table_name like '%crosswalk'"
                )
            )
        }
    overlap = columns & firewall.forbidden_columns
    assert not overlap, (
        f"crosswalk tables now carry forbidden columns {sorted(overlap)}; they are "
        "excluded from the classified schema and that exclusion is no longer safe"
    )


# --------------------------------------------------- value-based matrix guard


@pytest.fixture(scope="module")
def live_truth(pg_engine, firewall, live_schema) -> pd.DataFrame:
    """Forbidden columns for every claim, keyed by `claim_sk`."""
    forbidden = firewall.model_a_forbidden(live_schema)
    frames = []
    with pg_engine.connect() as conn:
        for table in ("sim_claim_adjudication", "sim_operating_costs"):
            if table not in live_schema:
                continue
            wanted = [c for c in live_schema[table] if c in forbidden and c != "clm_id"]
            columns = ", ".join(f'"{c}"' for c in dict.fromkeys(["claim_sk", *wanted]))
            frames.append(
                pd.read_sql(text(f"select {columns} from rcm.{table}"), conn).set_index("claim_sk")
            )
    return pd.concat(frames, axis=1).sort_index()


@pytest.fixture(scope="module")
def live_label(pg_engine) -> pd.Series:
    with pg_engine.connect() as conn:
        frame = pd.read_sql(
            text(f"select claim_sk, {LABEL_COLUMN} from rcm.sim_claim_adjudication"), conn
        )
    return frame.set_index("claim_sk")[LABEL_COLUMN].astype(int).sort_index()


@pytest.fixture(scope="module")
def live_matrices(live_truth):
    """Discovered matrices, aligned to the truth frame on `claim_sk`."""
    discovered = _discover()
    if not discovered:
        pytest.skip(
            "no training matrix found — see the discovery contract in "
            "tests/leakage/test_training_matrix_guard.py"
        )
    aligned = []
    for source, matrix in discovered:
        if "claim_sk" not in matrix.columns:
            pytest.fail(
                f"{source} has no claim_sk column, so its features cannot be compared "
                "against the warehouse. The matrix must carry the key it was built on."
            )
        indexed = matrix.set_index("claim_sk").sort_index()
        shared = indexed.index.intersection(live_truth.index)
        assert len(shared) > 100, f"{source} shares only {len(shared)} claims with the warehouse"
        aligned.append((source, indexed.loc[shared], shared))
    return aligned


def test_no_feature_is_derived_from_a_forbidden_column(live_matrices, live_truth):
    """The probe that catches renames, logs, rescalings and re-binnings.

    Calibration and the one known gap are documented in `tests/leakage/detectors.py`.
    """
    failures: list[str] = []
    for source, matrix, shared in live_matrices:
        features = matrix[[c for c in _feature_columns(matrix.reset_index()) if c in matrix]]
        findings = detectors.dependency_findings(features, live_truth.loc[shared])
        failures += [f"{source}: {finding}" for finding in findings]
    assert not failures, "features derived from forbidden columns:\n" + "\n".join(failures)


def test_no_single_feature_beats_the_oracle(live_matrices, live_truth, live_label, capsys):
    """No function of pre-submission facts can out-predict the latent probability.

    The latent probability is the sufficient statistic the label was drawn from, so it
    bounds every single feature. A lone column above it did not get there honestly. The
    full ranking is printed so a reviewer can eyeball anything in the suspicious band
    below the ceiling, which no automatic threshold can safely reject.
    """
    failures: list[str] = []
    for source, matrix, shared in live_matrices:
        y = live_label.loc[shared].to_numpy()
        ceiling = detectors.single_feature_auc(live_truth.loc[shared, "sim_latent_p"], y)
        features = matrix[[c for c in _feature_columns(matrix.reset_index()) if c in matrix]]
        with capsys.disabled():
            print(f"\n{source}\n{detectors.auc_report(features, y, ceiling)}")
        findings = detectors.label_auc_findings(features, y, ceiling)
        failures += [f"{source}: {finding}" for finding in findings]
    assert not failures, "features that beat the oracle ceiling:\n" + "\n".join(failures)
