"""§3.2 across a PASS-THROUGH boundary — the one the perturbation probe cannot see.

QA-AUTHORED REVIEW GATE (`tests/leakage/` is qa's under the 2026-07-27 ownership
ruling). Do not delete it to go green.

STATUS AT THE TIME OF WRITING: **RED on `src/api/`**, measured on
feat/phase5-qa @ 21d75d4 (= main a04d38c + feat/phase5-blockers f18dfc7 +
feat/phase5-app 08d88cc). Red is the correct state; this file is the gate that
says when it has been fixed.

WHY A SECOND INSTRUMENT, WHEN ONE ALREADY EXISTS
------------------------------------------------
`tests/leakage/test_output_surface_provenance.py` closed the "nobody checks this
boundary" hole for Phase 5 and its route detector correctly fires on
`src/api/main.py`. The obvious next step — register the module as an
`exposure.Surface` and let the probe measure it — was tried and MEASURED FIRST,
and it does not work:

    surface: src/api/main.py::work_queue(heuristic), stub data source,
             input frame shaped like rcm.vw_work_queue_priority
    result:  ZERO emitted columns reported as unmarked-simulated —
             including `sim_dollars_at_stake`, the column the API re-marks
             precisely because it is simulated money.

The probe perturbs a simulated INPUT and reports emitted columns that MOVE, so it
can only see columns the surface COMPUTES. `src/models/work_queue.py` computes
its columns, which is what the probe was built for. The API read side computes
nothing: every column arrives already built by a curated view and is copied to the
wire, so nothing moves and everything reads clean. **Registering the API as a
surface would have turned this gate green and proved nothing** — the same shape as
the MATCHER-EXPRESSIVENESS rule, where the instrument that runs is weaker than the
green implies. `test_the_perturbation_probe_is_blind_across_a_passthrough_boundary`
pins that limitation so it cannot be rediscovered as a surprise.

WHAT REPLACES IT
----------------
Across a pass-through boundary, provenance can only come from a DECLARATION.
Two exist, and neither is qa's:

* the view SQL — the register (docs/provenance_register.md:167) names each view's
  header block as "the register's cited source of truth" for per-column class;
* `config/model.yaml: forbidden_derived_features` — ml's list of view columns that
  are functions of simulated columns, each with ml's own stated reason.

This gate cross-references the second against the columns the API actually emits.
Keying on ml's config rather than on a qa-authored list is deliberate: the verdict
"this column is made of simulated data" is then the project's own, already written
down for a different purpose, and a qa reviewer cannot widen it by opinion.

WHAT IS DELIBERATELY NOT REPORTED
---------------------------------
`forbidden_source_features` (`medicare_source_paid_amt`, `clm_pmt_amt`) are
forbidden as FEATURES because a biller does not know them before submission. They
are SOURCE values — real CMS synthetic claim fields — so carrying no `sim_` marker
is CORRECT for them, and reporting them would be over-firing of exactly the kind
that fills an exemption list until a real finding gets waved through with the rest.
`test_a_source_forbidden_column_is_not_reported` is the control.
"""

from __future__ import annotations

import pathlib
from typing import Any

import pandas as pd
import pytest
import yaml

from tests.leakage import exposure, wire

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MODEL_CONFIG = REPO_ROOT / "config" / "model.yaml"

#: The 16 output columns of `rcm.vw_work_queue_priority`, verified against the
#: live catalog on 2026-07-29. Pinned so a parser regression is visible in CI,
#: where there is no database to compare against.
WORK_QUEUE_COLUMNS = [
    "claim_sk",
    "clm_id",
    "prvdr_num",
    "sim_facility_name",
    "sim_payer_id",
    "sim_action_type",
    "sim_denial_flag",
    "sim_denial_category",
    "sim_denial_type",
    "sim_ar_open_flag",
    "sim_age_days",
    "sim_dollars_at_stake",
    "sim_heuristic_priority_score",
    "sim_priority_tier",
    "sim_appeal_levels",
    "is_heuristic_placeholder",
]

#: Columns that ARE functions of simulated data and legitimately carry no marker,
#: under team-lead's RULING C applied to outputs: a rank, a tier, a
#: recommendation, a query parameter are statements about OUR process. Each
#: carries its reason, and `test_no_wire_exemption_is_speculative` refuses any
#: entry the measurement would not otherwise have reported.
#:
#: No view-derived field is exempt: simulated values retain their marker at the
#: source instead of relying on an API-only carve-out.
PROCESS_METADATA: dict[str, str] = {}


