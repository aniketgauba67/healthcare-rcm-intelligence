"""The CSVs next to the metrics carry no provenance of their own.

QA-AUTHORED REVIEW GATE (tests/leakage/ is qa's under the 2026-07-27 ownership
ruling). Expected RED until each artifact directory carries a provenance note.

`metrics.json` does this properly — both models embed a `provenance` key stating
that the label and every `sim_` feature are SIMULATED. The CSVs beside it do not,
and two of them are the artifacts §3 names explicitly:

* `slice_payer.csv` is a PAYER-LEVEL ANALYSIS. CLAUDE.md §3.5 is unambiguous:
  "Medicare FFS has one payer. Our multi-payer dimension is 100% simulated. Every
  payer-level analysis, view, and dashboard page must carry the simulated-data
  banner." The file lists MCR_FFS, MCR_ADV, COM_LARGE, COM_REGIONAL and MCD_MCO
  with different denial rates and AUCs. Opened on its own it is a five-payer
  comparison, and there is no payer dimension in the real source data at all.

* `work_queue_backtest.csv` reads as an operational worklist: claim ids, dollar
  amounts, `recommended_action` strings like "Human review required by policy
  before any write-off." Judged alone — which is the standard for the Phase 4
  honesty pass — nothing on the row says the denial being worked never happened.

The fix is deliberately cheap, one file per directory rather than a header on
every CSV: a README.md (or PROVENANCE.md) in `models_artifacts/model_a/` and
`models_artifacts/model_c/` saying what the directory holds and that the outcomes
are simulated. A reader who opens a CSV from a file browser sees it in the same
folder; a reader who opens the repo sees it first.
"""

from __future__ import annotations

import pathlib

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_ARTIFACT_DIRS = (
    _REPO_ROOT / "models_artifacts" / "model_a",
    _REPO_ROOT / "models_artifacts" / "model_c",
)

_NOTE_NAMES = ("README.md", "PROVENANCE.md", "README.txt", "PROVENANCE.txt")


@pytest.mark.parametrize("directory", _ARTIFACT_DIRS, ids=lambda path: path.name)
def test_the_artifact_directory_states_its_provenance(directory: pathlib.Path) -> None:
    if not directory.exists():
        pytest.skip(
            f"{directory.name} has not been generated; run `make train` / `make train-appeal`"
        )

    csvs = sorted(path.name for path in directory.glob("*.csv"))
    if not csvs:
        pytest.skip(f"{directory.name} holds no CSV artifacts")

    notes = [directory / name for name in _NOTE_NAMES if (directory / name).exists()]
    assert notes, (
        f"models_artifacts/{directory.name}/ ships {len(csvs)} CSV artifacts with no provenance "
        f"note beside them: {csvs}. metrics.json in this same directory embeds a `provenance` "
        "key and the CSVs do not, so a reader who opens slice_payer.csv gets a five-payer "
        "denial-rate comparison with nothing saying the payer dimension is 100% simulated "
        "(CLAUDE.md §3.5 requires the banner on every payer-level analysis). Add a README.md "
        "here — one file, written by the run — stating what the directory holds, that the "
        "outcomes are SIMULATED, and the `make` target that regenerates it."
    )

    text = "\n".join(note.read_text() for note in notes)
    assert "SIMULATED" in text.upper(), (
        f"models_artifacts/{directory.name}/{notes[0].name} exists but never says the outcomes "
        "are simulated, which is the one thing it is there to say."
    )
