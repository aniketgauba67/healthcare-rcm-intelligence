"""Two disclosures the human named, checked where a USER sees the data.

QA-AUTHORED REVIEW GATE (qa owns `tests/`). Do not delete it to go green.

The human's Phase 5 instruction: the honesty pass must EXPLICITLY re-verify the
vintage skew and the crosswalk collision / synthetic-ID keying rule, and they must
be visible where a user looks — README, dashboard, model card — **not only in
internal docs**. That last clause is the whole point of this file. Both facts are
already recorded somewhere; being recorded somewhere is what made them invisible.

1. VINTAGE SKEW. The code sets are correct and unskewed: ICD-10-CM/PCS FY2023,
   HCPCS 2023, MS-DRG v40 FY2023, all matching the 2023-04 claims. The skew is in
   the CROSSWALK reference files — Hospital General Information is vintage 2026-04
   and the Medicare Physician by Provider file is data year 2024, roughly three
   years after the claims. Hospitals open, close and change type over three years;
   providers change specialty and state. `docs/provenance_register.md` records
   both vintages and requires them for reproducibility, and never once says they
   do not match the claims. Recording a number is not disclosing a mismatch.

2. CROSSWALK COLLISION. The facility crosswalk multiplexes 4,876 synthetic
   billing providers onto 2,857 real CCNs, worst case 8:1, so a real CCN is not a
   stable entity key and all provider analysis keys on the synthetic `prvdr_num`.
   `docs/model_card.md` states this with the numbers. `README.md` says
   "display-only enrichment" WITHOUT them, which is the weaker claim: display-only
   tells a reader not to trust the mapping, the ratio tells them a facility row is
   eight different synthetic providers wearing one real name.

HOW THESE CHECKS ARE ANCHORED, AND WHY IT MATTERS
-------------------------------------------------
Three predecessor gates on this project misfired by matching on spellings, and
each misfire pushed a reader towards deleting an honest sentence to go green. So
neither check here matches a phrase the author must copy:

* the vintage check reads the vintages **out of `config/sources.yaml`** and
  requires the surfaces to name those. Re-pull a reference at a new vintage and
  these go red, which is correct — the disclosure is then stale.
* the collision check is pinned to numbers **qa measured from the warehouse**
  (recorded below, re-measured by the `integration` test at the bottom), not to
  the model card's text. Nothing here is satisfied by one document agreeing with
  another.

Wording is free. What is required is that the numbers are present, that the skew
is stated as a mismatch rather than as two recorded facts, and that it happens in
one passage a reader will read as one thought.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCES = REPO_ROOT / "config" / "sources.yaml"
README = REPO_ROOT / "README.md"
MODEL_CARD = REPO_ROOT / "docs" / "model_card.md"
DASHBOARD = REPO_ROOT / "dashboard"

# Measured by qa-reviewer-p14 on 2026-07-28 against the loaded warehouse:
#   select count(*), count(distinct sim_facility_ccn) from rcm.sim_facility_crosswalk
#     -> 4876, 2857
#   max claims per CCN -> 8
# `test_the_documented_collision_still_matches_the_warehouse` re-measures these.
FACILITY_CROSSWALK_SYNTHETIC = 4876
FACILITY_CROSSWALK_REAL_CCNS = 2857
FACILITY_CROSSWALK_WORST_RATIO = 8

# A passage must say the vintages differ, not merely mention two of them.
_SKEW_WORDS = re.compile(
    r"\bskew|\bmismatch|\bnewer\b|\blater\b|\byears after\b|\byears newer\b|"
    r"\bdo(?:es)? not match\b|\bnot contemporaneous\b|\bpostdat|\bthree years\b|\b3 years\b",
    re.IGNORECASE,
)
_DISPLAY_ONLY = re.compile(r"display[-\s]only", re.IGNORECASE)
_FORBIDDEN_AS_FEATURE = re.compile(
    r"forbidden as a feature|never a feature|not a feature|excluded from (?:every )?(?:the )?"
    r"(?:model|feature)",
    re.IGNORECASE,
)


def _vintages() -> dict[str, str]:
    config = yaml.safe_load(SOURCES.read_text())["sources"]
    return {
        "claims": str(config["cms_synthetic_claims"]["vintage"]),
        "facilities": str(config["hospital_general_information"]["vintage"]),
        "providers": str(config["medicare_providers"]["vintage"]),
    }


def _year(vintage: str) -> str:
    match = re.search(r"(19|20)\d{2}", vintage)
    assert match, f"no year in vintage {vintage!r}"
    return match.group(0)


def _paragraphs(text: str) -> list[str]:
    return [block for block in re.split(r"\n\s*\n", text) if block.strip()]


def rendered_strings(source: str) -> list[str]:
    """Every string a module can put on screen, as the VALUE not as the source text.

    MEASURED FALSE RED, qa-reviewer-p18 2026-07-29. This gate read dashboard/*.py
    with `read_text()` and reported that `dashboard/` never says the crosswalk is
    forbidden as a feature. It says exactly that, at
    `dashboard/disclosures.py:119-120`, and a user reads it — but the sentence is
    built by implicit concatenation, so the FILE contains

        ...is also **forbidden as a "\\n    "feature** in every model...

    and `re.search("forbidden as a feature")` cannot cross the quote, the newline
    and the indent. The disclosure was present, correct, and reported missing, which
    on this project is the shape that pushes an author towards rewording an honest
    sentence to satisfy an instrument.

    Parsing fixes it at the root rather than by widening the pattern: CPython merges
    adjacent string literals into ONE `ast.Constant` at parse time, so the value in
    the tree is the value at runtime. This is the same lesson as
    MATCHER-EXPRESSIVENESS and [PASSTHROUGH-BLIND] — the instrument that ran was
    weaker than the claim its result implied.

    Docstrings are deliberately EXCLUDED. A user never reads one, so prose that
    lives only in a docstring is not a disclosure to anybody; counting it would let
    this gate go green on a page whose screen says nothing. Comments were never
    included, and are not now, for the same reason.
    """
    tree = ast.parse(source)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def _surfaces() -> list[tuple[str, str]]:
    """(name, text) for every surface a user reads. The dashboard joins when built."""
    found = [("README.md", README.read_text()), ("docs/model_card.md", MODEL_CARD.read_text())]
    if DASHBOARD.is_dir():
        modules = sorted(p for p in DASHBOARD.rglob("*.py") if p.name != "__init__.py")
        markdown = sorted(DASHBOARD.rglob("*.md"))
        if modules or markdown:
            rendered = [text for path in modules for text in rendered_strings(path.read_text())]
            rendered += [path.read_text() for path in markdown]
            found.append(("dashboard/", "\n\n".join(rendered)))
    return found


def _surface_ids() -> list[str]:
    return [name for name, _ in _surfaces()]


@pytest.fixture(params=_surfaces(), ids=_surface_ids())
def surface(request) -> tuple[str, str]:
    return request.param


# --------------------------------------------------------------------------
# 1. Vintage skew
# --------------------------------------------------------------------------


def test_the_reference_vintages_really_are_skewed() -> None:
    """The premise, measured, so the tests below cannot outlive the condition.

    If a future re-pull ever lands contemporaneous reference files, this fails and
    tells the next reviewer to retire the disclosure rather than leaving a
    permanent warning about a problem that has gone away.
    """
    vintages = _vintages()
    claims = int(_year(vintages["claims"]))
    for key in ("facilities", "providers"):
        assert int(_year(vintages[key])) > claims, (
            f"config/sources.yaml now records {key} at {vintages[key]} against claims at "
            f"{vintages['claims']}, so the vintage skew this file requires disclosed no longer "
            "exists. Re-check and retire the disclosure instead of keeping a stale warning."
        )


def test_the_surface_discloses_the_crosswalk_vintage_skew(surface) -> None:
    name, text = surface
    vintages = _vintages()
    claims_year = _year(vintages["claims"])
    reference_years = {_year(vintages["facilities"]), _year(vintages["providers"])}

    missing = sorted(y for y in reference_years | {claims_year} if y not in text)
    assert not missing, (
        f"{name} never names the vintage(s) {missing}. config/sources.yaml records claims at "
        f"{vintages['claims']}, Hospital General Information at {vintages['facilities']} and "
        f"Medicare Physician by Provider at {vintages['providers']}; a reader of {name} cannot "
        "tell that the facility and provider names attached to a 2023 claim were measured about "
        "three years later."
    )

    stated = [
        block
        for block in _paragraphs(text)
        if claims_year in block
        and any(y in block for y in reference_years)
        and _SKEW_WORDS.search(block)
    ]
    assert stated, (
        f"{name} mentions the vintages but never says they DISAGREE. Two dates recorded in "
        "different places is what docs/provenance_register.md already does, and it is why this "
        "was invisible. One passage must put the claims vintage and the crosswalk reference "
        "vintage together and name the mismatch — e.g. 'the claims are 2023-04, but the "
        f"facility reference file is {vintages['facilities']} and the provider file is data year "
        f"{vintages['providers']}: roughly three years newer. Hospitals open, close and change "
        "type over three years and providers change specialty and state, so a facility's "
        "attributes here are not necessarily what they were when the claim was filed.'"
    )


# --------------------------------------------------------------------------
# 2. Crosswalk collision / synthetic-ID keying
# --------------------------------------------------------------------------


def _states_number(text: str, value: int) -> bool:
    """`4876` or `4,876`, as a whole number."""
    return bool(re.search(rf"\b{value:,}\b|\b{value}\b".replace(",", ",?"), text))


def test_the_surface_discloses_the_crosswalk_collision(surface) -> None:
    name, text = surface

    missing: list[str] = []
    if not _states_number(text, FACILITY_CROSSWALK_SYNTHETIC):
        missing.append(f"{FACILITY_CROSSWALK_SYNTHETIC:,} synthetic providers")
    if not _states_number(text, FACILITY_CROSSWALK_REAL_CCNS):
        missing.append(f"{FACILITY_CROSSWALK_REAL_CCNS:,} real CCNs")
    if not re.search(rf"{FACILITY_CROSSWALK_WORST_RATIO}\s*(?::|\s+to\s+)\s*1", text):
        missing.append(f"the worst-case {FACILITY_CROSSWALK_WORST_RATIO}:1 collision")

    assert not missing, (
        f"{name} does not state {', '.join(missing)}. 'Display-only enrichment' is the weaker "
        "claim: it tells a reader not to trust the mapping, while the ratio tells them a single "
        f"real facility name on this dashboard stands for up to {FACILITY_CROSSWALK_WORST_RATIO} "
        "different synthetic providers. The human asked for this to be visible in README, "
        "dashboard AND model card, not only in the model card."
    )


def test_the_surface_states_the_keying_rule(surface) -> None:
    """Display-only, forbidden as a feature, and the synthetic id is the grain."""
    name, text = surface
    assert _DISPLAY_ONLY.search(text), (
        f"{name} never says the crosswalk is display-only (docs/project_rules.md §3.4)."
    )
    assert _FORBIDDEN_AS_FEATURE.search(text), (
        f"{name} never says the crosswalk is forbidden as a model feature. A reader who is told "
        "only that the names are 'for display' can still reasonably assume the model saw them."
    )
    assert re.search(r"prvdr_num|synthetic (?:provider )?id", text, re.IGNORECASE), (
        f"{name} never says which identifier the analysis actually keys on. The rule is that "
        "every provider analysis keys on the synthetic `prvdr_num`, because a real CCN is not a "
        "stable entity here."
    )


@pytest.mark.integration
def test_the_documented_collision_still_matches_the_warehouse() -> None:
    """Re-measure. A disclosure pinned to a number that has drifted is worse than none.

    The crosswalk is seeded and reproducible, so these are stable — until the seed,
    the stratification or a reference vintage changes, which is exactly when the
    documents need rewriting.
    """
    from sqlalchemy import create_engine, text as sql

    from src.ingestion.load_postgres import database_url

    url = database_url()
    if not url:
        pytest.skip("no Postgres configured (set POSTGRES_* in .env)")

    with create_engine(url).connect() as conn:
        synthetic, real = conn.execute(
            sql("select count(*), count(distinct sim_facility_ccn) from rcm.sim_facility_crosswalk")
        ).one()
        worst = conn.execute(
            sql(
                "select max(n) from (select sim_facility_ccn, count(*) n "
                "from rcm.sim_facility_crosswalk group by 1) t"
            )
        ).scalar_one()

    measured = (int(synthetic), int(real), int(worst))
    documented = (
        FACILITY_CROSSWALK_SYNTHETIC,
        FACILITY_CROSSWALK_REAL_CCNS,
        FACILITY_CROSSWALK_WORST_RATIO,
    )
    assert measured == documented, (
        f"the facility crosswalk now measures {measured} (synthetic providers, distinct real "
        f"CCNs, worst collision) but README, the model card and this file all state {documented}. "
        "Update the constants here and every user-facing surface in the same commit."
    )


# --------------------------------------------------------------------------
# Controls on the extractor. A green disclosure gate is a claim about the
# extractor before it is a claim about the docs, and this one has already been
# wrong once in the permissive direction (it reported a present disclosure
# missing). These pin both directions on text the gate does not otherwise read.
# --------------------------------------------------------------------------

_CONCATENATED = """
NOTE = (
    "The crosswalk is also **forbidden as a "
    "feature** in every model at every boundary."
)
"""

_ONLY_IN_A_COMMENT = """
# The crosswalk is forbidden as a feature in every model.
NOTE = "Facility names are attached by a seeded random assignment."
"""

_ONLY_IN_A_DOCSTRING = '''
"""The crosswalk is forbidden as a feature in every model."""

NOTE = "Facility names are attached by a seeded random assignment."
'''


def test_the_extractor_reads_across_an_implicit_concatenation() -> None:
    """The measured false red. `dashboard/disclosures.py` splits this exact phrase."""
    assert _FORBIDDEN_AS_FEATURE.search("\n".join(rendered_strings(_CONCATENATED)))


def test_a_disclosure_only_in_a_comment_does_not_count() -> None:
    """Nobody reading the screen reads a comment, so it cannot discharge §3/§6."""
    assert not _FORBIDDEN_AS_FEATURE.search("\n".join(rendered_strings(_ONLY_IN_A_COMMENT)))


def test_a_disclosure_only_in_a_docstring_does_not_count() -> None:
    """Same reason, and the likelier mistake: these files carry long docstrings."""
    assert not _FORBIDDEN_AS_FEATURE.search("\n".join(rendered_strings(_ONLY_IN_A_DOCSTRING)))


def test_the_live_dashboard_disclosure_is_what_makes_that_surface_green() -> None:
    """Anchors the fix to the real file, so a future edit that re-splits it stays green
    and a future edit that DELETES it goes red — which grepping the source could not
    distinguish."""
    disclosures = DASHBOARD / "disclosures.py"
    if not disclosures.is_file():
        pytest.skip("dashboard/disclosures.py does not exist")
    rendered = "\n".join(rendered_strings(disclosures.read_text()))
    assert _FORBIDDEN_AS_FEATURE.search(rendered), (
        "dashboard/disclosures.py no longer states that the crosswalk is forbidden as a model "
        "feature. That sentence is what makes the `dashboard/` surface satisfy "
        "test_the_surface_states_the_keying_rule."
    )
