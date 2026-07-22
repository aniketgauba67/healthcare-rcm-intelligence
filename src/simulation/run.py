"""`make simulate` — generate the simulated adjudication layer to Parquet.

Writes one Parquet file per sim_ table into data/simulated/ (gitignored, like
every other data directory) plus a run report containing the realized marginals
and a content hash per table.

Reproducibility contract: the canonical definition of "byte-identical output"
for this project is the SHA-256 of each table's canonical CSV serialization,
recorded in the report as `table_hashes`. CSV rather than the Parquet bytes
because Parquet embeds writer metadata that can differ between library versions
without a single value having changed — hashing it would make the guarantee
depend on pyarrow's build rather than on the generator.
"""

from __future__ import annotations

import argparse
import hashlib
import json

import pandas as pd

from src.ingestion.logging_utils import get_logger, log_event
from src.ingestion.paths import REPO_ROOT

from .config import load_config
from .generator import SimulationResult, generate
from .validate import run_validation

_LOGGER = get_logger("simulation.run")
DATA_SIMULATED = REPO_ROOT / "data" / "simulated"


def table_hash(df: pd.DataFrame) -> str:
    """SHA-256 of a table's canonical CSV form (column order as generated)."""
    return hashlib.sha256(df.to_csv(index=False).encode("utf-8")).hexdigest()


def table_hashes(result: SimulationResult) -> dict[str, str]:
    return {name: table_hash(df) for name, df in sorted(result.tables.items())}


def write_outputs(result: SimulationResult) -> dict[str, str]:
    DATA_SIMULATED.mkdir(parents=True, exist_ok=True)
    hashes = table_hashes(result)
    for name, df in sorted(result.tables.items()):
        df.to_parquet(DATA_SIMULATED / f"{name}.parquet", index=False)
        log_event(_LOGGER, "simulation.written", table=name, rows=int(len(df)))
    report = dict(result.report)
    report["table_hashes"] = hashes
    report["table_rows"] = {name: int(len(df)) for name, df in sorted(result.tables.items())}
    (DATA_SIMULATED / "simulation_report.json").write_text(json.dumps(report, indent=2) + "\n")
    return hashes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the simulated adjudication layer.")
    parser.add_argument(
        "--no-write", action="store_true", help="Generate and validate without writing files."
    )
    args = parser.parse_args(argv)

    cfg = load_config()
    log_event(_LOGGER, "simulation.start", config_version=cfg.version, seed=cfg.seed)
    result = generate(cfg)

    checks = run_validation(cfg, result)
    failed = [c for c in checks if not c.passed]
    for check in checks:
        log_event(
            _LOGGER,
            "simulation.check",
            name=check.name,
            status="PASS" if check.passed else "FAIL",
            detail=check.detail,
        )

    if not args.no_write:
        write_outputs(result)

    log_event(
        _LOGGER,
        "simulation.done",
        checks=len(checks),
        failed=len(failed),
        **{k: v for k, v in result.report.items() if k != "generated_at_utc"},
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