def _model_config() -> dict[str, Any]:
    return yaml.safe_load(MODEL_CONFIG.read_text())


def simulated_derived_columns() -> dict[str, str]:
    """View columns ml has classified as functions of simulated columns."""
    return dict(_model_config()["forbidden_derived_features"])


def source_forbidden_columns() -> dict[str, str]:
    return dict(_model_config().get("forbidden_source_features") or {})


# ---------------------------------------------------------------------------
# The parser is an instrument, so it is checked
# ---------------------------------------------------------------------------


def test_the_parser_reads_the_work_queue_projection() -> None:
    """Static control on the parser, pinned to a hand-checked view."""
    path = wire.VIEWS_DIR / "vw_work_queue_priority.sql"
    assert wire.view_output_columns(path) == WORK_QUEUE_COLUMNS


def test_the_parser_reads_an_alias_and_a_case_expression() -> None:
    """A `case` in the select list must not be mistaken for the projection edge."""
    columns = wire.view_output_columns(
        _tmp_sql(
            "create or replace view rcm.vw_x as\n"
            "select a.claim_sk,\n"
            "       case when a.sim_denial_flag then 'X' else 'Y' end as action_type,\n"
            "       round(a.sim_denied_amount, 2) as dollars_at_stake,\n"
            "       (select max(z) from t) as latest\n"
            "from a;\n"
        )
    )
    assert columns == ["claim_sk", "action_type", "dollars_at_stake", "latest"]


def test_every_view_parses_to_a_plausible_projection() -> None:
    """No view may parse to nothing, or to something with SQL still in it."""
    for name, columns in wire.view_columns_by_name().items():
        assert columns, f"{name}: parsed to no columns"
        bad = [c for c in columns if not c.isidentifier()]
        assert not bad, (
            f"{name}: parsed column names that are not identifiers: {bad}. The parser has "
            "drifted from the SQL; add the view to wire.UNPARSEABLE_VIEWS WITH A REASON or "
            "fix the parse — do not let the gate keep running on a wrong column set."
        )


@pytest.mark.integration
def test_the_sql_parse_matches_the_live_catalog() -> None:
    """The instrument, checked against the thing it approximates.

    A parser that returns the wrong column set makes every assertion below green
    for the wrong reason. Read-only: `information_schema` only.
    """
    from sqlalchemy import create_engine, text

    from src.ingestion.load_postgres import database_url

    url = database_url()
    if not url:
        pytest.skip("no Postgres configured (set POSTGRES_* in .env)")
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "select table_name, column_name from information_schema.columns "
                    "where table_schema = 'rcm' and table_name like 'vw\\_%' "
                    "order by table_name, ordinal_position"
                )
            ).fetchall()
    except Exception as error:  # noqa: BLE001 - any connection error means skip
        pytest.skip(f"Postgres unavailable: {error}")

    live: dict[str, list[str]] = {}
    for table, column in rows:
        live.setdefault(table, []).append(column)
    if not live:
        pytest.skip("no rcm.vw_* views in this warehouse (run `make views`)")

    mismatched: list[str] = []
    for name, parsed in wire.view_columns_by_name().items():
        if name not in live:
            continue
        if parsed != live[name]:
            mismatched.append(f"{name}:\n    parsed: {parsed}\n    live:   {live[name]}")
    assert not mismatched, (
        "the static SQL parse disagrees with the live catalog, so every wire-provenance "
        "assertion built on it is measuring the wrong column set:\n  " + "\n  ".join(mismatched)
    )


def _tmp_sql(body: str) -> pathlib.Path:
    import tempfile

    handle = tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False)
    handle.write(body)
    handle.close()
    return pathlib.Path(handle.name)


# ---------------------------------------------------------------------------
# The limitation this file exists for, pinned
# ---------------------------------------------------------------------------


