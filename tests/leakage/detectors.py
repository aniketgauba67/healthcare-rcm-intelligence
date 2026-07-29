"""Detectors that decide whether a training matrix carries leakage.

CLAUDE.md §4.1 requires the build to fail if a forbidden column "or a column derived
from one" enters a training matrix. Name matching alone cannot do that: a forbidden
column that has been renamed, binned, log-scaled, divided by the billed amount, or
aggregated over a claim's history is still the answer key, and none of those show up
in the column name. So the guard is four independent probes, and any one of them
firing is a finding:

1. `name_findings` — the cheap pass. Catches a forbidden column pasted in verbatim or
   with a decorated name. Necessary, never sufficient.
2. `dependency_findings` — the uncertainty coefficient U(x|f) = I(x;f)/H(x), which is
   1.0 exactly when the feature x is a deterministic function of the forbidden column
   f. Renames, monotone transforms, binnings and discretisations all preserve that,
   so they are caught regardless of what the column is called.
3. `label_auc_findings` — the catch-all, and the one that catches ratios and
   aggregates that probe 2 misses. A single feature's AUC against the label is
   bounded above by the oracle: the generator's own latent probability is a
   sufficient statistic for the label, and it reaches only ~0.68. A lone feature
   scoring above that ceiling cannot have obtained the information from
   pre-submission facts. This probe also runs on each feature's *null indicator*,
   because null-ness is itself a leakage channel (`sim_denial_review_date` is
   non-null if and only if the claim was denied).
4. `temporal_findings` — CLAUDE.md §4.3. A split is not temporal if any training row
   postdates a test row.

Thresholds are calibrated against real data rather than guessed; see
`tests/leakage/test_detectors.py`, which measures the permitted-column baseline and
proves each probe fires on a deliberately poisoned matrix.

KNOWN GAP, stated rather than papered over. A copy of `sim_latent_p` with noise added
at roughly the width of a quantile bin (sigma 0.01) escapes all four probes: the noise
scrambles the discretisation enough to drop U to 0.57, and the result scores AUC 0.6756
— fractionally *under* the 0.6778 oracle ceiling, so probe 3 does not fire either.
Closing it by lowering either threshold would put the detector below the measured
permitted baseline and start failing honest work, which is the worse error. The
compensating controls are the name probe, the config-vs-document agreement test (a
deliberately noised latent column cannot be built without reading a column that test
keeps out of the config), and `auc_report`, which the guard prints on every run so a
reviewer sees anything sitting in the suspicious 0.59-0.68 band by hand.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.metrics import mutual_info_score, roc_auc_score
from sklearn.model_selection import StratifiedKFold

DEPENDENCY_THRESHOLD = 0.90
"""U(x|f) at or above this means x is (near-)deterministic in the forbidden column f.

Calibrated on the live layer (20,867 claims), not chosen by eye. Highest U reached by
any genuinely permitted column against any forbidden column is 0.8561
(`sim_submission_date` against `sim_ack_date` — the acknowledgement is the submission
plus a short lag, so the dependency is real and runs in the safe direction). Deliberate
derivations of a forbidden column score 0.90-1.00: renamed 0.965, log1p 0.965,
scaled 0.971, re-binned 0.972, rounded 0.973, null-indicator 1.000, ratio-to-billed
0.902. The threshold sits in that gap.
"""

ORACLE_AUC_FALLBACK = 0.68
"""Used only when the latent probability is unavailable to measure the ceiling live."""

_FEATURE_BINS = 12
_TRUTH_BINS = 64
"""The forbidden side is discretised finely on purpose.

