"""Column roles, read from what a matrix DECLARES rather than from what it is called.

QA-OWNED (tests/leakage/ is qa's under the 2026-07-27 ownership ruling). This
module pays [SPLIT-DISCOVERY], the debt qa-reviewer-p8 left and team-lead
scheduled into Phase 5.

WHY THE OLD SHAPE WAS WRONG
---------------------------
The training-matrix guard used to find the fold-assignment column by name, out of
a frozenset ``{"is_train", "split", "fold"}``, and the label out of a frozenset of
five plausible spellings. Two failures followed from that, one of which actually
happened:

1. **It made a correct rename look dangerous.** ml-engineer-4 argued that `split`
   could not be prefixed because the guard discovers it by name and prefixing
   would blind the temporal check. Team-lead's QA RULING C rejected the reasoning
   while upholding the outcome, and the reason it gave is the whole point of this
   module: *a guard must never be the reason a correctness-improving rename
   cannot happen.* A safety net that constrains the code it protects has stopped
   being a safety net. Under this module the fold column may be called anything
   at all — `split`, `sim_fold`, `partition_2024`, `q` — and the temporal check
   follows it, because the matrix says which column it is.

2. **It was silently satisfiable.** A matrix whose fold column matched none of
   the three names made the temporal probe `continue`, and a probe that skips
   reads exactly like a probe that passed. Naming is now irrelevant to whether
   the check runs; declaring is what matters, and an undeclared column is treated
   as a FEATURE, which is the strictest possible handling rather than the
   laxest.

THE FAILURE DIRECTION IS THE DESIGN
-----------------------------------
Every column with no declared role is a feature and faces every probe. So the way
to disarm this guard is not to forget to declare something — forgetting makes it
stricter. It is to declare a leaky column into a non-feature role, and
`validate()` below is aimed squarely at that:

* only the two warehouse join keys the firewall document names in its §7 may be
  declared `key`;
* the declared `label` must be the label `src/features/` itself declares, so an
  artifact and its builder cannot disagree about what was being predicted;
* the declared `split` must actually look like a partition — few distinct values,
  not a continuous or dated measurement — so `sim_paid_amount` cannot be
  declared the fold column and thereby excused from the probes;
* the `time` role is declared for the temporal check's use and grants **no**
  exemption at all. A date column is a legitimate feature candidate (the firewall
  document permits several) and must keep facing `unrecognised_date_findings`.

DECLARATION CHANNELS
--------------------
In precedence order, and all of them are things a producer writes down rather
than things this module infers:

1. ``$RCM_MATRIX_ROLES`` — path to a JSON file, for the ``$RCM_FEATURE_MATRIX``
   discovery route, whose matrix has no sidecar of its own;
2. the sidecar manifest ``<stem>.json`` next to a discovered file — this is the
   channel in use today, and `src/features/store.py` already writes `label`,
   `time_column` and `split_column` into it;
3. parquet file-level key-value metadata under ``rcm_column_roles``;
4. for the no-argument-builder route, a ``column_roles`` attribute on the builder
   or a ``COLUMN_ROLES`` mapping on `src.features`, falling back to the canonical
   export's sidecar when the built frame has an identical column list (identical
   columns means the declaration is about exactly these columns).

Two payload spellings are accepted so that a producer can move to explicit roles
without this parser changing again: an explicit ``{"column_roles": {name: role}}``
block, or the manifest's flat `label` / `split_column` / `time_column` keys.
"""

from __future__ import annotations

import json
import os
import pathlib
from dataclasses import dataclass

import pandas as pd

from tests.leakage import firewall_doc

ROLE_NAMES = ("key", "label", "split", "time")

_PARQUET_METADATA_KEY = b"rcm_column_roles"
_MAX_SPLIT_CARDINALITY = 10


class RoleDeclarationError(AssertionError):
    """A declaration exists but does not describe a matrix this guard may check."""


