"""The blacklist is checked against the warehouse it is supposed to protect.

The unit tests above compare `config/model.yaml` to a document. A document can
agree with a config and both be out of date with respect to the database. These
tests close that loop, read-only: every column of a wholesale-forbidden table is
enumerated in the config, and every simulated column that exists in Postgres is
either forbidden or explicitly classified as permitted — never merely unmentioned.

The firewall document's own closing rule is the standard applied here: "If a
column appears in the warehouse that is not listed here, treat it as forbidden
until this document is updated."
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from src.features.leakage import (
    FIREWALL_DOC_PATH,
    forbidden_columns,
    load_model_config,
    parse_firewall_doc,
)

pytestmark = pytest.mark.integration

_SCHEMA = "rcm"


@pytest.fixture(scope="module")
def engine():
    from src.ingestion.load_postgres import database_url

    url = database_url()
    if not url:
        pytest.skip("no Postgres configured (set POSTGRES_* in .env)")

    from sqlalchemy import create_engine

    eng = create_engine(url)
    try:
        with eng.connect() as conn:
            conn.execute(text("select 1"))
    except Exception as exc:  # noqa: BLE001 - any connection error means skip
        pytest.skip(f"Postgres unreachable ({exc}); run `docker compose up -d`")
    return eng


def _columns_of(engine, table: str) -> list[str]:
    query = text(
        "select column_name from information_schema.columns "
        "where table_schema = :schema and table_name = :table order by ordinal_position"
    )
    with engine.connect() as conn:
        return [row[0] for row in conn.execute(query, {"schema": _SCHEMA, "table": table})]


def test_forbidden_table_expansion_matches_the_database(engine) -> None:
    """`forbidden_table_columns` is a cached expansion; caches go stale."""
    config = load_model_config()
    # claim_sk / clm_id / the provenance stamps are already blocked by name from
    # the document's §6 and §7, so the per-table lists do not repeat them.
    shared = {"claim_sk", "clm_id", "sim_provenance", "sim_config_version", "sim_seed"}
    for table in config["forbidden_tables"]:
        actual = set(_columns_of(engine, table)) - shared
        assert actual, f"{table} not found in {_SCHEMA}"
        configured = set(config["forbidden_table_columns"][table])
        assert configured == actual, (
            f"{table}: config lists {sorted(configured)} but the warehouse has {sorted(actual)}"
        )


def test_every_simulated_column_is_classified(engine) -> None:
    """No sim_ column may be silently unmentioned by both config and document.

    An unclassified column is one nobody has decided is pre- or post-submission,
    which is how a leak ships. Three ways to be classified: named in the
    document, blocked by the config, or living in a table that one of the two
    classifies wholesale (§3's "all columns" rows, and the crosswalk tables the
    config excludes entirely).
    """
    config = load_model_config()
    doc_text = FIREWALL_DOC_PATH.read_text()
    blocked = forbidden_columns("A", config)
    wholesale = parse_firewall_doc().permitted_tables | set(config["forbidden_crosswalk_tables"])
    assert len(wholesale) >= 6, sorted(wholesale)

    query = text(
        "select table_name, column_name from information_schema.columns "
        "where table_schema = :schema and table_name like 'sim\\_%' "
        "order by table_name, ordinal_position"
    )
    with engine.connect() as conn:
        rows = list(conn.execute(query, {"schema": _SCHEMA}))

    unclassified = [
        f"{table}.{column}"
        for table, column in rows
        if table not in wholesale and column not in blocked and column not in doc_text
    ]
    assert not unclassified, f"simulated columns classified nowhere: {unclassified}"


def test_the_label_is_reachable_and_is_not_a_feature(engine) -> None:
    """Sanity: the thing we are predicting exists, and the guard rejects it."""
    columns = _columns_of(engine, "sim_claim_adjudication")
    assert "sim_denial_flag" in columns
    assert "sim_denial_flag" in forbidden_columns("A")
    with engine.connect() as conn:
        denied = conn.execute(
            text(f"select count(*) from {_SCHEMA}.sim_claim_adjudication where sim_denial_flag")
        ).scalar()
        total = conn.execute(
            text(f"select count(*) from {_SCHEMA}.sim_claim_adjudication")
        ).scalar()
    assert total == 20867, total
    # Base rate ~12.8%; a wildly different rate means the layer was regenerated
    # under a different calibration and every Phase 4 metric needs rerunning.
    assert 0.10 < denied / total < 0.16, f"{denied}/{total}"
