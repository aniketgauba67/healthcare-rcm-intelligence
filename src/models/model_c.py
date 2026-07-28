"""Model C: appeal success, and the work queue it feeds.

`make train-appeal` runs this end to end and writes to
`models_artifacts/model_c/`. The shape mirrors Model A — temporal folds, fit on
the earliest, calibrate on a held-out later slice, measure once on the forward
fold — so the two are readable side by side. Three things are genuinely
different and are the reason this is not a parameter on `train.py`.

**The boundary is the denial.** Remittance facts are legitimately available;
see `src/features/appeal.py`.

**The fold is small.** 967 appealed denials over nine years: 774 train, 193
test, about 86 of them overturned. Every interval here is wide, and a difference
between two models at the second decimal place is not a difference. The code
reports intervals everywhere and the model card leads with the fold size — with
193 rows the honest headline is what the queue is worth, not the third decimal
of an AUC.

**The training population is not the scoring population.** The model learns from
denials somebody chose to appeal and is applied to the whole denial queue,
including the two thirds nobody appealed. That is selection on the outcome's own
decision, and it cannot be fixed from this data. It is measured
(`appeal_selection_summary`) and reported rather than assumed away.

The output of the run is not really the AUC. It is the queue: what a team that
can work 10% of its denials would have recovered by following it, against what
they would have recovered by working the biggest denials instead. That
comparison is the one a denials manager would ask for, and it is the one an
appeal-success model has to win to be worth building.
"""

from __future__ import annotations

import argparse
import json
import pathlib
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