At equal resolution a zero-inflated column defeats the probe: `sim_denied_amount` is
zero for 87% of claims, so quantile binning collapses it to two levels and a
five-level re-binning of it scores only U=0.31. At 64 bins the same poison is caught.
The permitted-column baseline is unchanged by the finer truth side (0.8561 at every
resolution tested), so the sensitivity is bought without spending specificity.
"""


@dataclass(frozen=True)
class Finding:
    """One reason a matrix is rejected."""

    feature: str
    probe: str
    detail: str
    score: float

    def __str__(self) -> str:
        return f"[{self.probe}] {self.feature}: {self.detail} (score={self.score:.4f})"


def _normalise(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def name_findings(features: list[str], forbidden: frozenset[str]) -> list[Finding]:
    """Forbidden columns present verbatim, or wearing a decorated name.

    The decorated-name check strips the `sim_` prefix and looks for the remaining stem
    inside the normalised feature name, so `denied_amount_log` and `payerDenialFlag`
    are caught. It cannot catch a rename that drops the stem entirely — that is what
    the value-based probes are for.
    """
    findings: list[Finding] = []
    stems = {f: _normalise(f.removeprefix("sim_")) for f in forbidden}
    for feature in features:
        if feature in forbidden:
            findings.append(Finding(feature, "name", "forbidden column present verbatim", 1.0))
            continue
        normalised = _normalise(feature)
        for forbidden_column, stem in stems.items():
            if len(stem) >= 8 and stem in normalised:
                findings.append(
                    Finding(
                        feature,
                        "name",
                        f"name contains the stem of forbidden column {forbidden_column}",
                        1.0,
                    )
                )
                break
    return findings


def _codes(series: pd.Series, bins: int = _FEATURE_BINS) -> np.ndarray:
    """Discretise to integer codes, with nulls as their own level.

    Nulls get a level of their own deliberately: a column whose *missingness* mirrors
    a forbidden column is derived from it just as surely as one whose values do.
    """
    values = series
    if not pd.api.types.is_datetime64_any_dtype(values) and _is_datelike(values):
        # A PostgreSQL `date` arrives as dtype `object` holding `datetime.date`, which
        # is neither datetime64 nor numeric — so it fell through to `factorize` and
        # entered at FULL cardinality (~3,000 levels) while the same column from the
        # in-memory generator, as datetime64, was binned to _TRUTH_BINS. The two sides
        # of every live comparison were therefore discretised at different resolutions,
        # and the thresholds calibrated on the generated frame did not mean the same
        # thing live. Normalise first so resolution is a property of the code, not of
        # which pandas reader produced the column.
        values = pd.to_datetime(values, errors="coerce")
    if pd.api.types.is_datetime64_any_dtype(values):
        values = values.astype("int64").where(values.notna())
    if pd.api.types.is_bool_dtype(values):
        values = values.astype("float64")
    if pd.api.types.is_numeric_dtype(values) and values.nunique(dropna=True) > bins:
        binned = pd.qcut(values, bins, labels=False, duplicates="drop")
    else:
        binned = pd.Series(pd.factorize(values, use_na_sentinel=True)[0], index=values.index)
        binned = binned.where(binned >= 0)
    return binned.fillna(-1).astype("int64").to_numpy()


def _entropy(codes: np.ndarray) -> float:
    counts = np.bincount(codes - codes.min())
    probabilities = counts[counts > 0] / counts.sum()
    return float(-(probabilities * np.log(probabilities)).sum())


def uncertainty_coefficient(feature: pd.Series, forbidden: pd.Series) -> float:
    """U(feature|forbidden) — the share of the feature's entropy the forbidden column explains.

    1.0 means the feature is a deterministic function of the forbidden column. Returns
    0.0 for a constant feature, which carries no information to leak.
    """
    x = _codes(feature, _FEATURE_BINS)
    f = _codes(forbidden, _TRUTH_BINS)
    h_x = _entropy(x)
    if h_x <= 1e-12:
        return 0.0
    return float(mutual_info_score(f, x) / h_x)


KEY_CARDINALITY_RATIO = 0.9
"""A truth column with this share of distinct values is a key, not a mechanism."""


def _is_row_key(series: pd.Series) -> bool:
    return len(series) > 0 and series.nunique(dropna=True) / len(series) >= KEY_CARDINALITY_RATIO


def _is_datelike(series: pd.Series) -> bool:
    """True for date columns in any of the shapes this suite sees.

    `datetime.date` matters as much as `pd.Timestamp` here. A PostgreSQL `date`
    column comes back through pandas as dtype `object` holding `datetime.date`, so
    the earlier `pd.Timestamp`-only check answered False for every date in the live
    truth frame — and the date carve-out in `dependency_findings`, which is
    conditioned on this function, silently did not apply where it was designed to.
    It was calibrated against the in-memory generated frame, where the same columns
    arrive as Timestamps and the check works. The gap only became visible once a
    real feature matrix existed to run the probe against.
    """
    if pd.api.types.is_datetime64_any_dtype(series):
        return True
    non_null = series.dropna()
    if non_null.empty:
        return False
    return isinstance(non_null.iloc[0], (pd.Timestamp, dt.date, dt.datetime))


def dependency_findings(
    matrix: pd.DataFrame,
    truth: pd.DataFrame,
    threshold: float = DEPENDENCY_THRESHOLD,
) -> list[Finding]:
    """Features that are (near-)deterministic functions of a forbidden column.

    `truth` holds the forbidden columns for the same rows, in the same order — the
    generator-side values the matrix is not allowed to have seen.

    Row-unique key columns are excluded from the comparison. `clm_id` and `claim_sk`
    identify the row, so *every* column is a deterministic function of them and the
    probe would report U=1.0 for the entire matrix — measured, not assumed: on the
    live layer this fired on all 27 permitted columns. Keys are covered instead by the
    name probe and by `identifier_findings`.

    Date-against-date comparisons are excluded for a similar reason. Every
    post-submission date the generator produces is the submission date plus a lag, so
    a permitted anchor date is legitimately near-deterministic in a forbidden one: the
    `sim_submission_date` / `sim_ack_date` pair measures U=0.856 on the live layer and
    0.963 on the smaller generated frame, straddling any usable threshold. Rather than
    loosen the threshold until that pair passes — which would also let renamed and
    log-scaled forbidden columns (0.965) through — dates are handed to
    `unrecognised_date_findings`, which rejects any date-typed feature the firewall
    document does not name. A renamed forbidden date is caught there instead.
    """
    usable = [c for c in truth.columns if not _is_row_key(truth[c])]
    findings: list[Finding] = []
    for feature in matrix.columns:
        worst: tuple[float, str] | None = None
        feature_is_date = _is_datelike(matrix[feature])
        for forbidden_column in usable:
            if feature_is_date and _is_datelike(truth[forbidden_column]):
                continue
            score = uncertainty_coefficient(matrix[feature], truth[forbidden_column])
            if score >= threshold and (worst is None or score > worst[0]):
                worst = (score, forbidden_column)
        if worst is not None:
            findings.append(
                Finding(
                    feature,
                    "dependency",
                    f"is a deterministic function of forbidden column {worst[1]}",
                    worst[0],
                )
            )
    return findings


def identifier_findings(matrix: pd.DataFrame, claim_sk: pd.Series) -> list[Finding]:
    """Row identifiers smuggled in under another name.

    §7 of the firewall document: `claim_sk` is assigned in source-file order, so it
    correlates with time and acts as a hidden date feature. A near-unique feature that
    is monotone in `claim_sk` is that key wearing a different name — the dependency
    probe cannot see it, because keys are excluded from that comparison for the reason
    given there.
    """
    findings: list[Finding] = []
    for feature in matrix.columns:
        values = matrix[feature]
        if not _is_row_key(values):
            continue
        if pd.api.types.is_datetime64_any_dtype(values):
            values = values.astype("int64").where(values.notna())
        if not pd.api.types.is_numeric_dtype(values):
            continue
        rho = values.corr(claim_sk, method="spearman")
        if pd.notna(rho) and abs(rho) >= 0.99:
            findings.append(
                Finding(
                    feature,
                    "identifier",
                    "is row-unique and monotone in claim_sk — a surrogate key, which "
                    "encodes source-file order and therefore time",
                    float(abs(rho)),
                )
            )
    return findings


def _out_of_fold_target_encoding(codes: np.ndarray, y: np.ndarray, seed: int) -> np.ndarray:
    """Cross-fitted category -> target-rate encoding.

    Cross-fitting matters: an in-fold encoding of a high-cardinality category memorises
    the label and would make every such column look like leakage.
    """
    encoded = np.full(len(y), y.mean(), dtype="float64")
    splitter = StratifiedKFold(n_splits=5, shuffle=True, random_state=seed)
    for train_idx, test_idx in splitter.split(codes.reshape(-1, 1), y):
        rates = pd.Series(y[train_idx]).groupby(codes[train_idx]).mean()
        encoded[test_idx] = pd.Series(codes[test_idx]).map(rates).fillna(y[train_idx].mean())
    return encoded


def single_feature_auc(feature: pd.Series, y: np.ndarray, seed: int = 1337) -> float:
    """The best AUC a single feature reaches on its own, as a one-sided score.

    Numeric features are ranked directly; anything categorical is cross-fit
    target-encoded first so that cardinality alone cannot manufacture a score.
    """
    if len(np.unique(y)) < 2:
        return 0.5
    values = feature
    if pd.api.types.is_datetime64_any_dtype(values):
        values = values.astype("int64").where(values.notna())
    if pd.api.types.is_bool_dtype(values):
        values = values.astype("float64")
    if pd.api.types.is_numeric_dtype(values) and values.nunique(dropna=True) > 2:
        scores = values.fillna(values.median()).to_numpy(dtype="float64")
    else:
        scores = _out_of_fold_target_encoding(_codes(values), y, seed)
    if np.all(scores == scores[0]):
        return 0.5
    auc = roc_auc_score(y, scores)
    return float(max(auc, 1.0 - auc))


def null_indicator_auc(feature: pd.Series, y: np.ndarray) -> float:
    """AUC of the feature's missingness alone.

    `sim_denial_review_date` is non-null exactly when the claim was denied, so a
    feature that merely inherits its null pattern reconstructs the label without ever
    exposing a value.
    """
    indicator = feature.isna().astype("float64").to_numpy()
    if len(np.unique(y)) < 2 or len(np.unique(indicator)) < 2:
        return 0.5
    auc = roc_auc_score(y, indicator)
    return float(max(auc, 1.0 - auc))


def label_auc_findings(
    matrix: pd.DataFrame,
    y: np.ndarray,
    ceiling: float,
    seed: int = 1337,
) -> list[Finding]:
    """Single features that beat the oracle — impossible without leakage.

    `ceiling` is the AUC of the generator's latent probability on these same rows. No
    function of pre-submission facts can exceed it, because the latent probability is
    the sufficient statistic the label was drawn from.
    """
    findings: list[Finding] = []
    for feature in matrix.columns:
        value_auc = single_feature_auc(matrix[feature], y, seed)
        if value_auc >= ceiling:
            findings.append(
                Finding(
                    feature,
                    "label_auc",
                    f"single-feature AUC {value_auc:.4f} at or above the oracle "
                    f"ceiling {ceiling:.4f}",
                    value_auc,
                )
            )
            continue
        missing_auc = null_indicator_auc(matrix[feature], y)
        if missing_auc >= ceiling:
            findings.append(
                Finding(
                    feature,
                    "label_auc",
                    f"null-indicator AUC {missing_auc:.4f} at or above the oracle "
                    f"ceiling {ceiling:.4f}",
                    missing_auc,
                )
            )
    return findings


def unrecognised_date_findings(matrix: pd.DataFrame, permitted: frozenset[str]) -> list[Finding]:
    """Date-typed features the firewall document does not name as permitted.

    This is the counterpart to the date carve-out in `dependency_findings`, and it is
    strict on purpose. A Model A matrix has no legitimate reason to carry a raw date it
    cannot name: the permitted anchors are listed in §3 and §4 of the document, and
    anything else date-typed is either a post-submission date wearing a new name or a
    column nobody has classified. Either way it must not be trained on.

    A feature genuinely derived from a permitted date should be a numeric offset from
    `sim_submission_date` rather than a date, so honest work does not trip this.
    """
    return [
        Finding(
            feature,
            "date",
            "is date-typed but is not a date the firewall document permits — a "
            "renamed post-submission date, or one nobody has classified",
            1.0,
        )
        for feature in matrix.columns
        if _is_datelike(matrix[feature]) and feature not in permitted
    ]


def auc_report(matrix: pd.DataFrame, y: np.ndarray, ceiling: float, top: int = 15) -> str:
    """Single-feature AUCs, strongest first, for a human to read.

    The hard threshold only fires at the oracle ceiling, because that is the only bound
    that carries no false-positive risk. Anything between the permitted baseline
    (measured at 0.586 on the live layer) and that ceiling is not automatically wrong —
    an out-of-fold historical rate is legitimately strong — but it is where a subtle
    leak would sit, so it is printed for review rather than silently accepted.
    """
    scored = sorted(((single_feature_auc(matrix[c], y), c) for c in matrix.columns), reverse=True)
    lines = [f"single-feature AUC (oracle ceiling {ceiling:.4f}), strongest first:"]
    for auc, column in scored[:top]:
        flag = "  <-- REVIEW" if auc >= 0.59 else ""
        lines.append(f"    {auc:.4f}  {column}{flag}")
    return "\n".join(lines)


def temporal_findings(
    dates: pd.Series,
    is_train: pd.Series,
) -> list[Finding]:
    """CLAUDE.md §4.3: the split must be temporal, not random.

    A random split leaves training rows scattered past the earliest test row, which is
    what this measures. A clean quantile split on the submission date leaves none.
    """
    train_dates = dates[is_train.astype(bool)]
    test_dates = dates[~is_train.astype(bool)]
    if train_dates.empty or test_dates.empty:
        return []
    earliest_test = test_dates.min()
    trespassers = int((train_dates > earliest_test).sum())
    if trespassers == 0:
        return []
    share = trespassers / len(train_dates)
    return [
        Finding(
            str(dates.name),
            "temporal",
            f"{trespassers} training rows ({share:.1%}) postdate the earliest test row "
            f"({earliest_test}) — the split is not temporal",
            share,
        )
    ]
