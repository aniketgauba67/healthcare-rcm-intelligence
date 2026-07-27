"""Model A: train, calibrate, evaluate, explain — one reproducible run.

`make train` runs this end to end and writes every number the model card quotes
into `models_artifacts/model_a/`. Nothing in the card is typed by hand.

The order of operations is the substance of the module, so it is stated once
here:

1. Build the point-in-time feature store (`src/features/build.py`).
2. Split forward in time at the configured quantile, then carve a calibration
   fold off the END of the training window (`src/features/splits.py`). Three
   folds result: FIT (earliest), CALIBRATE (late train), TEST (forward).
3. Fit every estimator — baselines included — on FIT only.
4. Fit the calibrator on CALIBRATE, which the estimators never saw.
5. Choose the operating threshold on CALIBRATE, using the cost matrix.
6. Measure everything on TEST, once, with bootstrap intervals.

The threshold is chosen on CALIBRATE rather than on TEST deliberately: picking an
operating point on the fold you then report is choosing the answer with the
answers in hand, and it inflates every downstream dollar figure.

A tripwire runs at the end. `config/model.yaml: sanity_bounds` says an oracle
scoring with the true latent probability tops out near ROC-AUC 0.68, so a model
above 0.75 is a leak to hunt rather than a result to report, and the run fails
rather than writing it out. That is the correct direction for this project: a
suspiciously good number is a bug (CLAUDE.md §1).
"""

from __future__ import annotations

import argparse
import json
import pathlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

from src.features.build import LABEL, MODEL_A_FEATURES, build_model_a_frame, labels
from src.features.extract import fetch_outcome_economics
from src.features.leakage import assert_no_forbidden_columns, load_model_config
from src.features.splits import TemporalSplit, calibration_split, split_from_config
from src.models.advanced import gradient_boosted_model
from src.models.baselines import BaseRateBaseline, PayerRuleBaseline, logistic_baseline
from src.models.calibrate import calibrate, method_from_config
from src.models.evaluate import (
    apply_threshold,
    bootstrap_dollar_capture,
    calibration_curve_points,
    evaluate_classifier,
    expected_calibration_error,
    flagged_share_by_multiplier,
    paired_bootstrap_difference,
    slice_metrics,
    threshold_at_capacity,
    threshold_from_cost_matrix,
)
from src.models.explain import claim_waterfall, explain_tree_model, unmapped_features
from src.models.preprocess import prepare_matrix

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ARTIFACT_DIR = REPO_ROOT / "models_artifacts" / "model_a"

# Providers are the facility slice (CLAUDE.md §3.4: group on the synthetic
# prvdr_num, never the crosswalked real CCN). There are thousands of them and
# almost none carry enough test denials to score, which is itself the finding —
# so the table is capped at the highest-volume ones and the count of unscorable
# providers is reported alongside.
_PROVIDER_SLICE_ROWS = 15


class SuspiciousPerformanceError(AssertionError):
    """Test performance above the noise ceiling. Treat as leakage until disproved."""


@dataclass
class FoldSet:
    """The three folds, as boolean masks over one frame."""

    fit: np.ndarray
    calibrate: np.ndarray
    test: np.ndarray
    split: TemporalSplit
    inner: TemporalSplit


def make_folds(frame: pd.DataFrame, config: dict) -> FoldSet:
    """FIT / CALIBRATE / TEST, all temporal, in that time order."""
    split = split_from_config(frame, config)
    inner = calibration_split(frame, split, config)
    return FoldSet(fit=inner.train, calibrate=inner.test, test=split.test, split=split, inner=inner)


def _amounts_for(frame: pd.DataFrame, engine) -> np.ndarray:  # noqa: ANN001
    """Denied dollars per claim, aligned to `frame`. EVALUATION ONLY.

    Read here and nowhere near the feature builder. `sim_denied_amount` is
    forbidden as a feature and is used only to answer outcome-side questions:
    how much of the money at risk the top decile captures, and what a review
    threshold is worth.
    """
    economics = fetch_outcome_economics(engine).set_index("claim_sk")
    denied = (
        frame["claim_sk"].map(economics["sim_denied_amount"]).astype(float).fillna(0.0).to_numpy()
    )
    return denied