def test_the_perturbation_probe_is_blind_across_a_passthrough_boundary() -> None:
    """MEASURED 2026-07-29. The reason `exposure.py` is not the instrument here.

    A surface that copies a pre-computed simulated column to the wire reports
    ZERO dependence, because nothing downstream of the view moves when a
    simulated input to the SURFACE moves — the view already ran. If this test
    ever fails, the probe has gained the ability to see across the boundary and
    the argument in this module's docstring needs revisiting; until then, a green
    `test_every_user_facing_emitter_is_registered` on an API module is not
    evidence that the API's columns carry their provenance.
    """
    frame = wire.frame_like(WORK_QUEUE_COLUMNS)

    def passthrough(f: pd.DataFrame) -> pd.DataFrame:
        return f.copy()

    surface = exposure.Surface(name="control/passthrough", build=passthrough, frame=frame)
    offenders = exposure.unmarked_simulated_columns(surface)
    assert offenders == {}, (
        "the perturbation probe now reports unmarked columns on a pure pass-through, which "
        f"contradicts the measurement this gate is built on: {sorted(offenders)}"
    )
    # And the columns it stays silent about are exactly the ones this file reports.
    assert "sim_ar_open_flag" in frame.columns


# ---------------------------------------------------------------------------
# What the API actually puts on the wire
# ---------------------------------------------------------------------------


class _StubSource:
    """A data source made of frames, so a route can be driven with no database."""

    kind = "bundle"

    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self._frames = frames

    def frame(self, dataset: str) -> pd.DataFrame:
        return self._frames[dataset]

    def available(self) -> set[str]:
        return set(self._frames)

    def describe(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "path": "stub",
            "git_commit": "0" * 40,
            "git_tree_dirty": False,
            "built_at_utc": "2026-01-01T00:00:00Z",
            "source_vintages": {},
        }


def _payload_field_names(payload: Any, prefix: str = "") -> list[str]:
    """Every field name anywhere in a nested response, dotted."""
    names: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            names.append(f"{prefix}{key}")
            names.extend(_payload_field_names(value, f"{prefix}{key}."))
    elif isinstance(payload, (list, tuple)) and payload:
        names.extend(_payload_field_names(payload[0], f"{prefix}[]."))
    return names


