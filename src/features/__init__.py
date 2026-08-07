"""Point-in-time feature stores for Models A and C.

`build_training_matrix` is re-exported at package level on purpose: it is one of
the three routes `tests/leakage/test_training_matrix_guard.py` uses to find a
training matrix, and the only one that needs no path and no environment
variable. Removing it, or giving it a required argument, silently disarms the
docs/project_rules.md §4.1 value probes — they skip, and a skip reads like a pass. See
`src/features/store.py` for why that matters and what it cost last time.
"""

from src.features.store import (
    MANIFEST_PATH,
    MATRIX_PATH,
    build_training_matrix,
    persist_training_matrix,
    read_manifest,
    read_persisted_matrix,
)

__all__ = [
    "MANIFEST_PATH",
    "MATRIX_PATH",
    "build_training_matrix",
    "persist_training_matrix",
    "read_manifest",
    "read_persisted_matrix",
]