from src.features.appeal import (
    MODEL_C_FEATURES,
    RECOVERABLE,
    TIME_COLUMN,
    appeal_selection_summary,
    build_model_c_frame,
    labels,
)
from src.features.leakage import assert_no_forbidden_columns, load_model_config
from src.features.splits import quantile_temporal_split
from src.models.advanced import gradient_boosted_model
from src.models.baselines import BaseRateBaseline, GroupRateBaseline, logistic_baseline
from src.models.calibrate import calibrate, method_from_config
from src.models.evaluate import (
    calibration_curve_points,
    evaluate_classifier,
    expected_calibration_error,
    paired_bootstrap_difference,
    slice_metrics,
)
from src.models.preprocess import prepare_matrix
from src.models.train import json_safe, write_provenance_readme
from src.models.work_queue import (
    AppealEconomics,
    assert_no_deadline_claim_was_outranked,
    bootstrap_capture_difference,
    build_work_queue,
    expected_net_recovery,
    fit_recovery_ratio,
    priority_score,
    realised_recovery_at_capacity,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ARTIFACT_DIR = REPO_ROOT / "models_artifacts" / "model_c"


def _score(model, matrix: pd.DataFrame) -> np.ndarray:  # noqa: ANN001
    return np.asarray(model.predict_proba(matrix))[:, 1]


def _queue_comparison(
    frame: pd.DataFrame,
    probability: np.ndarray,
    economics: AppealEconomics,
    recovered: pd.Series,
    capacity_share: float,
    n_resamples: int,
    seed: int,
) -> dict[str, Any]:
    """Ranking rules compared as SCORE VECTORS over the same claims.

    Every rule below is a score aligned to `frame`, so the headline share and its
    confidence interval are computed from one object. That is a correction: the
    table used to rank by the TIERED queue while the interval beside it ranked by
    the raw Expected Net Recovery, and the two disagreed by 2.2 points with
    nothing in the output to say why.

    Four rules, and none of them is a strawman:

    * `enr_score` — Expected Net Recovery, ordered purely by money. This is what
      the model is worth with no policy applied.
    * `enr_tiered_queue` — the queue actually shipped, deadline and mandatory-
      review tiers included. Comparing it to the row above prices the policy: the
      overrides are meant to cost some dollars, and this says how many.
    * `largest_denial_first` — sort by denied amount. What most billing offices
      already do, needs no model, and is the rule Expected Net Recovery has to
      beat to justify existing.
    * `random` — the floor.
    """
    realised = np.asarray(recovered, dtype=float)
    claims = frame["claim_sk"]
    tiered = build_work_queue(frame, probability, economics)

    scores: dict[str, np.ndarray] = {
        "enr_score": expected_net_recovery(
            probability, frame["sim_denied_amount"].to_numpy(dtype=float), economics
        ),
        "enr_tiered_queue": priority_score(tiered, claims),
        "largest_denial_first": frame["sim_denied_amount"].to_numpy(dtype=float),
        "random": np.random.default_rng(seed).uniform(size=len(frame)),
    }

    out: dict[str, Any] = {
        name: realised_recovery_at_capacity(score, realised, capacity_share, seed=seed)
        for name, score in scores.items()
    }

    # The comparisons that decide whether the probability and the policy earned
    # their places. Paired: every rule is scored on identical resampled claims.
    out["differences"] = {
        f"{a}_minus_{b}": bootstrap_capture_difference(
            scores[a], scores[b], realised, capacity_share, n_resamples=n_resamples, seed=seed
        ).as_dict()
        for a, b in (
            ("enr_score", "largest_denial_first"),
            ("enr_tiered_queue", "largest_denial_first"),
            ("enr_tiered_queue", "enr_score"),
        )
    }
    out["reading"] = (
        "enr_score minus largest_denial_first is the one that matters: an interval "
        "spanning zero means the probability adds nothing to a queue already sorted by "
        "size, and the honest recommendation is then the simpler rule. "
        "enr_tiered_queue minus enr_score prices the policy overrides — they are "
        "expected to cost dollars, and this is how many."
    )
    return out


def _deadline_pressure(
    frame: pd.DataFrame,
    probability: np.ndarray,
    economics: AppealEconomics,
    time_column: str,
    freq: str = "MS",
) -> dict[str, Any]:
    """Does the deadline override ever actually fire? Rolling month-end queues.

    Neither reported queue exercises it, and that is a property of the two
    conventions rather than of the rule. The backtest triages every claim on the
    day its denial posts, so every claim has the whole filing window ahead of it
    and nothing is ever urgent. The live snapshot is one instant, and on this
    warehouse the 2023-24 tail is thin enough that the instant holds almost
    nothing.

    A deadline rule that never fires in any reported artifact is a rule nobody
    has seen work. So the queue is rebuilt at each month start across the window
    over the claims open on that date, which is how a worklist is really read —
    a claim sits on it, ages, and eventually becomes urgent. This reports how
    often the tier is reached and re-asserts the ordering guarantee on every one
    of those queues, not just on the two headline ones.
    """
    dates = pd.to_datetime(frame[time_column])
    if dates.empty:
        return {"snapshots": 0, "note": "no denials in the window"}

    counts: dict[str, int] = {}
    snapshots = 0
    ever_critical: set[int] = set()
    for as_of in pd.date_range(dates.min(), dates.max(), freq=freq):
        open_now = (dates <= as_of) & (
            dates + pd.Timedelta(days=economics.filing_window_days) >= as_of
        )
        if not open_now.any():
            continue
        queue = build_work_queue(
            frame.loc[open_now].reset_index(drop=True),
            np.asarray(probability)[open_now.to_numpy()],
            economics,
            as_of=as_of,
        )
        # The guarantee, re-checked on every snapshot rather than only on the two
        # queues that reach the report.
        assert_no_deadline_claim_was_outranked(queue)
        snapshots += 1
        for tier, n in queue["tier"].value_counts().items():
            counts[str(tier)] = counts.get(str(tier), 0) + int(n)
        ever_critical.update(
            queue.loc[queue["tier"] == "DEADLINE_CRITICAL", "claim_sk"].astype(int).tolist()
        )

    return {
        "snapshots": snapshots,
        "frequency": freq,
        "claim_appearances_by_tier": dict(sorted(counts.items())),
        "distinct_claims_that_became_deadline_critical": len(ever_critical),
        "note": "Counts are claim-appearances across snapshots, not distinct claims — a "
        "claim open for four months is counted in four queues, which is the point: the "
        "same claim moves from VALUE to DEADLINE_CRITICAL as it ages. The ordering "
        "guarantee was re-asserted on every snapshot.",
    }


def run_model_c(
    engine,  # noqa: ANN001
    config: dict | None = None,
    artifact_dir: pathlib.Path = ARTIFACT_DIR,
    n_resamples: int = 1000,
    write: bool = True,
) -> dict[str, Any]:
    """Train, calibrate, evaluate and rank. Returns the report written as JSON."""
    cfg = config or load_model_config()
    seed = int(cfg["seed"])
    artifact_dir.mkdir(parents=True, exist_ok=True)

    frame = build_model_c_frame(engine, cfg)
    selection = appeal_selection_summary(frame)

    labelled = frame.loc[frame["appealed"]].reset_index(drop=True)
    matrix = prepare_matrix(labelled, MODEL_C_FEATURES)
    assert_no_forbidden_columns(
        matrix.columns, model="C", config=cfg, context="Model C training matrix"
    )
    y = labels(labelled).to_numpy()

    split = quantile_temporal_split(labelled, TIME_COLUMN, float(cfg["split"]["train_quantile"]))
    inner = quantile_temporal_split(
        labelled.loc[split.train], TIME_COLUMN, float(cfg["split"]["calibration_quantile_of_train"])
    )
    train_positions = np.flatnonzero(split.train)
    fit_mask = np.zeros(len(labelled), dtype=bool)
    cal_mask = np.zeros(len(labelled), dtype=bool)
    fit_mask[train_positions[inner.train]] = True
    cal_mask[train_positions[inner.test]] = True

    x_fit, y_fit = matrix.loc[fit_mask], y[fit_mask]
    x_cal, y_cal = matrix.loc[cal_mask], y[cal_mask]
    x_test, y_test = matrix.loc[split.test], y[split.test]

    fitted = {
        "base_rate": BaseRateBaseline().fit(x_fit, y_fit),
        "category_rule": GroupRateBaseline("sim_denial_category").fit(x_fit, y_fit),
        "logistic": logistic_baseline(MODEL_C_FEATURES, cfg).fit(x_fit, y_fit),
        "xgboost": gradient_boosted_model(MODEL_C_FEATURES, cfg).fit(x_fit, y_fit),
    }
    method = method_from_config(cfg)
    calibrated = {
        f"{name}_calibrated": calibrate(model, x_cal, y_cal, method=method)
        for name, model in fitted.items()
        if name in ("logistic", "xgboost")
    }
    platt = {
        f"{name}_platt": calibrate(model, x_cal, y_cal, method="sigmoid")
        for name, model in fitted.items()
        if name in ("logistic", "xgboost")
    }
    all_models = {**fitted, **calibrated, **platt}

    test_scores = {name: _score(model, x_test) for name, model in all_models.items()}
    metrics = {
        name: evaluate_classifier(
            y_test,
            score,
            amounts=labelled.loc[split.test, RECOVERABLE].to_numpy(float),
            n_resamples=n_resamples,
            seed=seed,
        ).as_dict()
        for name, score in test_scores.items()
    }

    champion_name = max(
        ("logistic", "xgboost"),
        key=lambda n: float(average_precision_score(y_cal, _score(fitted[n], x_cal))),
    )
    champion = calibrated[f"{champion_name}_calibrated"]
    champion_test = test_scores[f"{champion_name}_calibrated"]

    comparisons = {
        f"{champion_name}_minus_category_rule": {
            metric_name: paired_bootstrap_difference(
                metric_fn,
                y_test,
                champion_test,
                test_scores["category_rule"],
                n_resamples,
                seed=seed,
            ).as_dict()
            for metric_name, metric_fn in (
                ("roc_auc", roc_auc_score),
                ("pr_auc", average_precision_score),
            )
        },
        "xgboost_minus_logistic": {
            metric_name: paired_bootstrap_difference(
                metric_fn,
                y_test,
                test_scores["xgboost"],
                test_scores["logistic"],
                n_resamples,
                seed=seed,
            ).as_dict()
            for metric_name, metric_fn in (
                ("roc_auc", roc_auc_score),
                ("pr_auc", average_precision_score),
            )
        },
    }

    # --- economics, measured on the fit fold only ----------------------------
    recovery_ratio = fit_recovery_ratio(
        labelled.loc[fit_mask, "sim_appeal_recovered_amount"],
        labelled.loc[fit_mask, RECOVERABLE],
        y_fit,
    )
    economics = AppealEconomics.from_config(cfg, recovery_ratio)

    # --- the queue, in its two honest readings -------------------------------
    # Every denial in the test window, scored — including the two thirds nobody
    # appealed, because ranking those is the job.
    test_window_start = labelled.loc[split.test, TIME_COLUMN].min()
    queue_frame = frame.loc[pd.to_datetime(frame[TIME_COLUMN]) >= test_window_start].reset_index(
        drop=True
    )
    queue_probability = _score(champion, prepare_matrix(queue_frame, MODEL_C_FEATURES))

    # (1) LIVE SNAPSHOT: the worklist as it stood on the last day in the data,
    # over the claims still inside their filing window on that day. Building it
    # over the whole test window instead would report 467 EXPIRED and 1 workable
    # claim — true, and not a work queue.
    as_of = pd.to_datetime(queue_frame[TIME_COLUMN]).max()
    live = (
        pd.to_datetime(queue_frame[TIME_COLUMN]) + pd.Timedelta(days=economics.filing_window_days)
        >= as_of
    ).to_numpy()
    snapshot = build_work_queue(
        queue_frame.loc[live].reset_index(drop=True),
        queue_probability[live],
        economics,
        as_of=as_of,
    )
    assert_no_deadline_claim_was_outranked(snapshot)

    # (2) BACKTEST: every claim triaged at its own arrival, which is how the
    # queue was really worked over the window.
    backtest = build_work_queue(queue_frame, queue_probability, economics, as_of=None)
    assert_no_deadline_claim_was_outranked(backtest)

    capacity = float(cfg["appeal_economics"]["queue_capacity_share"])
    # Realised recovery is only observed where an appeal was actually filed, so
    # the value comparison runs on the appealed test rows: a queue that ranks an
    # un-appealed claim first has no recorded outcome to credit it with. That
    # restriction flatters every ranking equally and is stated in the model card.
    appealed_test = queue_frame.loc[queue_frame["appealed"]].reset_index(drop=True)
    queue_value = _queue_comparison(
        appealed_test,
        _score(champion, prepare_matrix(appealed_test, MODEL_C_FEATURES)),
        economics,
        appealed_test["sim_appeal_recovered_amount"],
        capacity,
        n_resamples=n_resamples,
        seed=seed,
    )

    # (3) DEADLINE PRESSURE: rolling month-start queues, so the urgency override
    # is exercised on real data rather than only asserted in a unit test.
    pressure = _deadline_pressure(queue_frame, queue_probability, economics, TIME_COLUMN)

    calibration_report = {
        "method": method,
        "calibration_fold_rows": int(cal_mask.sum()),
        "ece_uncalibrated": round(
            expected_calibration_error(y_test, test_scores[champion_name], n_bins=5), 5
        ),
        "ece_calibrated": round(expected_calibration_error(y_test, champion_test, n_bins=5), 5),
        "ece_platt": round(
            expected_calibration_error(y_test, test_scores[f"{champion_name}_platt"], n_bins=5), 5
        ),
        "curve": calibration_curve_points(y_test, champion_test, n_bins=5)
        .round(4)
        .to_dict(orient="records"),
        "note": "Five bins, not ten: 193 test rows over ten bins is nineteen claims a "
        "point, and a reliability curve built on nineteen claims a point is a "
        "picture of sampling noise.",
    }

    # Slices carry volumes and an explicit too-thin marker: with 193 test rows
    # every slice here is small, and a rate without its denominator invites a
    # conclusion this fold cannot support.
    slice_frame = labelled.loc[split.test]
    slices = {
        key: slice_metrics(
            slice_frame,
            y_test,
            champion_test,
            by=column,
            min_positives=10,
            n_resamples=n_resamples,
            seed=seed,
        ).to_dict(orient="records")
        for key, column in (
            ("denial_category", "sim_denial_category"),
            ("payer", "sim_payer_id"),
            ("service_line", "sim_service_line_id"),
            ("facility_provider", "prvdr_num"),
        )
    }
    # Provider is the facility slice (CLAUDE.md §3.4: group on the synthetic
    # prvdr_num, never the crosswalked real CCN). At 193 test rows essentially no
    # provider is scorable, which is the finding rather than an omission.
    provider_rows = slices.pop("facility_provider")
    slices["facility_provider_summary"] = [
        {
            "providers_in_test_fold": len(provider_rows),
            "scorable_providers": sum(not r["too_thin_to_score"] for r in provider_rows),
            "note": "Reported as a count only. A 193-row appealed test fold cannot support "
            "per-provider appeal metrics, and a table of nulls would imply it nearly could.",
        }
    ]

    report: dict[str, Any] = {
        "model": "C — appeal success and expected net recovery",
        "seed": seed,
        "provenance": "Targets and every sim_ feature are SIMULATED (CLAUDE.md §3). "
        "No real appeal outcomes exist in this project.",
        "population": {
            "denied_claims": int(len(frame)),
            "appealed_and_labelled": int(len(labelled)),
            "appeal_rate": round(float(frame["appealed"].mean()), 4),
            "note": "The model is fitted on the appealed rows and scored on all denials. "
            "See selection_bias below — this is the limitation that matters most.",
        },
        "selection_bias": {
            "by_category": selection.round(4).to_dict(orient="records"),
            "reading": "Appeal rate runs from 47.6% of MEDICAL_NECESSITY denials to 0 of 12 "
            "TIMELY_FILING denials, and appealed denials are larger on average, so "
            "P(overturn | appealed) is being applied to claims that were passed over for "
            "reasons the model cannot see.",
        },
        "folds": {
            "fit_rows": int(fit_mask.sum()),
            "calibration_rows": int(cal_mask.sum()),
            "test_rows": int(split.test.sum()),
            "cut_date": str(split.cut_date.date()),
            "test_positives": int(y_test.sum()),
        },
        "champion": champion_name,
        "metrics_test_fold": metrics,
        "comparisons": comparisons,
        "calibration": calibration_report,
        "economics": {
            "appeal_processing_cost_usd": economics.processing_cost_usd,
            "recovery_ratio_fit_fold": round(recovery_ratio, 4),
            "recovery_ratio_note": "Mean recovered/denied among overturned appeals on the fit "
            "fold. Flat across category (0.74-0.83) and denial type, and uncorrelated with "
            "claim size (r=0.02), so a richer model of it would be fitting noise.",
            "filing_window_days": economics.filing_window_days,
            "deadline_critical_days": economics.deadline_critical_days,
            "mandatory_review_categories": list(economics.mandatory_review_categories),
            "cost_affects_cutoff_not_order": "Processing cost is a constant subtracted from "
            "every claim, so it decides which claims are worth working, not the order they "
            "are worked in.",
        },
        "queue": {
            "scored_denials_in_test_window": int(len(queue_frame)),
            "capacity_share": capacity,
            "live_snapshot": {
                "as_of": str(as_of.date()),
                "rows": int(len(snapshot)),
                "excluded_as_out_of_window": int(len(queue_frame) - len(snapshot)),
                "tier_counts": snapshot["tier"].value_counts().to_dict(),
                "top_20": snapshot.head(20)
                .drop(columns=["as_of"])
                .round(2)
                .to_dict(orient="records"),
                "caveat": "This snapshot is DEGENERATE on this warehouse and is reported "
                "rather than quietly dropped. The last denial posts in 2024 and the 2023-24 "
                "tail holds ~3% of claims, so almost nothing is still inside its 120-day "
                "filing window on the final day — a true statement about the data and not a "
                "work queue. The backtest and the deadline_pressure block below are the "
                "ones to read; this exists to show the shape a dashboard would render.",
            },
            "backtest_at_arrival": {
                "rows": int(len(backtest)),
                "tier_counts": backtest["tier"].value_counts().to_dict(),
                "note": "No claim is DEADLINE_CRITICAL here by construction: at-arrival "
                "triage gives every claim the full filing window. See deadline_pressure.",
            },
            "deadline_pressure": pressure,
            "value_at_capacity": queue_value,
        },
        "slices": slices,
    }

    if write:
        (artifact_dir / "metrics.json").write_text(
            json.dumps(json_safe(report), indent=2, allow_nan=False)
        )
        write_provenance_readme(
            artifact_dir,
            model="C",
            make_target="make train-appeal",
            description="Appeal success and Expected Net Recovery work queue: the ranked "
            "queues (live snapshot, backtest, rolling monthly), the appeal-selection "
            "table, slice metrics, and the full `metrics.json` report.",
        )
        snapshot.to_csv(artifact_dir / "work_queue_live_snapshot.csv", index=False)
        backtest.to_csv(artifact_dir / "work_queue_backtest.csv", index=False)
        selection.to_csv(artifact_dir / "appeal_selection.csv", index=False)
        for name, rows in slices.items():
            pd.DataFrame(rows).to_csv(artifact_dir / f"slice_{name}.csv", index=False)
    return report


def _headline(report: dict[str, Any]) -> str:
    lines = [
        f"Model C — {report['population']['denied_claims']:,} denials, "
        f"{report['population']['appealed_and_labelled']:,} appealed "
        f"({report['population']['appeal_rate']:.1%})",
        f"  folds: fit {report['folds']['fit_rows']} / cal {report['folds']['calibration_rows']} "
        f"/ test {report['folds']['test_rows']} ({report['folds']['test_positives']} overturned)",
        "",
        f"  {'model':<24}{'ROC-AUC':>9}{'PR-AUC':>9}{'Brier':>9}",
    ]
    for name, m in report["metrics_test_fold"].items():
        lines.append(f"  {name:<24}{m['roc_auc']:>9.4f}{m['pr_auc']:>9.4f}{m['brier']:>9.5f}")
    lines += ["", "  work queue, recovered dollars captured at capacity:"]
    for name, value in report["queue"]["value_at_capacity"].items():
        if not isinstance(value, dict) or "share_of_recoverable_captured" not in value:
            continue
        lines.append(
            f"    {name:<24}{value['share_of_recoverable_captured']:>8.1%} "
            f"(${value['recovered_dollars_captured']:,.0f} of "
            f"${value['recovered_dollars_total']:,.0f})"
        )
    lines.append("  paired differences (same resampled claims):")
    for name, diff in report["queue"]["value_at_capacity"]["differences"].items():
        lines.append(
            f"    {name:<46}{diff['point']:+.1%} [{diff['ci_low']:+.1%}, {diff['ci_high']:+.1%}]"
        )
    pressure = report["queue"]["deadline_pressure"]
    lines += [
        "",
        f"  live snapshot as of {report['queue']['live_snapshot']['as_of']}: "
        f"{report['queue']['live_snapshot']['rows']} open claims, "
        f"tiers {report['queue']['live_snapshot']['tier_counts']}",
        f"  backtest tiers: {report['queue']['backtest_at_arrival']['tier_counts']}",
        f"  deadline pressure over {pressure['snapshots']} monthly queues: "
        f"{pressure['claim_appearances_by_tier']}",
        f"    distinct claims that became deadline-critical: "
        f"{pressure['distinct_claims_that_became_deadline_critical']}",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train and evaluate Model C (appeal success).")
    parser.add_argument("--resamples", type=int, default=1000)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)

    from sqlalchemy import create_engine

    from src.ingestion.load_postgres import database_url

    url = database_url()
    if not url:
        raise SystemExit("no Postgres configured; set POSTGRES_* in .env")

    report = run_model_c(create_engine(url), n_resamples=args.resamples, write=not args.no_write)
    print(_headline(report))
    if not args.no_write:
        print(f"\nartifacts: {ARTIFACT_DIR}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
