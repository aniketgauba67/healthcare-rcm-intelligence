"""Healthcare RCM Intelligence — Streamlit dashboard entry point.

    make dashboard      # uv run streamlit run dashboard/app.py

Five pages: Executive overview, Denial prevention, A/R & recovery, Predictive
work queue, Model & data quality. **Every one of them renders the synthetic-data
banner** (CLAUDE.md §6, "no page ships without it"), and this module renders it
too, since the landing view puts numbers on screen like any other page.

WHAT THIS APP IS READING
------------------------
The bundled DuckDB extract under `dashboard/demo_data/`, not a live database.
That is a locked decision (CLAUDE.md §2): the hosted demo ships its own data, so
a clean clone or a free-tier deployment has something to show without credentials,
a network or a warehouse. `RCM_DATA_SOURCE=postgres` points the same pages at a
loaded warehouse for local development; the pages are identical because the
bundle's tables are verbatim copies of the same curated views.

ROLE-LIKE VIEWS
---------------
The sidebar offers **Executive** and **Analyst**. Executive keeps the aggregate
pages and hides claim-level detail; Analyst unlocks the per-claim work queue and
the claim inspector. This demonstrates the shape of a role boundary — it is NOT a
security control, there is no authentication here, and the README says so in the
same words. It is enforced in the app rather than the database for the same reason
the data is bundled: the hosted demo has no database to enforce it in.

THE SYS.PATH LINE AT THE TOP
----------------------------
`streamlit run dashboard/app.py` puts `dashboard/` on `sys.path`, not the repo
root, so `import dashboard` and `import src` would both fail. Prepending the root
here is what makes `make dashboard` work from a clean clone rather than only under
a package install.
"""

from __future__ import annotations

import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st  # noqa: E402

from dashboard import data, disclosures  # noqa: E402
from dashboard.components import (  # noqa: E402
    Kpi,
    data_source_caption,
    kpi_row,
    money,
    percent,
    render_synthetic_data_banner,
    required_disclosures,
)

st.set_page_config(
    page_title="Healthcare RCM Intelligence",
    page_icon=":material/health_metrics:",
    layout="wide",
    initial_sidebar_state="expanded",
)

ROLES = {
    "Executive": (
        "Aggregate views only. Claim-level detail is hidden, the same way a real "
        "deployment would scope it — demonstrated here, not enforced by anything."
    ),
    "Analyst": (
        "Adds the per-claim work queue, the claim inspector and the per-claim SHAP drivers."
    ),
}


def _sidebar() -> str:
    with st.sidebar:
        st.markdown("### Healthcare RCM Intelligence")
        st.caption(disclosures.BANNER_SHORT)
        role = st.radio(
            "Role-like view",
            list(ROLES),
            help=(
                "A demonstration of a role boundary, not a security control. There is no "
                "authentication in this app and nothing here is protected."
            ),
        )
        st.caption(ROLES[role])
        st.divider()
        described = {}
        try:
            described = data.source_description()
        except data.DashboardDataError:
            pass
        if described:
            st.caption(
                f"Data source: **{described.get('kind')}**  \n"
                f"Built at {str(described.get('built_at_utc', 'unknown'))[:19]}  \n"
                f"Commit `{str(described.get('git_commit', 'unknown'))[:12]}`"
            )
        st.divider()
        st.caption(
            "Source code, provenance register, data dictionary and model card ship with "
            "the repository. Every simulated column is prefixed `sim_`."
        )
    return str(role)


def landing() -> None:
    """The landing view: what this is, what is simulated, and the headline book."""
    st.title("Healthcare RCM Intelligence")
    st.caption(
        "Revenue-cycle analytics over official CMS synthetic Medicare claims with a "
        "clearly labelled simulated adjudication layer."
    )
    render_synthetic_data_banner()
    data_source_caption()

    st.markdown(
        "**What is real and what is not.** The claims are official CMS *synthetic* "
        "Medicare inpatient records — real files published by CMS, containing no real "
        "patients. Everything downstream of submission is this project's own "
        "simulation: denials, appeals, payments, timing, workflow cost, and the entire "
        "multi-payer dimension. Nothing here is evidence about how any real payer "
        "behaves, and the two models below are reported with their negative results "
        "intact rather than their best framing."
    )

    try:
        totals = data.executive_totals()
    except data.DashboardDataError as error:
        st.warning(str(error))
        return

    kpi_row(
        [
            Kpi(
                "Claims in the book",
                f"{totals['claims_submitted']:,}",
                "SOURCE",
                "CMS synthetic inpatient claims. Real published file, no real patients.",
            ),
            Kpi(
                "Billed charges",
                money(totals["source_billed_charge_amt"]),
                "SOURCE",
                "clm_tot_chrg_amt as CMS published it.",
            ),
            Kpi(
                "Denial rate",
                percent(totals["denial_rate"], 2),
                "SIMULATED",
                "Denials do not exist in the CMS files. Every one is generated here.",
            ),
            Kpi(
                "Denied dollars",
                money(totals["sim_denied_amt"]),
                "SIMULATED",
                "Simulated denied amount. Not money anybody lost.",
            ),
        ]
    )

    st.divider()
    st.subheader("Two disclosures worth reading before any number in this app")
    required_disclosures(expanded=True)

    st.divider()
    st.subheader("The model results, stated as they came out")
    with st.container(border=True):
        st.markdown(disclosures.MODEL_A_HONESTY)
    with st.container(border=True):
        st.markdown(disclosures.MODEL_C_HONESTY)


def _pages(role: str) -> list:  # noqa: ANN201 - list[st.navigation.Page]
    home = st.Page(landing, title="Overview", icon=":material/home:", default=True)
    executive = st.Page(
        "pages/executive_overview.py", title="Executive overview", icon=":material/analytics:"
    )
    denial = st.Page(
        "pages/denial_prevention.py", title="Denial prevention", icon=":material/block:"
    )
    ar = st.Page("pages/ar_recovery.py", title="A/R & recovery", icon=":material/payments:")
    queue = st.Page(
        "pages/work_queue.py", title="Predictive work queue", icon=":material/list_alt:"
    )
    quality = st.Page(
        "pages/model_data_quality.py", title="Model & data quality", icon=":material/rule:"
    )
    # Both roles see all five pages; the ROLE gates claim-level detail INSIDE them,
    # which is the honest version of this demonstration. Hiding a page would imply
    # the data is protected, and it is not — the bundle ships in the repository.
    return [home, executive, denial, ar, queue, quality]


def main() -> None:
    role = _sidebar()
    st.session_state["role"] = role
    st.navigation({"Dashboard": _pages(role)}).run()


main()
