"""A `sim_` marker earned in the view must survive out onto the API wire.

QA-AUTHORED REVIEW GATE (qa owns `tests/`). It is RED on the tree that added it,
by design. Do not delete it to go green; fix the response models.

WHY THIS EXISTS. Commit 01b1a62 gave the `sim_` marker to the 65 simulated
columns of the demo bundle, and `tests/contracts/test_bundle_column_provenance.py`
now holds the bundle to it. That guard reads the BUNDLE and nothing else. The
FastAPI service reads the SAME views and is at least as exposed, and there the
rename went only half way: `src/api/tables.py` was updated to READ
`sim_claims_submitted`, `sim_denied_claims`, `sim_avg_days_to_payment` and the
rest, but it still WRITES them onto the wire under their bare pre-rename names.

That is worse than leaving them all bare. `GET /metrics/executive` now returns
`sim_net_collection_rate` and `denial_rate` as siblings in one JSON object, and
`meta.notice` tells the reader "Every sim_-prefixed field in this response is
SIMULATED". A reader who believes the notice concludes that the eight unmarked
siblings are real — and every one of them is a rate or a count over this
project's simulated adjudication layer. CLAUDE.md §1 makes "no simulated value
presented as real" the property the project rests on; an absent marker inside a
mixed object is no longer silence, it is an assertion.

`src/demo/spec.py` already states the rule this violates, for the opposite
direction: "coercing the header at the boundary would leave the source still
wrong and hide it." Here the boundary strips a marker the source had earned.

HOW IT CHECKS. Declaration-driven, like the bundle guard, and for the same
reason: a hardcoded list of the eight known names would go stale the moment a
ninth column is added. The declaration is the view SQL itself. For every bare
field a response model publishes, if the view behind that endpoint has the same
name carrying `sim_`, the marker was stripped at the boundary.
"""

from __future__ import annotations

import pathlib

import pydantic

from tests.leakage.wire import view_output_columns

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
VIEWS_DIR = REPO_ROOT / "sql" / "views"
MARKER = "sim_"

#: response model -> the view its endpoint reads. Only endpoints that pass a view
#: through are checkable this way; the others are named in the module docstring
#: of `test_wire_provenance.py`.
RESPONSE_MODEL_SOURCE_VIEW = {
    "ExecutiveMetricsResponse": "vw_executive_rcm_summary",
}


def _model_fields(name: str) -> list[str]:
    from src.api import schemas

    model = getattr(schemas, name)
    assert issubclass(model, pydantic.BaseModel), f"{name} is not a response model"
    return list(model.model_fields)


def test_the_wire_does_not_strip_a_marker_the_view_earned() -> None:
    """The finding this file exists for, in its most direct form."""
    stripped: list[str] = []
    for model_name, view in RESPONSE_MODEL_SOURCE_VIEW.items():
        view_columns = set(view_output_columns(VIEWS_DIR / f"{view}.sql"))
        for field in _model_fields(model_name):
            if field.startswith(MARKER):
                continue
            if f"{MARKER}{field}" in view_columns:
                stripped.append(f"{model_name}.{field} <- {view}.{MARKER}{field}")
    assert not stripped, (
        "these API response fields drop a `sim_` marker that the view they read already "
        "carries, so a caller sees a simulated quantity under a bare name -- next to "
        "marked siblings in the same object, which makes the bare name read as real:\n  "
        + "\n  ".join(sorted(stripped))
    )


def test_the_reason_code_naming_agrees_between_the_api_and_the_explainer() -> None:
    """One quantity, two surfaces, two spellings is the same defect one layer out.

    `src/models/explain.py` marks these `sim_reason_code` / `sim_analyst_action`
    because they are statements about a prediction of a SIMULATED denial, and the
    bundle ships them marked. `src/api/schemas.ReasonCode` publishes the identical
    pair bare. Whichever ruling is right, both surfaces have to hold it.
    """
    source = (REPO_ROOT / "src" / "models" / "explain.py").read_text()
    marked_in_explainer = sorted(
        name for name in ("sim_reason_code", "sim_analyst_action") if f'"{name}"' in source
    )
    assert marked_in_explainer, (
        "explain.py no longer emits either marked name, so this test would pass "
        "vacuously. Re-derive it against whatever the explainer emits now."
    )

    api_fields = set(_model_fields("ReasonCode"))
    disagreements = [
        f"explain.py emits {name} but schemas.ReasonCode publishes {name.removeprefix(MARKER)}"
        for name in marked_in_explainer
        if name.removeprefix(MARKER) in api_fields
    ]
    assert not disagreements, (
        "the same quantity carries the marker in one surface and not the other:\n  "
        + "\n  ".join(disagreements)
    )


# --------------------------------------------------------------------------
# Control. Unlike the four in test_bundle_column_provenance.py, this one drives
# the REAL function, so neutering that function fails this test too.
# --------------------------------------------------------------------------


def test_the_check_is_driven_by_the_view_and_fires_on_a_stripped_marker() -> None:
    """Feed the real check a view that marks a field the model publishes bare."""
    view_columns = set(view_output_columns(VIEWS_DIR / "vw_executive_rcm_summary.sql"))
    assert "sim_denial_rate" in view_columns, (
        "the parser stopped seeing the view's marked columns, so the check above would "
        "pass vacuously. That is the failure mode this control exists for."
    )
    assert "denial_rate" in _model_fields("ExecutiveMetricsResponse"), (
        "the response model no longer publishes the bare name; if that is a fix rather "
        "than a rename, update this control."
    )
