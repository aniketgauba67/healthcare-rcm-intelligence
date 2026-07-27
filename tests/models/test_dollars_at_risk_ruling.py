"""The dollars-at-risk comparison needs a PAIRED instrument, not two intervals.

TEAM-LEAD RULING (tasks.md, Phase 4, 2026-07-27): the champion's top-decile
capture is reported at 38.4% with a bootstrap interval of [16.0%, 59.3%] while a
constant scorer lands at 20.4% — inside that interval. The required fix was to
the INSTRUMENT: a paired bootstrap CI on the DIFFERENCE over the same resamples,
the discipline already applied to `xgboost - logistic`.

As of this file, `train.py` reports three SEPARATE unpaired intervals and a note
inviting the reader to judge them against each other. Comparing two overlapping
unpaired intervals is not the same test as a paired interval on the difference,
and it is wrong in both directions: overlapping intervals do not imply no
difference, and disjoint ones overstate it. Here it is wrong in the conservative
direction — qa-reviewer-p10 measured the paired difference on the current
39-feature store and it does NOT span zero:

    champion - base_rate    +0.1793  95% CI [+0.036, +0.532]  P(diff<=0) ~ 0.01
    champion - payer_rule   +0.2962  95% CI [+0.071, +0.534]

stable across five bootstrap seeds. So the metric DOES support a business claim,
and the currently reported framing understates the model. The interval is very
wide and strongly right-skewed because the ten largest denied claims hold 50.9%
of denied dollars, so the honest statement is "better than arbitrary ranking,
magnitude poorly determined" — not "indistinguishable", and not "38.4% vs 20.4%".

Both halves matter, which is why there are two tests: the static one cannot go
quiet on a clean clone with no artifacts, and the artifact one checks the number
a reader will actually be shown.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_TRAIN = _REPO_ROOT / "src" / "models" / "train.py"
_METRICS = _REPO_ROOT / "models_artifacts" / "model_a" / "metrics.json"

# A paired difference reported under any of these spellings satisfies the ruling.
_PAIRED_KEY = re.compile(r"paired|difference|_minus_|_vs_", re.IGNORECASE)


def _dollars_block(source: str) -> str:
    """The `dollars_report = {...}` literal in train.py, brace-matched."""
    start = source.find("dollars_report")
    if start == -1:
        return ""
    open_brace = source.find("{", start)
    depth = 0
    for index in range(open_brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[open_brace : index + 1]
    return source[open_brace:]


def test_train_applies_a_paired_difference_to_dollar_capture() -> None:
    """Static, so a clean clone with no artifacts still enforces the ruling."""
    source = _TRAIN.read_text()
    block = _dollars_block(source)
    assert block, "could not find the dollars_report literal in src/models/train.py"

    assert "paired_bootstrap_difference" in block or _PAIRED_KEY.search(block), (
        "the dollars-at-risk report contains no paired difference. It currently reports "
        "per-model unpaired intervals only, which is the comparison the team-lead ruling "
        "rejected: a constant scorer's point estimate falling inside the champion's "
        "interval is not evidence that the two are indistinguishable. Report "
        "metric(champion) - metric(reference) over the SAME resampled claims. "
        "`paired_bootstrap_difference` already exists in src/models/evaluate.py; it takes "
        "metric_fn(y, score), so wrap `dollars_at_risk_captured` in a closure that binds "
        "the amounts, resampling amounts with the rows."
    )


def test_metrics_json_reports_the_paired_difference() -> None:
    """The artifact a reader is shown must carry the difference and its interval."""
    if not _METRICS.exists():
        pytest.skip("no models_artifacts/model_a/metrics.json — run `make train` first")

    report = json.loads(_METRICS.read_text())
    dollars = report.get("dollars_at_risk")
    assert dollars, "metrics.json has no dollars_at_risk section"

    paired = {key: value for key, value in dollars.items() if _PAIRED_KEY.search(key)}
    assert paired, (
        "metrics.json reports dollars-at-risk capture without any paired difference. "
        f"present keys: {sorted(dollars)}. Per the ruling, add the champion-minus-reference "
        "difference with its paired bootstrap interval; the separate per-model intervals "
        "may stay alongside it, but they are not the comparison."
    )

    for name, interval in paired.items():
        assert {"point", "ci_low", "ci_high"} <= set(interval), (
            f"{name} is not a full interval: {interval}. A difference reported without "
            "its interval is the failure this ruling exists to prevent."
        )
        assert interval["ci_low"] <= interval["point"] <= interval["ci_high"], (
            f"{name} point estimate lies outside its own interval: {interval}"
        )