@dataclass(frozen=True)
class ColumnRoles:
    """What a matrix says each of its non-feature columns is for."""

    origin: str
    keys: frozenset[str] = frozenset()
    label: str | None = None
    split: str | None = None
    time: str | None = None

    @property
    def non_features(self) -> frozenset[str]:
        """Columns excused from the feature probes.

        The time column is deliberately NOT here — see the module docstring.
        """
        declared = set(self.keys)
        if self.label:
            declared.add(self.label)
        if self.split:
            declared.add(self.split)
        return frozenset(declared)

    def feature_columns(self, matrix: pd.DataFrame) -> list[str]:
        return [c for c in matrix.columns if c not in self.non_features]


def _from_mapping(payload: dict, origin: str, columns: set[str]) -> ColumnRoles:
    """Read either payload spelling into a `ColumnRoles`."""
    label = payload.get("label")
    split = payload.get("split_column")
    time = payload.get("time_column")
    keys = set(payload.get("key_columns") or payload.get("keys") or ())

    explicit = payload.get("column_roles") or {}
    for column, role in explicit.items():
        if role not in ROLE_NAMES:
            raise RoleDeclarationError(
                f"{origin}: column_roles declares {column!r} as {role!r}; the roles this "
                f"guard understands are {ROLE_NAMES}"
            )
        if role == "key":
            keys.add(column)
        elif role == "label":
            label = column
        elif role == "split":
            split = column
        else:
            time = column

    # The warehouse keys are declared by the firewall document's §7, and the
    # matrix declares which of them it carries by passing them through. Both
    # halves are written down; neither is this module guessing at a name.
    passthrough = set(payload.get("passthrough") or ())
    keys |= (passthrough | columns) & firewall_doc.JOIN_KEYS & columns

    return ColumnRoles(
        origin=origin,
        keys=frozenset(keys),
        label=label,
        split=split,
        time=time,
    )


def _sidecar_payload(path: pathlib.Path) -> dict | None:
    sidecar = path.with_suffix(".json")
    if not sidecar.exists():
        return None
    return json.loads(sidecar.read_text())


def _parquet_payload(path: pathlib.Path) -> dict | None:
    if path.suffix != ".parquet":
        return None
    import pyarrow.parquet as pq

    metadata = pq.read_schema(path).metadata or {}
    raw = metadata.get(_PARQUET_METADATA_KEY)
    return json.loads(raw) if raw else None


def _builder_payload(matrix: pd.DataFrame) -> tuple[dict, str] | None:
    """The declaration for the no-argument-builder discovery route."""
    try:
        import src.features as features_package
    except Exception:  # pragma: no cover - the guard reports an unimportable package
        return None

    builder = getattr(features_package, "build_training_matrix", None)
    declared = getattr(builder, "column_roles", None) or getattr(
        features_package, "COLUMN_ROLES", None
    )
    if declared:
        return dict(declared), "src.features.build_training_matrix().column_roles"

    # Fall back to the canonical export's sidecar, but only when the built frame
    # has exactly the same columns. A declaration is a statement about a set of
    # columns; an identical set is the one case where transferring it asserts
    # nothing new.
    try:
        from src.features import store
    except Exception:  # pragma: no cover
        return None
    payload = _sidecar_payload(store.MATRIX_PATH) if store.MATRIX_PATH.exists() else None
    if payload is None:
        return None
    exported = pd.read_parquet(store.MATRIX_PATH, columns=None).columns
    if list(exported) != list(matrix.columns):
        return None
    return payload, f"{store.MANIFEST_PATH.name} (identical column list)"


