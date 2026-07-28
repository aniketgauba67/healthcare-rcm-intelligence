"""Model C moves the boundary to the denial. It does not remove it.

Model C predicts appeal success at triage, so a denials analyst's remittance
advice is legitimately in scope: what was denied, how much, under which category
and CARC group, when the payer adjudicated. Those are forbidden to Model A and
permitted here (`docs/simulated_forbidden_columns.md` §5, configured under
`model_c`). Widening the guard is the correct thing to do and also the easiest
place in this project to widen it one column too far, so every assertion below is
about a column that is nearly-but-not-quite on the right side of the line. The
columns that are obviously wrong were never the risk.

What stays forbidden is narrower and sharper, and this file pins the two calls
team-lead UPHELD at GATE 1:

1. `sim_denial_driver_mechanism` stays forbidden EVEN FOR MODEL C. It is the
   generator's statement of WHY a denial happened and appears on no remittance
   advice anyone has ever read. Admitting it would invert the §4.5 firewall
   through a column name — the one place the boundary is easiest to lose,
   because every other denial column around it is permitted.

2. `sim_appeal_disputed_amount` stays out, with `sim_denied_amount` as the
   legitimate substitute for the same economic quantity. The two coincide
   numerically and not in time: the denied amount is on the remittance advice at
   triage, the disputed amount is a fact about an appeal not yet filed.

Call 2 rests on an equality — disputed == denied on all 967 appealed claims —
that the Expected Net Recovery score depends on and that nothing in the suite
re-measured. That is the covered-days defect class: a fact checked once by hand,
relied on afterwards, and invisible if a future load breaks it. The integration
test below re-measures it.

PROVENANCE OF THIS FILE (qa-reviewer-p11): qa-reviewer-p10 and ml-engineer-3
independently wrote files at this path on branches that had not met, so the merge
arrived as an add/add conflict. Neither could see the other's; this is a naming
collision and NOT an ownership breach, and it is recorded here so a future reader
does not reconstruct it as one. Resolved as a UNION rather than a choice — ml's
parametrized sweep of the permitted/forbidden boundary is good work qa would have
written substantially the same way, and qa's two gate assertions (the
read-then-drop check on the extract query, and the live re-measurement of the
equality) are the ones phase acceptance turns on. Dropping either half to resolve
a conflict would have silently lost coverage that was already paid for.
"""

from __future__ import annotations

import pandas as pd
import pytest
from sqlalchemy import text

from src.features.extract import APPEAL_TARGET_QUERY, DENIAL_QUERY
from src.features.leakage import (
    LeakageError,
    assert_no_forbidden_columns,
    forbidden_columns,
    load_model_config,
)

_MECHANISM = "sim_denial_driver_mechanism"
_DISPUTED = "sim_appeal_disputed_amount"
_DENIED = "sim_denied_amount"

# What the remittance advice actually carries: what was denied, how much, under
# which category and CARC group, and when the payer adjudicated it.
_REMITTANCE = (
    "sim_denial_flag",
    "sim_denial_type",
    "sim_denial_category",
    "sim_denial_carc_group",
    "sim_denied_amount",
    "sim_allowed_amount",
    "sim_paid_amount",
    "sim_patient_responsibility_amount",
    "sim_contractual_adjustment_amount",
    "sim_adjudication_date",
    "sim_denial_review_date",
    "sim_days_to_adjudication",
)


@pytest.mark.parametrize("column", _REMITTANCE)
def test_the_remittance_advice_is_available_to_model_c(column: str) -> None:
    """Forbidden to A, permitted to C. If this fails, the boundary collapsed onto A's."""
    with pytest.raises(LeakageError):
        assert_no_forbidden_columns([column], model="A")
    assert_no_forbidden_columns([column], model="C")


def test_the_driver_mechanism_is_forbidden_for_both_models() -> None:
    """The whole §5 boundary in one column: permitted neighbours, forbidden itself.

    It sits next to the denial category in the schema and is forbidden while the
    category is permitted, which is exactly why it needs its own test. A category
    is a CARC group on a remittance advice that a human being can read. The
    driver mechanism is the simulation's internal statement of what caused the
    denial, appears on no remittance advice anyone has ever worked, and admitting
    it would invert the CLAUDE.md §4.5 firewall through a column name.
    """
    assert _MECHANISM in forbidden_columns("A")
    assert _MECHANISM in forbidden_columns("C"), (
        "sim_denial_driver_mechanism became permitted for Model C. Team-lead UPHELD it as "
        "forbidden even there: it is the generator's account of WHY, not a remittance fact."
    )
    for model in ("A", "C"):
        with pytest.raises(LeakageError, match=_MECHANISM):
            assert_no_forbidden_columns([_DENIED, _MECHANISM], model=model)


def test_the_permitted_denial_neighbours_really_are_permitted_for_model_c() -> None:
    """Sharpens the test above: it must fail on the mechanism, not on everything."""
    permitted = forbidden_columns("C")
    for column in ("sim_denial_category", "sim_denial_carc_group", "sim_denial_type", _DENIED):
        assert column not in permitted, f"{column} is on the remittance advice and Model C needs it"
    assert_no_forbidden_columns(
        ["sim_denial_category", "sim_denial_carc_group", "sim_denial_type", _DENIED], model="C"
    )


@pytest.mark.parametrize(
    "column",
    [
        "sim_appeal_sk",
        "sim_appeal_level",
        "sim_appeal_filed_date",
        "sim_appeal_decision_date",
        "sim_appeal_outcome",
        "sim_appeal_disputed_amount",
        "sim_appeal_recovered_amount",
        "sim_appeal_latent_p",
    ],
)
def test_no_appeal_column_is_a_model_c_feature(column: str) -> None:
    """Every sim_appeals column postdates the decision being predicted.

    Including the two that are targets: a target is not a feature, and the only
    thing standing between those two roles is this guard.
    """
    with pytest.raises(LeakageError, match=column):
        assert_no_forbidden_columns([column], model="C")