def _wire_fields(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """Route -> the field names its response body carries.

    The routes are called directly rather than over HTTP because the point is the
    payload, and a TestClient would add a live data source to the requirements of
    a CI gate.
    """
    from src.api import main as api_main

    frames = {
        "vw_work_queue_priority": wire.frame_like(WORK_QUEUE_COLUMNS),
        "vw_claim_enriched": wire.frame_like(
            wire.view_output_columns(wire.VIEWS_DIR / "vw_claim_enriched.sql")
        ),
        "vw_executive_rcm_summary": wire.frame_like(
            wire.view_output_columns(wire.VIEWS_DIR / "vw_executive_rcm_summary.sql")
        ),
    }
    source = _StubSource(frames)
    monkeypatch.setattr(api_main, "_source", lambda: source)
    monkeypatch.setattr(api_main, "_frame", lambda dataset: frames[dataset])

    responses = {
        "GET /work-queue?queue_mode=heuristic": api_main.work_queue(
            queue_mode="heuristic", tier=None, limit=10, offset=0, role="analyst"
        ),
        "GET /claims/{claim_id}": api_main.get_claim(claim_id="1", role="analyst"),
        "GET /metrics/executive": api_main.executive_metrics(include_monthly=True),
    }
    return {
        name: _payload_field_names(response.model_dump()) for name, response in responses.items()
    }


def test_the_api_computes_no_row_column_of_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    """The premise of the pass-through declaration, checked rather than assumed.

    `PASSTHROUGH_MODULES` in tests/leakage/test_output_surface_provenance.py
    exempts `src/api/main.py` from the perturbation probe on the grounds that it
    computes none of its columns. That is true today and nothing enforced it: the
    day a route adds a column of its own, the probe would be the right instrument
    again and the exemption would be silently wrong. So the premise is the test —
    every row column must be a column of the view it came from, or a declared
    re-marking of one.
    """
    from src.api import main as api_main
    from src.api.tables import RE_MARKED_COLUMNS

    frames = {"vw_work_queue_priority": wire.frame_like(WORK_QUEUE_COLUMNS)}
    source = _StubSource(frames)
    monkeypatch.setattr(api_main, "_source", lambda: source)
    monkeypatch.setattr(api_main, "_frame", lambda dataset: frames[dataset])

    rows = api_main.work_queue(
        queue_mode="heuristic", tier=None, limit=5, offset=0, role="analyst"
    ).model_dump()["rows"]
    assert rows, "the fixture produced no rows, so this test measures nothing"

    unmarking = {new: old for old, new in RE_MARKED_COLUMNS.items()}
    invented = sorted(
        column for column in rows[0] if unmarking.get(column, column) not in set(WORK_QUEUE_COLUMNS)
    )
    assert not invented, (
        "the /work-queue route emits column(s) that the view does not: "
        f"{invented}\nThe API is no longer a pure pass-through, so the pass-through "
        "declaration in tests/leakage/test_output_surface_provenance.py::PASSTHROUGH_MODULES "
        "no longer holds. Register `src/api/main.py` as a probed `exposure.Surface` instead, "
        "or declare the new column as a re-marking."
    )


def test_no_simulated_derived_column_reaches_the_wire_unaccounted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED. `config/model.yaml` says these columns are made of simulated data.

    A reader of a JSON body sees the field name and nothing else — the same
    argument that decided [QUEUE-PREFIX] at the CSV header, one layer further out.
    """
    classified = simulated_derived_columns()
    offenders: list[str] = []
    for route, fields in _wire_fields(monkeypatch).items():
        for field in fields:
            leaf = field.split(".")[-1]
            if leaf in classified and "sim_" not in leaf and leaf not in PROCESS_METADATA:
                offenders.append(f"{route}: `{field}` — {classified[leaf]}")

    assert not offenders, (
        f"{len(offenders)} field(s) reach the wire without a `sim_` marker while "
        "config/model.yaml `forbidden_derived_features` classifies them as functions of "
        "simulated columns (docs/project_rules.md §3.2):\n  "
        + "\n  ".join(sorted(offenders))
        + "\n\nThis is the [QUEUE-PREFIX] defect at the API boundary, and it is invisible to "
        "tests/leakage/test_output_surface_provenance.py because the API computes none of "
        "these columns — see this module's docstring. Two fixes are legitimate: re-mark the "
        "column on the way out (src/api/tables.py::RE_MARKED_COLUMNS already does this for "
        "`dollars_at_stake` and `heuristic_priority_score`), or correct the view so the "
        "marker is never lost. Declaring one process metadata is legitimate ONLY for a rank, "
        "a tier, a recommendation or a query parameter, and the declaration must carry its "
        "reason."
    )


def test_a_source_forbidden_column_is_not_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """Negative control. A gate that flags SOURCE columns is a gate nobody can use.

    `medicare_source_paid_amt` is forbidden as a FEATURE and is a real CMS
    synthetic claim field, so it is correctly unmarked. If this ever fails, the
    gate above has started conflating §4 leakage with §3 provenance and its
    findings can no longer be trusted to mean what they say.
    """
    fields = {f.split(".")[-1] for route in _wire_fields(monkeypatch).values() for f in route}
    on_the_wire = fields & set(source_forbidden_columns())
    assert on_the_wire, (
        "no SOURCE-forbidden column reaches the wire in this fixture, so this control is "
        "vacuous — it must exercise the case it exists to rule out"
    )
    classified = simulated_derived_columns()
    assert not (on_the_wire & set(classified)), (
        "a SOURCE column is also classified as simulated-derived; the two lists in "
        "config/model.yaml disagree and the gate above would report a real claim field as "
        f"a provenance defect: {sorted(on_the_wire & set(classified))}"
    )


def test_no_wire_exemption_is_speculative(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every process-metadata exemption must be one the gate would otherwise report."""
    classified = simulated_derived_columns()
    on_the_wire = {f.split(".")[-1] for route in _wire_fields(monkeypatch).values() for f in route}
    stale = [
        f"`{column}` ({reason})"
        for column, reason in PROCESS_METADATA.items()
        if column not in on_the_wire or column not in classified
    ]
    assert not stale, (
        "these columns are exempted as process metadata but the gate would not have reported "
        "them anyway, so the exemption is a place to pre-emptively excuse things:\n  "
        + "\n  ".join(stale)
    )


def test_the_apps_own_exemption_list_states_a_reason_per_column() -> None:
    """RED. Two exemption lists, same job, and only one of them can be argued with.

    `tests/leakage/test_output_surface_provenance.py` requires a REASON per
    exemption and has `test_no_exemption_is_speculative` to refuse entries the
    probe would never have reported. `src/api/tables.py::PROCESS_METADATA_COLUMNS`
    is a bare `frozenset` of names with neither property, and it is the one that
    decides what the wire actually carries. That is how `action_type` — which
    config/model.yaml:192 calls a `case` on `sim_denial_flag` that "encodes the
    label directly" — came to be declared a statement about our process.
    """
    from src.api import tables

    declared = getattr(tables, "PROCESS_METADATA_COLUMNS")
    assert isinstance(declared, dict) and all(
        isinstance(reason, str) and reason.strip() for reason in declared.values()
    ), (
        "src/api/tables.py::PROCESS_METADATA_COLUMNS is "
        f"{type(declared).__name__} of {len(declared)} names with no reasons. Make it a "
        "mapping of column -> reason, the same shape the qa gate requires, so that an "
        "exemption has to be argued for in the file that grants it. Columns currently "
        f"exempted with no stated reason: {sorted(declared)}"
    )


def test_no_label_bearing_column_is_exempted_as_process_metadata() -> None:
    """RULING C exempts a rank, a tier, a recommendation — not a restatement of the label.

    Cross-config, so the verdict is not a QA opinion: a column `config/model.yaml`
    forbids because it is built on `sim_denial_flag` (the LABEL) cannot
    simultaneously be a statement about our process.

    THE `priority_tier` CARVE-OUT IS GONE (qa-reviewer-p18, 2026-07-29), and its
    removal is the point rather than a tightening for its own sake. This test used
    to subtract `{"priority_tier"}` by name, which meant a reviewer's opinion
    overrode ml's recorded reason and the two live instruments disagreed about one
    column: `config/model.yaml:186` forbids it as "built on sim_denial_flag", while
    `tests/features/test_demo_bundle_provenance.py` (ml's own, 21fe077) flags it
    unmarked in the bundle and `src/api/tables.py` exempts it as a rank.

    MEASURED at the source, `sql/views/vw_work_queue_priority.sql:99`:

        ntile(4) over (order by heuristic_priority_score desc) as priority_tier

    so the EXEMPTION is factually right and the model.yaml REASON is the inaccurate
    one — it is a rank over our own heuristic, transitively a function of simulated
    money, which is why forbidding it as a FEATURE is still correct. The fix is one
    line of wording in `config/model.yaml` (ml-engineer): say it is an ntile over
    `heuristic_priority_score`, and the `"ntile" not in reason` filter below exempts
    it with ml's own words. Until then this is red, and red is the honest state for
    two gates that contradict each other about a published column.
    """
    from src.api import tables

    classified = simulated_derived_columns()
    label_bearing = {
        column: reason
        for column, reason in classified.items()
        if "sim_denial_flag" in reason and "ntile" not in reason
    }
    offenders = sorted(set(tables.PROCESS_METADATA_COLUMNS) & set(label_bearing))
    assert not offenders, (
        "src/api/tables.py exempts as process metadata column(s) that config/model.yaml "
        "forbids because they are built on the LABEL:\n  "
        + "\n  ".join(f"`{c}` — {label_bearing[c]}" for c in offenders)
        + "\nA reader seeing `action_type: DENIAL_REWORK` is reading `sim_denial_flag` under "
        "another name. Mark it, or drop it from the response."
    )


# ---------------------------------------------------------------------------
# Membership: the label-bearing property no column name can express
# ---------------------------------------------------------------------------

_MEMBERSHIP_TERMS = ("denied", "open ar", "open-ar", "actionable", "selected", "selection")


def test_the_work_queue_response_discloses_its_membership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED. The queue's label-bearing property is MEMBERSHIP, not any column.

    `vw_work_queue_priority`'s where clause selects denied-or-open-AR claims, so
    the list already knows the outcome; no column-name blacklist can express that,
    and a response describing only its ORDER presents a filtered set as a neutral
    one. The disclosure has to travel with the payload — a JSON body is read
    without the page around it.
    """
    from src.api import main as api_main

    frames = {"vw_work_queue_priority": wire.frame_like(WORK_QUEUE_COLUMNS)}
    source = _StubSource(frames)
    monkeypatch.setattr(api_main, "_source", lambda: source)
    monkeypatch.setattr(api_main, "_frame", lambda dataset: frames[dataset])

    response = api_main.work_queue(
        queue_mode="heuristic", tier=None, limit=5, offset=0, role="analyst"
    )
    prose = " ".join(
        str(value)
        for key, value in response.model_dump().items()
        if key in {"ranking", "ordering_caveat", "limitations", "membership"}
    ).lower()

    assert any(term in prose for term in _MEMBERSHIP_TERMS), (
        "the /work-queue response describes how its rows are ORDERED and never states which "
        "claims are IN it. The view selects `where sim_denial_flag or ar_open_flag`, so "
        "membership in this list is itself a simulated outcome — every row is a claim the "
        "simulation already denied or left unpaid. Say so in the payload (the `ranking` text "
        "or a dedicated field), not only in the view header a caller never sees.\n"
        f"prose checked: {prose[:400]}"
    )
