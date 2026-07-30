"""Declared provenance for dashboard surfaces that put data in front of a reader.

The dashboard's tables, charts, and KPI rows are page-level outputs. They are
registered here rather than inferred from labels so a new page cannot quietly
bypass the simulated-data disclosure or the shared table boundary.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DashboardEmitter:
    """One page-level dashboard surface and the provenance contract it must keep."""

    module: str
    surface: str
    provenance: str
    contains_simulated: bool
    disclosure: str
    outputs: tuple[str, ...]


_BANNER_DISCLOSURE = (
    "Renders render_synthetic_data_banner before user-facing output and preserves sim_ "
    "column markers in the shared dataframe renderer."
)


DASHBOARD_EMITTERS: dict[str, DashboardEmitter] = {
    "dashboard/components.py": DashboardEmitter(
        module="dashboard/components.py",
        surface="Shared dataframe renderer",
        provenance="MIXED",
        contains_simulated=True,
        disclosure=(
            "Renders a simulated-column caption from the actual headers; every page supplies "
            "the required synthetic-data banner."
        ),
        outputs=("dataframe", "table caption"),
    ),
    "dashboard/pages/ar_recovery.py": DashboardEmitter(
        module="dashboard/pages/ar_recovery.py",
        surface="A/R aging and payer recovery analysis",
        provenance="MIXED",
        contains_simulated=True,
        disclosure=_BANNER_DISCLOSURE,
        outputs=("KPI", "chart", "dataframe"),
    ),
    "dashboard/pages/denial_prevention.py": DashboardEmitter(
        module="dashboard/pages/denial_prevention.py",
        surface="Denial root-cause and prevention analysis",
        provenance="MIXED",
        contains_simulated=True,
        disclosure=_BANNER_DISCLOSURE,
        outputs=("KPI", "chart", "dataframe"),
    ),
    "dashboard/pages/executive_overview.py": DashboardEmitter(
        module="dashboard/pages/executive_overview.py",
        surface="Executive RCM summary",
        provenance="MIXED",
        contains_simulated=True,
        disclosure=_BANNER_DISCLOSURE,
        outputs=("KPI", "chart", "dataframe"),
    ),
    "dashboard/pages/model_data_quality.py": DashboardEmitter(
        module="dashboard/pages/model_data_quality.py",
        surface="Model, reconciliation, and bundle-quality analysis",
        provenance="MIXED",
        contains_simulated=True,
        disclosure=_BANNER_DISCLOSURE,
        outputs=("KPI", "chart", "dataframe", "provenance register"),
    ),
    "dashboard/pages/work_queue.py": DashboardEmitter(
        module="dashboard/pages/work_queue.py",
        surface="Outcome-selected recovery, prevention, and heuristic work queues",
        provenance="MIXED",
        contains_simulated=True,
        disclosure=(
            "Renders the synthetic-data banner and the outcome-selection warning: membership "
            "already selects simulated denied or open-A/R claims and is not neutral."
        ),
        outputs=("KPI", "chart", "dataframe", "worklist"),
    ),
}


def emitter_for(module: str) -> DashboardEmitter:
    """Return the declared surface for a renderer; missing registration is a defect."""
    try:
        return DASHBOARD_EMITTERS[module]
    except KeyError as error:
        raise ValueError(f"dashboard emitter is not registered: {module}") from error