def declare(matrix: pd.DataFrame, path: pathlib.Path | None, source: str) -> ColumnRoles | None:
    """The roles `matrix` declares, or None if it declares nothing.

    None is not a pass: the caller treats an undeclared matrix as all-features,
    which is the strictest handling, and `tests/leakage/test_role_declaration.py`
    additionally requires the canonical export to carry a full declaration.
    """
    columns = set(matrix.columns)

    env_path = os.environ.get("RCM_MATRIX_ROLES")
    if env_path and path is not None and os.environ.get("RCM_FEATURE_MATRIX") == str(path):
        payload = json.loads(pathlib.Path(env_path).read_text())
        return _from_mapping(payload, f"$RCM_MATRIX_ROLES -> {env_path}", columns)

    if path is not None:
        payload = _sidecar_payload(path)
        if payload is not None:
            return _from_mapping(payload, f"{path.with_suffix('.json').name}", columns)
        payload = _parquet_payload(path)
        if payload is not None:
            return _from_mapping(payload, f"{path.name}:{_PARQUET_METADATA_KEY.decode()}", columns)
        return None

    built = _builder_payload(matrix)
    if built is not None:
        payload, origin = built
        return _from_mapping(payload, f"{source} via {origin}", columns)
    return None


def validate(roles: ColumnRoles, matrix: pd.DataFrame) -> None:
    """Refuse a declaration that would excuse a column it has no business excusing.

    Raises `RoleDeclarationError`. See the module docstring for why each of these
    is here: declaring is the only way to get out of the probes, so declaring is
    the thing that has to be policed.
    """
    columns = set(matrix.columns)

    for role in ROLE_NAMES:
        declared = getattr(roles, "keys" if role == "key" else role)
        names = declared if role == "key" else ({declared} if declared else set())
        missing = sorted(n for n in names if n not in columns)
        if missing:
            raise RoleDeclarationError(
                f"{roles.origin}: declares {missing} as {role!r} but the matrix has no such "
                "column. A declaration that does not describe the file beside it is stale; "
                "regenerate the artifact and its manifest together."
            )

    stray_keys = sorted(roles.keys - firewall_doc.JOIN_KEYS)
    if stray_keys:
        raise RoleDeclarationError(
            f"{roles.origin}: declares {stray_keys} as key column(s). Only the warehouse join "
            f"keys named in §7 of docs/simulated_forbidden_columns.md ({sorted(firewall_doc.JOIN_KEYS)}) "
            "may be declared keys — otherwise 'key' becomes a way to excuse any column from "
            "the leakage probes by writing one word in a manifest."
        )

    if roles.label is not None:
        try:
            from src.features.build import LABEL
        except Exception:  # pragma: no cover - src/features/ absent in early phases
            LABEL = None
        if LABEL is not None and roles.label != LABEL:
            raise RoleDeclarationError(
                f"{roles.origin}: declares the label as {roles.label!r}, but src/features/ "
                f"declares {LABEL!r}. The artifact and the code that builds it disagree about "
                "what was being predicted, and the declared label is excused from the leakage "
                "probes — so this must never be settled by trusting the manifest."
            )

    if roles.split is not None:
        column = matrix[roles.split]
        if pd.api.types.is_float_dtype(column) or pd.api.types.is_datetime64_any_dtype(column):
            raise RoleDeclarationError(
                f"{roles.origin}: declares {roles.split!r} as the fold assignment, but it is "
                f"{column.dtype} — a continuous or dated measurement, not a partition label. "
                "The split role excuses a column from every probe; it may only be spent on a "
                "column that is actually a fold assignment."
            )
        distinct = int(column.nunique(dropna=False))
        if distinct > _MAX_SPLIT_CARDINALITY:
            raise RoleDeclarationError(
                f"{roles.origin}: declares {roles.split!r} as the fold assignment but it holds "
                f"{distinct} distinct values (limit {_MAX_SPLIT_CARDINALITY}). A per-claim "
                "quantity is not a partition."
            )


def train_mask(matrix: pd.DataFrame, roles: ColumnRoles) -> pd.Series:
    """The declared fold column as a boolean training mask.

    Handles the two shapes a fold column comes in — a boolean/0-1 flag, or a
    string naming the fold — without caring what the column is called.
    """
    column = matrix[roles.split]
    if column.dtype == object or isinstance(column.dtype, pd.CategoricalDtype):
        return column.astype(str).str.lower().isin({"train", "training", "true", "1"})
    return column.astype(bool)
