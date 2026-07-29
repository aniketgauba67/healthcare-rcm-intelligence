"""[FIREWALL-DOC-HOLE]: the §4.5 firewall leaks through documentation, on purpose.

QA-AUTHORED (tests/leakage/ is qa's). Gating the item team-lead swept onto the
Phase 5 board 2026-07-29. The ruling is written out in full as
`docs/assumptions.md` §12; this file is the part of it a build can check.

THE RULING, IN ONE LINE
-----------------------
CLAUDE.md §4.5 firewalls ml-engineer from `src/simulation/`, not from `docs/` or
`config/`, and the generator's realized output, its internals and its entire
latent formula are all published there. So the hole is REAL and is NOT FIXABLE by
redaction — every candidate for deletion is required by CLAUDE.md §1 or §7. It is
recorded as a known limitation instead, and what gets enforced is that the
disclosure does not GROW into the one shape that would be materially worse.

WHY REDACTION IS THE WRONG FIX (measured, not argued)
-----------------------------------------------------
* The realized denial rate is `sim_denial_flag.mean()` on the committed matrix —
  it is the LABEL. `test_the_realized_rate_is_derivable_from_the_label` measures
  it. An agent that trains on a label knows its base rate; deleting 12.8% from a
  document removes an auditable record and restores nothing.
* The oracle ceiling and the competitive-baseline caveat are what make a shipped
  ROC-AUC of 0.6254 an honest result near a known limit rather than a weak one.
  Deleting them would trade honesty for a wall with no other sides.

WHAT IS ACTUALLY ENFORCED
-------------------------
1. The limitation stays recorded. Deleting §12 silently is how a known limitation
   becomes an unknown one, and this project has already watched an engineer scrub
   an honest record to clear a check.
2. No REALIZED PER-CLAIM generator output reaches the feature store or a
   published artifact. Reading that the formula exists is a reconstruction
   someone *could* do; shipping `sim_latent_p` as a column is that reconstruction
   already done for them, and that is the line worth defending.
3. The ruling's own premises are still true. If `sim_denial_flag` ever stopped
   being the label, or `sim_latent_p` were no longer forbidden, the reasoning in
   §12 would need redoing rather than inheriting — so the premises are asserted,
   not assumed.
"""

from __future__ import annotations

import pathlib

import pandas as pd
import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ASSUMPTIONS = REPO_ROOT / "docs" / "assumptions.md"
MODEL_CONFIG = REPO_ROOT / "config" / "model.yaml"
MATRIX = REPO_ROOT / "artifacts" / "features" / "model_a_training_matrix.parquet"

# Generator internals that must never become a per-claim column anywhere a model
# or a reader can pick them up. Naming them in prose is the documented, accepted
# leak; shipping them as DATA is the failure this test exists for.
LATENT_COLUMNS = ("sim_latent_p", "sim_provider_quality_latent")

PUBLISHED_ROOTS = ("artifacts", "models_artifacts", "dashboard/demo_data")


@pytest.fixture(scope="module")
def assumptions_text() -> str:
    return ASSUMPTIONS.read_text()


# --- 1. the limitation stays recorded -------------------------------------


def test_the_firewall_limitation_is_recorded(assumptions_text: str) -> None:
    """§12 must exist and must say what it says.

    Checked on substance rather than on a heading number, so renumbering the
    document does not read as a finding — a check that misfires on a correct
    edit gets silenced, and a silenced check is worse than none.
    """
    lowered = assumptions_text.lower()
    required = {
        "the limitation is stated at all": "known limitation",
        "the firewall is not described as an information barrier": ("not an information barrier"),
        "the §4.5 reference is present": "4.5",
        "the shipped result is quoted as the evidence": "0.6254",
        "the reconstructible ceiling is quoted": "0.68",
    }
    missing = [why for why, needle in required.items() if needle.lower() not in lowered]
    assert not missing, (
        f"{ASSUMPTIONS.relative_to(REPO_ROOT)} no longer records the [FIREWALL-DOC-HOLE] "
        f"limitation: {missing}. This section is the project's written admission that "
        "CLAUDE.md §4.5 firewalls source files and not documentation. Removing it does not "
        "close the hole — it only stops the hole being disclosed, which is a §1 problem, not "
        "a fix."
    )


# --- 2. the premises the ruling rests on ----------------------------------


def test_the_realized_rate_is_derivable_from_the_label() -> None:
    """The measurement behind "Class A is not fixable by redaction".

    If this ever fails, `sim_denial_flag` has stopped being in the matrix and the
    ruling in §12 must be re-derived rather than inherited.
    """
    if not MATRIX.exists():
        pytest.skip(f"no committed matrix at {MATRIX}")
    frame = pd.read_parquet(MATRIX, columns=["sim_denial_flag"])
    rate = float(frame["sim_denial_flag"].mean())
    assert 0.0 < rate < 1.0, (
        "sim_denial_flag is degenerate in the committed matrix; §12's measurement is stale"
    )
    # Documented as 12.8%; the band is wide because the point of the assertion is
    # that the number is COMPUTABLE by anyone holding the matrix, not what it is.
    assert 0.05 < rate < 0.25, (
        f"realized denial rate {rate:.4f} has moved far from the 12.8% recorded in "
        f"{ASSUMPTIONS.relative_to(REPO_ROOT)} §1/§12. Update the document — a stale realized "
        "figure in an assumptions doc is the honesty defect, not the leak."
    )


def test_the_latent_probability_is_still_forbidden_as_a_feature() -> None:
    """§12 leans on `sim_latent_p` being unreachable AS DATA. Assert it, don't assume it."""
    config = yaml.safe_load(MODEL_CONFIG.read_text())
    forbidden = " ".join(str(v) for v in config.values())
    assert "sim_latent_p" in forbidden, (
        f"sim_latent_p is no longer named anywhere in {MODEL_CONFIG.relative_to(REPO_ROOT)}. "
        "CLAUDE.md §4 requires it be validation-only and never a feature, and §12 of the "
        "assumptions doc rests on that."
    )


# --- 3. the disclosure must not grow into shipped data --------------------


def test_no_generator_internal_reaches_the_feature_store() -> None:
    """The reconstruction must stay something someone would have to DO."""
    if not MATRIX.exists():
        pytest.skip(f"no committed matrix at {MATRIX}")
    import pyarrow.parquet as pq

    columns = set(pq.read_schema(MATRIX).names)
    present = sorted(columns.intersection(LATENT_COLUMNS))
    assert not present, (
        f"generator internals in the committed training matrix: {present}. Documenting that "
        "the latent formula exists is the accepted leak (assumptions.md §12); shipping the "
        "latent value itself as a column is that reconstruction already performed, and any "
        "model trained on this matrix would be scoring with the oracle."
    )


def test_no_generator_internal_reaches_a_published_artifact() -> None:
    """Same rule at the boundary a reader opens, not just the one a model trains on."""
    offenders: list[str] = []
    for root_name in PUBLISHED_ROOTS:
        root = REPO_ROOT / root_name
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.csv")):
            header = pd.read_csv(path, nrows=0).columns
            hit = sorted(set(header).intersection(LATENT_COLUMNS))
            if hit:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {hit}")
    assert not offenders, (
        "published artifacts carry generator-internal columns:\n  "
        + "\n  ".join(offenders)
        + "\nThese are VALIDATION ONLY under CLAUDE.md §4 and must not reach a file anyone "
        "opens."
    )
