"""What the demo bundle holds, and what each dataset is made of.

CLAUDE.md §3.1 requires every field in every curated table to carry a provenance
classification, and §3.3 requires the register and the dictionary to be updated in
the same PR that adds a table. `dashboard/demo_data/rcm_demo.duckdb` is a
COMMITTED data file — a reader can open it from a clean clone with no database —
so it is exactly the artifact those rules exist for.

The declaration lives here, in one place, and everything else reads it: the build
step writes it into the bundle's own manifest table, the dashboard's Model & data
quality page renders it, and `tests/app/` asserts the shipped bundle matches it.
A dataset that appears in the file and not in this list fails the build, and so
does the reverse. That is the point — a bundle whose contents nothing classifies
is the §3.3 hole the committed training matrix already had to be rescued from.

`contains_simulated` is declared rather than inferred from column names. A table
can be entirely simulated in substance while carrying no `sim_` column of its own
(`vw_denial_root_cause`'s counts and shares are the case in point: every one of
them is a count of events that never happened), and inferring the flag from
spellings would call that table clean.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

Provenance = str  # one of SOURCE / DERIVED / REFERENCE / SIMULATED / MIXED


@dataclass(frozen=True)
class Dataset:
    """One table in the demo bundle."""

    name: str
    query: str
    provenance: Provenance
    grain: str
    contains_simulated: bool
    note: str
    #: False for datasets built in Python by the build step rather than read from
    #: a warehouse view (model scores, reason codes, metrics).
    from_warehouse: bool = True


def _view(
    name: str,
    provenance: Provenance,
    grain: str,
    note: str,
    contains_simulated: bool = True,
    order_by: str | None = None,
) -> Dataset:
    order = f" order by {order_by}" if order_by else ""
    return Dataset(
        name=name,
        query=f"select * from rcm.{name}{order}",
        provenance=provenance,
        grain=grain,
        contains_simulated=contains_simulated,
        note=note,
    )


# ---------------------------------------------------------------------------
# The curated views. These are analytics-engineer's contract views verbatim —
# the extract copies them, it does not recompute them, so a dashboard figure and
# the SQL control query behind it cannot diverge through a second implementation.
# ---------------------------------------------------------------------------

WAREHOUSE_DATASETS: tuple[Dataset, ...] = (
    _view(
        "vw_claim_enriched",
        provenance="MIXED",
        grain="one row per inpatient claim (claim_sk); 20,867 rows",
        note=(
            "The flattened analytics base view. SOURCE CMS claim facts, DERIVED "
            "warehouse fields, REFERENCE FY2023 code-set display text, and the "
            "SIMULATED adjudication layer, every simulated column prefixed sim_. "
            "sim_facility_ccn/name/state/type are SIMULATED-LINKAGE and "
            "display-only (CLAUDE.md §3.4)."
        ),
        order_by="claim_sk",
    ),
    _view(
        "vw_executive_rcm_summary",
        provenance="MIXED",
        grain="one row per claim-submission month; 109 rows",
        note=(
            "Headline RCM KPIs. billed_charge_amt and medicare_source_paid_amt "
            "are SOURCE (the one real Medicare payer); every other amount and "
            "every rate is SIMULATED or DERIVED from SIMULATED."
        ),
        order_by="submission_year_month",
    ),
    _view(
        "vw_denial_root_cause",
        provenance="SIMULATED",
        grain="one row per (denial category, CARC group, driver mechanism); 34 rows",
        note=(
            "Denial mix. The CMS synthetic claims contain no denials at all, so "
            "every row here counts an event this project generated. CARC codes "
            "are category LABELS only (§3.7); carc_category_label is "
            "project-authored taxonomy text, not licensed X12 wording. ~1,222 of "
            "2,663 denials carry driver 'baseline', i.e. deliberate label noise."
        ),
        order_by="denial_count desc",
    ),
    _view(
        "vw_ar_aging",
        provenance="SIMULATED",
        grain="one row per A/R aging bucket; 5 rows (a spine, so empty buckets stay visible)",
        note=(
            "Ages SIMULATED unpaid claims against a snapshot taken from the "
            "latest simulated activity date. source_billed_at_risk_amt is the "
            "SOURCE billed charge; sim_ar_balance_amt is simulated allowed less "
            "simulated paid."
        ),
        order_by="bucket_sort",
    ),
    _view(
        "vw_payer_performance",
        provenance="SIMULATED",
        grain="one row per simulated payer archetype; 5 rows",
        note=(
            "CLAUDE.md §3.5: the payer dimension is 100% simulated. Medicare FFS "
            "has ONE payer. No archetype is modelled on or named after a real "
            "insurer. Any page or export built on this MUST carry the banner."
        ),
        order_by="claims desc",
    ),
    _view(
        "vw_clean_claim_performance",
        provenance="MIXED",
        grain="one row per SYNTHETIC billing provider (prvdr_num); 4,877 rows",
        note=(
            "Grouped on the synthetic prvdr_num, never on the crosswalked real "
            "CCN — the crosswalk multiplexes 4,876 synthetic providers onto "
            "2,857 real CCNs (worst 8:1), so a CCN grouping would merge up to "
            "eight distinct synthetic hospitals. sim_display_facility_* are "
            "display-only. provider_claims accompanies every rate."
        ),
        order_by="provider_claims desc",
    ),
    _view(
        "vw_work_queue_priority",
        provenance="MIXED",
        grain="one row per actionable claim (denied or open A/R); 2,663 rows",
        note=(
            "HEURISTIC SCAFFOLD, not a model: dollars weighted by age, no learned "
            "weights, every score column named heuristic_* and every row flagged "
            "is_heuristic_placeholder. Model C's Expected Net Recovery queue is a "
            "separate dataset in this bundle."
        ),
        order_by="heuristic_priority_score desc",
    ),
    _view(
        "vw_data_quality_scorecard",
        provenance="DERIVED",
        grain="one row per named data-quality check; 15 rows",
        note=(
            "Measures the warehouse, not the business. subject_provenance names "
            "the class of the data under test. A failing row is a data-quality "
            "review flag."
        ),
        contains_simulated=False,
        order_by="check_id",
    ),
    _view(
        "vw_model_monitoring",
        provenance="SIMULATED",
        grain="one row per (submission month, monitored feature); 981 rows",
        note=(
            "Input-distribution monitoring over submission-month cohorts. Holds "
            "NO model score and NO prediction; every row carries "
            "is_drift_scaffold. Drift here is drift in the simulation."
        ),
        order_by="submission_year_month, feature_name",
    ),
)

# ---------------------------------------------------------------------------
# Datasets the build step computes rather than copies.
# ---------------------------------------------------------------------------

COMPUTED_DATASETS: tuple[Dataset, ...] = (
    Dataset(
        name="model_a_scores",
        query="",
        provenance="DERIVED",
        grain="one row per claim (claim_sk); 20,867 rows",
        contains_simulated=True,
        note=(
            "Model A denial-risk scores, refitted from the COMMITTED training "
            "matrix (artifacts/features/model_a_training_matrix.parquet) so the "
            "bundle can be rebuilt with no warehouse. `fold` states whether a "
            "row is fit / calibrate / test: scores on fit and calibrate rows are "
            "IN-SAMPLE and every page that shows model performance restricts to "
            "the forward test fold and says so. sim_denial_flag is the SIMULATED "
            "label, carried for evaluation only — it is never a feature."
        ),
        from_warehouse=False,
    ),
    Dataset(
        name="model_a_reason_codes",
        query="",
        provenance="DERIVED",
        grain="one row per (claim, contributing feature) for the test fold, top drivers only",
        contains_simulated=True,
        note=(
            "Per-claim SHAP contributions from the tree model, folded back from "
            "encoded columns to declared features and mapped to this project's "
            "analyst reason codes and actions (src/models/explain.py). ~33% of "
            "simulated denials are label noise with no mechanism, so a "
            "decomposition is not a claim-level causal account."
        ),
        from_warehouse=False,
    ),
    Dataset(
        name="model_a_shap_global",
        query="",
        provenance="DERIVED",
        grain="one row per declared feature",
        contains_simulated=True,
        note="Global SHAP importance, summed over the encoded columns of each declared feature.",
        from_warehouse=False,
    ),
    Dataset(
        name="model_c_work_queue",
        query="",
        provenance="DERIVED",
        grain="one row per denial in a queue snapshot; `queue_mode` separates the two",
        contains_simulated=True,
        note=(
            "Model C's Expected Net Recovery worklist as the run wrote it. Two "
            "modes: 'backtest' triages every claim on the day its denial posts, "
            "'live_snapshot' is one instant. Every value column carries the sim_ "
            "marker; tier, tier_rank, queue_position and recommended_action are "
            "statements about OUR process, not measured quantities."
        ),
        from_warehouse=False,
    ),
    Dataset(
        name="model_metrics",
        query="",
        provenance="DERIVED",
        grain="one row per model (A, C), holding that run's metrics.json verbatim",
        contains_simulated=True,
        note=(
            "Copied from models_artifacts/*/metrics.json without alteration, so "
            "the dashboard reports what the run reported. Every metric describes "
            "performance against a SIMULATED label and is not evidence the model "
            "would work on real claims."
        ),
        from_warehouse=False,
    ),
    Dataset(
        name="demo_manifest",
        query="",
        provenance="DERIVED",
        grain="one row per dataset in this bundle",
        contains_simulated=False,
        note=(
            "The bundle's own provenance register: every dataset, its "
            "classification, its grain, its row count and its simulated columns."
        ),
        from_warehouse=False,
    ),
    Dataset(
        name="demo_build_info",
        query="",
        provenance="DERIVED",
        grain="one row — the build stamp of this bundle",
        contains_simulated=False,
        note=(
            "The git commit, branch, dirty-tree state and UTC timestamp the "
            "bundle was built at, plus the source vintages from "
            "config/sources.yaml. A bundle with no commit stamp cannot be pinned "
            "by inspection, which is the [SHA-STAMP] finding applied to this "
            "artifact."
        ),
        from_warehouse=False,
    ),
)

#: Written by the build step itself rather than produced as a dataset, so the
#: declared-but-not-produced check must not demand them from the frame map.
SELF_DESCRIBING_TABLES: frozenset[str] = frozenset({"demo_manifest", "demo_build_info"})

ALL_DATASETS: tuple[Dataset, ...] = WAREHOUSE_DATASETS + COMPUTED_DATASETS

DATASETS_BY_NAME: dict[str, Dataset] = {dataset.name: dataset for dataset in ALL_DATASETS}


def dataset(name: str) -> Dataset:
    """Look up a declared dataset, or say which names exist."""
    try:
        return DATASETS_BY_NAME[name]
    except KeyError:
        raise KeyError(
            f"{name!r} is not a declared demo dataset. Declared: "
            f"{sorted(DATASETS_BY_NAME)}. Add it to src/demo/spec.py with its "
            "provenance classification before the build step will write it."
        ) from None


#: The work-queue value columns that must carry the `sim_` marker, and the
#: pre-rename spellings they used to have. [QUEUE-PREFIX], closed in
#: `src/models/work_queue.py` at commit f0a1e12 under a direct human instruction.
#: Named here, in one place, because two surfaces have to agree about it: the
#: demo build refuses artifacts whose headers still carry the stripped names, and
#: the API refuses to serve a queue built by a pre-rename `build_work_queue`.
QUEUE_SIM_MARKER_RENAMES: dict[str, str] = {
    "p_overturn": "sim_p_overturn",
    "recoverable_amt": "sim_recoverable_amt",
    "expected_recovery_amt": "sim_expected_recovery_amt",
    "expected_net_recovery": "sim_expected_net_recovery",
    "days_to_deadline": "sim_days_to_deadline",
}

QUEUE_RENAME_ADVICE = (
    "These are SIMULATED quantities with the `sim_` provenance marker stripped, which is "
    "the [QUEUE-PREFIX] defect the human directed be fixed (CLAUDE.md §3.2) — and the "
    "column header is all a reader of a worklist sees. The rename landed in "
    "src/models/work_queue.py at commit f0a1e12; this tree predates it. Merge that commit "
    "and regenerate with `make train-appeal`. Nothing here renames the columns for you: "
    "coercing the header at the boundary would leave the source still wrong and hide it."
)


def stripped_marker_columns(columns: Iterable[str]) -> list[str]:
    """Any work-queue column still carrying a pre-rename, unmarked spelling."""
    return sorted(set(columns) & set(QUEUE_SIM_MARKER_RENAMES))


def simulated_columns(columns: list[str]) -> list[str]:
    """The `sim_`-prefixed columns in a frame, in order.

    §3.2's marker is a prefix, so this is a prefix test and not a substring one:
    `medicare_source_paid_amt` contains no simulated value and must not be
    reported as one.
    """
    return [column for column in columns if column.startswith("sim_")]
