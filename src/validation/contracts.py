"""Data-contract checks + quarantine for the validated layer.

Contracts (persona spec): required columns, key uniqueness, date ordering
(service <= submission <= adjudication <= payment; at the SOURCE layer this is
CLM_FROM_DT <= CLM_THRU_DT — sim submission/adjudication/payment dates are added
in Phase 2), and non-negative money. Table-level checks (columns present,
uniqueness) gate the table; row-level checks (date order, non-negative) send the
offending rows to a normalized quarantine so a bad row is isolated, never
silently dropped or allowed through.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

# Per-table contract spec.
_REQUIRED: dict[str, list[str]] = {
    "beneficiary_2024": ["BENE_ID"],
    "inpatient": [
        "BENE_ID",
        "CLM_ID",
        "CLM_LINE_NUM",
        "CLM_FROM_DT",
        "CLM_THRU_DT",
        "CLM_PMT_AMT",
        "CLM_TOT_CHRG_AMT",
    ],
}
_UNIQUE_KEYS: dict[str, list[str]] = {
    "beneficiary_2024": ["BENE_ID"],
    "inpatient": ["CLM_ID", "CLM_LINE_NUM"],
}
_NONNEG_MONEY: dict[str, list[str]] = {
    "beneficiary_2024": [],
    "inpatient": [
        "CLM_PMT_AMT",
        "CLM_TOT_CHRG_AMT",
        "NCH_IP_NCVRD_CHRG_AMT",
        "NCH_BENE_IP_DDCTBL_AMT",
    ],
}
# (table) -> (earlier_date_col, later_date_col) that must satisfy earlier <= later.
_DATE_ORDER: dict[str, list[tuple[str, str]]] = {
    "inpatient": [("CLM_FROM_DT", "CLM_THRU_DT")],
}

_QUARANTINE_COLUMNS = ["table_name", "contract", "entity_key", "reason"]


@dataclass
class ContractResult:
    table: str
    rows: int
    table_checks: dict[str, bool]  # required_columns, key_uniqueness -> pass?
    row_violations: dict[str, int]  # contract -> number of quarantined rows
    quarantine: pd.DataFrame = field(
        default_factory=lambda: pd.DataFrame(columns=_QUARANTINE_COLUMNS)
    )

    @property
    def passed(self) -> bool:
        return all(self.table_checks.values())


def _entity_key(df: pd.DataFrame, table: str, mask: pd.Series) -> pd.Series:
    keys = _UNIQUE_KEYS.get(table, [])
    present = [k for k in keys if k in df.columns]
    if not present:
        return mask[mask].index.astype(str)
    joined = df.loc[mask, present].astype(str).agg("|".join, axis=1)
    return joined


def check_contracts(table: str, df: pd.DataFrame) -> ContractResult:
    """Run the contract suite for one validated table, quarantining bad rows."""
    table_checks: dict[str, bool] = {}
    row_violations: dict[str, int] = {}
    quarantine_frames: list[pd.DataFrame] = []

    # Table-level: required columns.
    required = _REQUIRED.get(table, [])
    missing = [c for c in required if c not in df.columns]
    table_checks["required_columns"] = not missing

    # Table-level: key uniqueness.
    keys = [k for k in _UNIQUE_KEYS.get(table, []) if k in df.columns]
    if keys:
        dup_mask = df.duplicated(subset=keys, keep=False)
        table_checks["key_uniqueness"] = not bool(dup_mask.any())
        if dup_mask.any():
            quarantine_frames.append(
                pd.DataFrame(
                    {
                        "table_name": table,
                        "contract": "key_uniqueness",
                        "entity_key": _entity_key(df, table, dup_mask).values,
                        "reason": f"duplicate {'+'.join(keys)}",
                    }
                )
            )
            row_violations["key_uniqueness"] = int(dup_mask.sum())
    else:
        table_checks["key_uniqueness"] = True

    # Row-level: date ordering (earlier <= later, both present).
    for earlier, later in _DATE_ORDER.get(table, []):
        if earlier in df.columns and later in df.columns:
            e = pd.to_datetime(df[earlier], errors="coerce")
            lt = pd.to_datetime(df[later], errors="coerce")
            bad = e.notna() & lt.notna() & (e > lt)
            if bad.any():
                quarantine_frames.append(
                    pd.DataFrame(
                        {
                            "table_name": table,
                            "contract": "date_order",
                            "entity_key": _entity_key(df, table, bad).values,
                            "reason": f"{earlier} > {later}",
                        }
                    )
                )
            row_violations[f"date_order:{earlier}<={later}"] = int(bad.sum())

    # Row-level: non-negative money.
    for col in _NONNEG_MONEY.get(table, []):
        if col in df.columns:
            vals = pd.to_numeric(df[col], errors="coerce")
            bad = vals.notna() & (vals < 0)
            if bad.any():
                quarantine_frames.append(
                    pd.DataFrame(
                        {
                            "table_name": table,
                            "contract": "non_negative_money",
                            "entity_key": _entity_key(df, table, bad).values,
                            "reason": f"{col} < 0",
                        }
                    )
                )
            row_violations[f"non_negative:{col}"] = int(bad.sum())

    quarantine = (
        pd.concat(quarantine_frames, ignore_index=True)
        if quarantine_frames
        else pd.DataFrame(columns=_QUARANTINE_COLUMNS)
    )
    return ContractResult(
        table=table,
        rows=int(len(df)),
        table_checks=table_checks,
        row_violations={k: v for k, v in row_violations.items() if v},
        quarantine=quarantine,
    )