def test_the_disputed_amount_is_forbidden_and_its_substitute_is_not() -> None:
    """The two coincide numerically; they do not coincide in time.

    sim_appeal_disputed_amount equals sim_denied_amount on all 967 level-1
    appeals — re-measured against live Postgres by the integration test below —
    so the recoverable amount the Expected Net Recovery score needs is available
    from the permitted column. That is not a workaround. The denied amount is on
    the remittance advice at triage time; the disputed amount is a fact about an
    appeal nobody has filed yet, and reading it would mean the score could only
    be computed for claims that were going to be appealed anyway.
    """
    assert _DISPUTED in forbidden_columns("C")
    assert _DENIED not in forbidden_columns("C")
    with pytest.raises(LeakageError, match=_DISPUTED):
        assert_no_forbidden_columns([_DISPUTED], model="C")
    assert_no_forbidden_columns([_DENIED], model="C")


@pytest.mark.parametrize(
    "column",
    [
        "sim_latent_p",
        "sim_provider_quality_latent",
        "sim_label_noise_applied",
        "sim_appeal_latent_p",
    ],
)
def test_the_latent_internals_are_forbidden_to_every_model(column: str) -> None:
    """§1 of the firewall document does not relax at any boundary."""
    for model in ("A", "C"):
        with pytest.raises(LeakageError, match=column):
            assert_no_forbidden_columns([column], model=model)


def test_the_payment_is_still_forbidden_after_the_denial() -> None:
    """A payment posting after an appeal IS the appeal's result."""
    for column in ("sim_payment_date", "sim_days_to_payment"):
        with pytest.raises(LeakageError, match=column):
            assert_no_forbidden_columns([column], model="C")


def test_model_c_relaxes_the_guard_and_does_not_replace_it() -> None:
    """C's blacklist must be A's minus the remittance, not a shorter list of its own.

    Written as a set relation rather than a count so that a future column added
    to A's list is automatically covered here: the only permitted difference
    between the two blacklists is the configured `permitted_beyond_model_a`.
    """
    config = load_model_config()
    relaxed = forbidden_columns("A", config) - forbidden_columns("C", config)
    permitted = set(config["model_c"]["permitted_beyond_model_a"])
    assert relaxed == permitted, (
        "Model C's guard differs from Model A's by something other than the configured "
        f"post-denial facts: unexpected relaxations {sorted(relaxed - permitted)}"
    )


def test_the_crosswalk_stays_forbidden_to_model_c_too() -> None:
    """Display-only linkage is display-only at every boundary (CLAUDE.md §3.4)."""
    for column in ("sim_facility_ccn", "facility_ccn", "real_npi"):
        with pytest.raises(LeakageError, match=column):
            assert_no_forbidden_columns([column], model="C")


def test_the_extract_queries_do_not_read_what_model_c_may_not_use() -> None:
    """Blocked at the query, not only at the guard — the cdc93e3 precedent.

    When `clm_utlztn_day_cnt` was classified forbidden, team-lead upheld removing it
    from the extract query as well as the spec, "so it is never read rather than read
    and then dropped". The same standard applies here.
    """
    assert _MECHANISM not in DENIAL_QUERY

    assert _DISPUTED not in APPEAL_TARGET_QUERY, (
        f"{_DISPUTED} is forbidden for Model C but APPEAL_TARGET_QUERY selects it, and "
        "src/features/appeal.py drops it inline before the merge. Nothing in the code uses "
        "it, so this is a read-then-drop of a forbidden column — the pattern the "
        "clm_utlztn_day_cnt ruling rejected. If it is read to verify the "
        "disputed == denied equality, that check belongs in a test (see below), not in the "
        "feature path; if it is not, stop selecting it."
    )


@pytest.mark.integration
def test_the_disputed_equals_denied_equality_still_holds() -> None:
    """Re-measure the fact the Expected Net Recovery recoverable amount rests on.

    `sim_denied_amount` is used in place of `sim_appeal_disputed_amount` because the
    two are equal. If a future simulation load breaks that, the substitution stops
    being a provenance nicety and starts being wrong, and only this test would say so.
    """
    from src.ingestion.load_postgres import database_url

    url = database_url()
    if not url:
        pytest.skip("no Postgres configured (set POSTGRES_* in .env)")

    from sqlalchemy import create_engine

    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            frame = pd.read_sql(
                text(
                    """
                    select ap.claim_sk,
                           ap.sim_appeal_disputed_amount,
                           adj.sim_denied_amount
                    from rcm.sim_appeals ap
                    join rcm.sim_claim_adjudication adj on adj.claim_sk = ap.claim_sk
                    where ap.sim_appeal_level = 1
                    """
                ),
                conn,
            )
    except Exception as exc:  # noqa: BLE001 - any connection error means skip
        pytest.skip(f"Postgres unreachable ({exc}); run `docker compose up -d`")

    if frame.empty:
        pytest.skip("no level-1 appeals in the warehouse; run `make simulate-warehouse`")

    mismatched = frame[(frame[_DISPUTED] - frame[_DENIED]).abs() > 0.005]
    assert mismatched.empty, (
        f"{len(mismatched)} of {len(frame)} level-1 appeals no longer have "
        "sim_appeal_disputed_amount == sim_denied_amount. Model C's Expected Net Recovery "
        "reads the denied amount as the recoverable amount on the strength of that equality; "
        "re-examine the substitution rather than inheriting it."
    )