def _score(model, matrix: pd.DataFrame) -> np.ndarray:  # noqa: ANN001
    return np.asarray(model.predict_proba(matrix))[:, 1]


def _value_bands(frame: pd.DataFrame, fit_mask: np.ndarray) -> pd.Series:
    """Claim-value bands, with the cut points learned on the FIT fold only.

    Quantiles computed over the whole frame would be a (small, real) leak of the
    test period's charge distribution into a slice definition.
    """
    charges = frame["billed_charge_amt"].astype(float)
    edges = np.unique(charges[fit_mask].quantile([0.0, 0.25, 0.5, 0.75, 0.9, 1.0]).to_numpy())
    edges[0], edges[-1] = -np.inf, np.inf
    labels_ = ["Q1 lowest", "Q2", "Q3", "Q4", "top 10% by charge"][: len(edges) - 1]
    return pd.cut(charges, bins=edges, labels=labels_, include_lowest=True).astype("string")


def _calibration_plot(
    curves: dict[str, pd.DataFrame], path: pathlib.Path, title: str
) -> pathlib.Path:
    """Reliability diagram. Written to disk because §7 asks for the plot itself."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(6.0, 5.5))
    ax.plot([0, 1], [0, 1], linestyle="--", linewidth=1, color="0.6", label="perfect")
    for name, curve in curves.items():
        ax.plot(
            curve["mean_predicted"], curve["observed_rate"], marker="o", markersize=4, label=name
        )
    upper = max(
        float(c[["mean_predicted", "observed_rate"]].to_numpy().max()) for c in curves.values()
    )
    ax.set_xlim(0, upper * 1.1)
    ax.set_ylim(0, upper * 1.1)
    ax.set_xlabel("mean predicted denial probability (decile of score)")
    ax.set_ylabel("observed denial rate")
    ax.set_title(title, fontsize=10)
    ax.legend(fontsize=8)
    fig.text(
        0.5,
        0.005,
        "SIMULATED adjudication layer on CMS synthetic claims — not real denials.",
        ha="center",
        fontsize=7,
        color="0.35",
    )
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def _shap_plot(values: np.ndarray, names: list[str], path: pathlib.Path) -> pathlib.Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import shap

    shap.summary_plot(values, feature_names=names, show=False, max_display=20, plot_type="bar")
    fig = plt.gcf()
    fig.set_size_inches(7.5, 6.0)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def json_safe(value: Any) -> Any:
    """Recursively replace NaN/Infinity with null so the report is real JSON.

    `json.dumps` emits bare `NaN` for a float nan. Python reads it back happily
    and every other parser — the FastAPI client in Phase 5, a browser, `jq` —
    rejects the file. NaN is a legitimate value here (a slice too thin to score,
    a fold with no denied dollars), so it is written as `null` rather than
    suppressed: "not computed" is information the reader needs.
    """
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, (np.floating, float)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def sanity_verdict(
    headline: dict[str, Any], bounds: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """Compare the headline metrics against the documented noise ceiling.

    Separated from the run so it can be tested without a database, and so the
    thing that decides "this is too good to be true" is one readable function
    rather than a condition buried in a 200-line pipeline.
    """
    checks = (
        ("ROC-AUC", "roc_auc", "suspicious_roc_auc"),
        ("PR-AUC", "pr_auc", "suspicious_pr_auc"),
    )
    breaches = [
        f"{label} {headline[metric]} > {bounds[bound]}"
        for label, metric, bound in checks
        if float(headline[metric]) > float(bounds[bound])
    ]
    verdict = {
        "oracle_roc_auc": bounds["oracle_roc_auc"],
        "suspicious_roc_auc": bounds["suspicious_roc_auc"],
        "suspicious_pr_auc": bounds["suspicious_pr_auc"],
        "observed_roc_auc": headline["roc_auc"],
        "observed_pr_auc": headline["pr_auc"],
        "verdict": (
            "SUSPICIOUS — probable leakage: " + "; ".join(breaches)
            if breaches
            else "within the documented noise ceiling"
        ),
    }
    return verdict, breaches


def run_model_a(
    engine,  # noqa: ANN001
    config: dict | None = None,
    artifact_dir: pathlib.Path = ARTIFACT_DIR,
    n_resamples: int = 1000,
    write: bool = True,
) -> dict[str, Any]:
    """The whole Model A run. Returns the report that is also written as JSON."""
    cfg = config or load_model_config()
    seed = int(cfg["seed"])
    artifact_dir.mkdir(parents=True, exist_ok=True)

    frame = build_model_a_frame(engine, cfg)
    # `prepare_matrix` selects the declared features and nothing else, and coerces
    # them to modelling dtypes. Passthrough columns — claim_sk, the date, the
    # provider key, the label — stay on `frame` and never reach an estimator.
    matrix = prepare_matrix(frame, MODEL_A_FEATURES)
    y = labels(frame).to_numpy()

    # The guard, on the exact object handed to the estimators.
    assert_no_forbidden_columns(
        matrix.columns, model="A", config=cfg, context="Model A training matrix"
    )

    folds = make_folds(frame, cfg)
    amounts = _amounts_for(frame, engine)

    x_fit, y_fit = matrix.loc[folds.fit], y[folds.fit]
    x_cal, y_cal = matrix.loc[folds.calibrate], y[folds.calibrate]
    x_test, y_test = matrix.loc[folds.test], y[folds.test]

    fitted: dict[str, Any] = {
        "base_rate": BaseRateBaseline().fit(x_fit, y_fit),
        "payer_rule": PayerRuleBaseline().fit(x_fit, y_fit),
        "logistic": logistic_baseline(MODEL_A_FEATURES, cfg).fit(x_fit, y_fit),
        "xgboost": gradient_boosted_model(MODEL_A_FEATURES, cfg).fit(x_fit, y_fit),
    }

    method = method_from_config(cfg)
    calibrated = {
        f"{name}_calibrated": calibrate(model, x_cal, y_cal, method=method)
        for name, model in fitted.items()
        if name in ("logistic", "xgboost")
    }
    # Platt on the same fold, for the comparison the model card reports.
    platt = {
        f"{name}_platt": calibrate(model, x_cal, y_cal, method="sigmoid")
        for name, model in fitted.items()
        if name in ("logistic", "xgboost")
    }
    all_models = {**fitted, **calibrated, **platt}

    test_scores = {name: _score(model, x_test) for name, model in all_models.items()}
    cal_scores = {name: _score(model, x_cal) for name, model in fitted.items()}

    metrics = {
        name: evaluate_classifier(
            y_test, score, amounts=amounts[folds.test], n_resamples=n_resamples, seed=seed
        ).as_dict()
        for name, score in test_scores.items()
    }

    # --- the comparison CLAUDE.md §7 actually asks for -----------------------
    comparisons = {
        "xgboost_minus_logistic": {
            "roc_auc": paired_bootstrap_difference(
                roc_auc_score,
                y_test,
                test_scores["xgboost"],
                test_scores["logistic"],
                n_resamples,
                seed=seed,
            ).as_dict(),
            "pr_auc": paired_bootstrap_difference(
                average_precision_score,
                y_test,
                test_scores["xgboost"],
                test_scores["logistic"],
                n_resamples,
                seed=seed,
            ).as_dict(),
        },
        "logistic_minus_payer_rule": {
            "roc_auc": paired_bootstrap_difference(
                roc_auc_score,
                y_test,
                test_scores["logistic"],
                test_scores["payer_rule"],
                n_resamples,
                seed=seed,
            ).as_dict(),
            "pr_auc": paired_bootstrap_difference(
                average_precision_score,
                y_test,
                test_scores["logistic"],
                test_scores["payer_rule"],
                n_resamples,
                seed=seed,
            ).as_dict(),
        },
    }

    # --- champion, chosen on CALIBRATE, never on TEST ------------------------
    champion_name = max(
        ("logistic", "xgboost"),
        key=lambda n: float(average_precision_score(y_cal, cal_scores[n])),
    )
    champion_selection = {
        "chosen_on": "calibration fold (held out, strictly earlier than test)",
        "criterion": "PR-AUC",
        "candidates": {
            n: round(float(average_precision_score(y_cal, cal_scores[n])), 4)
            for n in ("logistic", "xgboost")
        },
        "champion": champion_name,
    }
    champion = calibrated[f"{champion_name}_calibrated"]
    champion_test_score = test_scores[f"{champion_name}_calibrated"]

    # --- calibration ---------------------------------------------------------
    curves = {
        "uncalibrated": calibration_curve_points(y_test, test_scores[champion_name]),
        f"{method} (chosen)": calibration_curve_points(y_test, champion_test_score),
        "platt": calibration_curve_points(y_test, test_scores[f"{champion_name}_platt"]),
    }
    calibration_report = {
        "method": method,
        "calibration_fold_rows": int(folds.calibrate.sum()),
        "ece_uncalibrated": round(
            expected_calibration_error(y_test, test_scores[champion_name]), 5
        ),
        "ece_calibrated": round(expected_calibration_error(y_test, champion_test_score), 5),
        "ece_platt": round(
            expected_calibration_error(y_test, test_scores[f"{champion_name}_platt"]), 5
        ),
        "curve": curves[f"{method} (chosen)"].round(4).to_dict(orient="records"),
    }

    # --- operating threshold -------------------------------------------------
    economics = dict(cfg["cost_matrix"])
    chosen = threshold_from_cost_matrix(
        y_cal,
        _score(champion, x_cal),
        amounts[folds.calibrate],
        review_cost_usd=float(economics["review_cost_usd"]),
        prevented_value_multiplier=float(economics["prevented_denial_value_multiplier"]),
    )
    applied = apply_threshold(
        y_test,
        champion_test_score,
        amounts[folds.test],
        chosen,
        prevented_value_multiplier=float(economics["prevented_denial_value_multiplier"]),
    )
    # The cost-optimal threshold is degenerate on this data — see
    # config/model.yaml: operating_point — so a capacity-constrained point is
    # reported next to it. Its cut is also taken on the calibration fold.
    capacity = float(cfg["operating_point"]["review_capacity_share"])
    capacity_choice = threshold_at_capacity(
        y_cal,
        _score(champion, x_cal),
        amounts[folds.calibrate],
        capacity_share=capacity,
        review_cost_usd=float(economics["review_cost_usd"]),
        prevented_value_multiplier=float(economics["prevented_denial_value_multiplier"]),
    )
    capacity_applied = apply_threshold(
        y_test,
        champion_test_score,
        amounts[folds.test],
        capacity_choice,
        prevented_value_multiplier=float(economics["prevented_denial_value_multiplier"]),
    )
    sensitivity = flagged_share_by_multiplier(
        y_cal,
        _score(champion, x_cal),
        amounts[folds.calibrate],
        review_cost_usd=float(economics["review_cost_usd"]),
        multipliers=list(cfg["operating_point"]["multiplier_sensitivity"]),
    )
    threshold_report = {
        "cost_optimal": {
            "chosen_on_calibration_fold": vars(chosen),
            "applied_to_test_fold": vars(applied),
            "reading": "Degenerate by construction, not by defect: at these costs "
            "reviewing every claim pays, so the threshold carries no information. "
            "Reported because hiding it would be the dishonest option.",
        },
        "capacity_constrained": {
            "review_capacity_share": capacity,
            "chosen_on_calibration_fold": vars(capacity_choice),
            "applied_to_test_fold": vars(capacity_applied),
            "reading": "The operating point a team can actually run: work the top "
            f"{capacity:.0%} of the queue by score.",
        },
        "multiplier_sensitivity": sensitivity.to_dict(orient="records"),
        "cost_matrix": economics,
    }

    # --- dollars at risk, with the interval it needs -------------------------
    test_amounts = amounts[folds.test]
    denied_dollars = np.sort(test_amounts[y_test == 1])[::-1]
    dollars_report = {
        "total_denied_dollars_test_fold": round(float(denied_dollars.sum()), 2),
        "denied_claims_test_fold": int(len(denied_dollars)),
        "share_of_denied_dollars_in_largest_10_claims": round(
            float(denied_dollars[:10].sum() / denied_dollars.sum()), 4
        ),
        "top_decile_capture": {
            name: bootstrap_dollar_capture(
                y_test, test_scores[name], test_amounts, n_resamples=n_resamples, seed=seed
            ).as_dict()
            for name in (f"{champion_name}_calibrated", "payer_rule", "base_rate")
        },
        "note": "The base-rate row is the floor: a constant score ranks nothing, so its "
        "capture is whatever a random tenth of the queue happens to hold. Its interval is "
        "the width to judge every other row against.",
    }

    # --- slices --------------------------------------------------------------
    test_frame = frame.loc[folds.test].copy()
    test_frame["value_band"] = _value_bands(frame, folds.fit).loc[folds.test]
    slices: dict[str, list[dict[str, Any]]] = {}
    for key, column in (
        ("payer", "sim_payer_id"),
        ("service_line", "sim_service_line_id"),
        ("value_band", "value_band"),
    ):
        table = slice_metrics(test_frame, y_test, champion_test_score, by=column)
        slices[key] = table.to_dict(orient="records")

    provider_table = slice_metrics(test_frame, y_test, champion_test_score, by="prvdr_num")
    slices["facility_provider_top_volume"] = provider_table.head(_PROVIDER_SLICE_ROWS).to_dict(
        orient="records"
    )
    slices["facility_provider_summary"] = [
        {
            "providers_in_test_fold": int(len(provider_table)),
            "scorable_providers": int((~provider_table["too_thin_to_score"]).sum()),
            "note": "Provider-level AUC is unscorable at this volume: the median provider "
            "contributes a handful of test claims. Reported per provider only where the "
            "denominator supports it; the payer slice is the stable one.",
        }
    ]

    # --- explanations --------------------------------------------------------
    tree_pipeline = fitted["xgboost"]
    shap_result = explain_tree_model(
        tree_pipeline.named_steps["preprocess"],
        tree_pipeline.named_steps["estimator"],
        test_frame,
        MODEL_A_FEATURES,
        seed=seed,
    )
    waterfall_positions = list(np.argsort(-shap_result.values.sum(axis=1))[:3])
    waterfalls = {
        str(test_frame.loc[shap_result.row_index[int(pos)], "claim_sk"]): claim_waterfall(
            shap_result, int(pos), MODEL_A_FEATURES
        ).to_dict(orient="records")
        for pos in waterfall_positions
    }
    shap_report = {
        "rows_explained": int(len(shap_result.row_index)),
        "global_importance": shap_result.global_importance.round(5).to_dict(orient="records"),
        "unmapped_features": unmapped_features(MODEL_A_FEATURES),
        "example_waterfalls": waterfalls,
    }

    # --- the tripwire --------------------------------------------------------
    sanity, breaches = sanity_verdict(metrics[f"{champion_name}_calibrated"], cfg["sanity_bounds"])

    report: dict[str, Any] = {
        "model": "A — pre-submission denial risk",
        "generated_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "seed": seed,
        "provenance": "Label and all sim_ features are SIMULATED (CLAUDE.md §3). "
        "Claim facts are CMS synthetic SOURCE data. No real patient or payer data.",
        "claims": int(len(frame)),
        "features": len(MODEL_A_FEATURES.names),
        "split": folds.split.describe(frame, label=LABEL),
        "folds": {
            "fit_rows": int(folds.fit.sum()),
            "calibration_rows": int(folds.calibrate.sum()),
            "test_rows": int(folds.test.sum()),
            "calibration_cut_date": str(folds.inner.cut_date.date()),
        },
        "champion_selection": champion_selection,
        "metrics_test_fold": metrics,
        "comparisons": comparisons,
        "calibration": calibration_report,
        "threshold": threshold_report,
        "dollars_at_risk": dollars_report,
        "slices": slices,
        "shap": shap_report,
        "sanity": sanity,
    }

    if breaches:
        if write:
            (artifact_dir / "metrics_SUSPICIOUS.json").write_text(
                json.dumps(json_safe(report), indent=2, allow_nan=False)
            )
        raise SuspiciousPerformanceError(
            "Model A scored above the documented noise ceiling ("
            + "; ".join(breaches)
            + "). docs/simulated_forbidden_columns.md §8 puts the oracle at ROC-AUC "
            f"{cfg['sanity_bounds']['oracle_roc_auc']}, so this is a leak to hunt, not a result "
            "to report. Run stopped; report written to metrics_SUSPICIOUS.json for diagnosis."
        )

    if write:
        _write_artifacts(report, curves, shap_result, artifact_dir, champion_name)
    return report


def _write_artifacts(
    report: dict[str, Any],
    curves: dict[str, pd.DataFrame],
    shap_result,  # noqa: ANN001
    artifact_dir: pathlib.Path,
    champion_name: str,
) -> None:
    (artifact_dir / "metrics.json").write_text(
        json.dumps(json_safe(report), indent=2, allow_nan=False)
    )
    pd.concat([c.assign(series=name) for name, c in curves.items()]).to_csv(
        artifact_dir / "calibration_curve.csv", index=False
    )
    shap_result.global_importance.to_csv(artifact_dir / "shap_global_importance.csv", index=False)
    shap_result.encoded_importance.to_csv(artifact_dir / "shap_encoded_importance.csv", index=False)
    for key, rows in report["slices"].items():
        pd.DataFrame(rows).to_csv(artifact_dir / f"slice_{key}.csv", index=False)
    _calibration_plot(
        curves,
        artifact_dir / "calibration_curve.png",
        f"Model A reliability — {champion_name}, forward test fold",
    )
    _shap_plot(
        shap_result.values,
        shap_result.encoded_names,
        artifact_dir / "shap_global_importance.png",
    )


def _headline(report: dict[str, Any]) -> str:
    lines = [
        f"Model A — {report['claims']:,} claims, {report['features']} features",
        f"  split {report['split']['cut_date']}: "
        f"fit {report['folds']['fit_rows']:,} / cal {report['folds']['calibration_rows']:,} "
        f"/ test {report['folds']['test_rows']:,}",
        f"  test base rate {report['split']['test_base_rate']}",
        "",
        f"  {'model':<24}{'ROC-AUC':>9}{'PR-AUC':>9}{'Brier':>9}{'$ top decile':>14}",
    ]
    for name, m in report["metrics_test_fold"].items():
        dollars = m.get("dollars_at_risk_captured_top_decile")
        lines.append(
            f"  {name:<24}{m['roc_auc']:>9.4f}{m['pr_auc']:>9.4f}{m['brier']:>9.5f}"
            f"{(f'{dollars:.1%}' if dollars is not None else '-'):>14}"
        )
    diff = report["comparisons"]["xgboost_minus_logistic"]["roc_auc"]
    lines += [
        "",
        f"  xgboost - logistic ROC-AUC: {diff['point']:+.4f} "
        f"[{diff['ci_low']:+.4f}, {diff['ci_high']:+.4f}]",
        f"  champion: {report['champion_selection']['champion']} (chosen on the calibration fold)",
        f"  ECE {report['calibration']['ece_uncalibrated']:.5f} -> "
        f"{report['calibration']['ece_calibrated']:.5f} after {report['calibration']['method']}",
        f"  sanity: {report['sanity']['verdict']}",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train and evaluate Model A (denial risk).")
    parser.add_argument(
        "--resamples", type=int, default=1000, help="bootstrap resamples for the intervals"
    )
    parser.add_argument("--no-write", action="store_true", help="compute without writing artifacts")
    args = parser.parse_args(argv)

    from sqlalchemy import create_engine

    from src.ingestion.load_postgres import database_url

    url = database_url()
    if not url:
        raise SystemExit("no Postgres configured; set POSTGRES_* in .env")
    engine = create_engine(url)

    report = run_model_a(engine, n_resamples=args.resamples, write=not args.no_write)
    print(_headline(report))
    if not args.no_write:
        print(f"\nartifacts: {ARTIFACT_DIR}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
