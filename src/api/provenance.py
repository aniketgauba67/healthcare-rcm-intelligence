"""Response provenance: the metadata block every payload carries.

The hard rule for this service is docs/project_rules.md §3 restated at the wire: **any
response containing `sim_` fields carries `contains_simulated: true`.** A JSON
body is read by a program and then, eventually, by a person looking at a log or a
notebook cell, with none of the surrounding pages. So the flag is not decoration
— it is the only thing travelling with the payload that says the dollar figure in
it describes a denial that never happened.

`contains_simulated` is MEASURED from the payload, not asserted by the endpoint
author. An endpoint that grows a simulated field later gets the flag without
anyone remembering, which is the failure mode this project has already paid for
once: a check that only fires when someone updates it is a check that stops
firing.

The prose is imported from `dashboard/disclosures.py` rather than restated here,
so the API and the screen cannot drift into different wordings of the same
disclosure.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from dashboard.disclosures import API_SIMULATED_NOTICE

API_VERSION = "1.0.0"

#: Simulated quantities whose names do not start with `sim_`. Declared, because
#: the prefix rule cannot see them.
#:
#: NOTHING ON THE WIRE MATCHES THIS SET TODAY, and that is the intended end state
#: rather than a reason to delete it. These are the pre-rename spellings of four
#: model outputs; the schemas now publish `sim_denial_risk`,
#: `sim_denial_risk_uncalibrated`, `sim_p_overturn` and `sim_expected_net_recovery`,
#: so the prefix rule sees them without help. The set is kept as a tripwire: if a
#: bare spelling ever comes back, the disclosure still reports it instead of
#: silently omitting it, which is exactly the failure `/metrics/executive` had.
_SIMULATED_UNPREFIXED: frozenset[str] = frozenset(
    {
        "denial_risk",
        "denial_risk_uncalibrated",
        "p_overturn",
        "expected_net_recovery",
    }
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def simulated_fields(payload: Any, prefix: str = "") -> list[str]:
    """Every `sim_`-prefixed key anywhere in a nested payload, dotted.

    Walks the structure instead of inspecting the top level: a claim record nests
    its simulated money one level down, and a top-level-only check would report a
    response full of simulated dollars as clean.
    """
    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            path = f"{prefix}{key}"
            if str(key).startswith("sim_") or str(key) in _SIMULATED_UNPREFIXED:
                found.append(path)
            found.extend(simulated_fields(value, prefix=f"{path}."))
    elif isinstance(payload, (list, tuple)):
        # One representative element. A 500-row work queue would otherwise report
        # the same field name 500 times and drown the list it exists to make
        # readable; the field set is identical across rows by construction.
        if payload:
            found.extend(simulated_fields(payload[0], prefix=f"{prefix}[]."))
    return found


def response_meta(
    payload: Any,
    data_source: dict[str, object],
    datasets: tuple[str, ...] = (),
) -> dict[str, Any]:
    """The metadata block, with `contains_simulated` measured rather than asserted.

    Measured two ways, because the name test alone is not sufficient and the
    project has the receipt for that. A response of denial RATES carries dollars
    and percentages computed entirely from events this project generated, under
    names like `denial_rate` that hold no `sim_` marker — the same shape as
    [QUEUE-PREFIX], where a simulated quantity lost its marker on the way out. So
    the flag is the OR of:

    * any `sim_`-prefixed (or declared-unprefixed) field in the payload, and
    * the declared `contains_simulated` of every dataset the response was built
      from, read from `src/demo/spec.py`.

    A response of aggregates over a simulated book is therefore flagged even
    though not one of its field names says so.
    """
    from src.demo import spec

    fields = sorted(set(simulated_fields(payload)))
    from_datasets = sorted(
        name for name in datasets if spec.DATASETS_BY_NAME[name].contains_simulated
    )
    contains = bool(fields or from_datasets)
    return {
        "contains_simulated": contains,
        "simulated_fields": fields,
        "simulated_datasets": from_datasets,
        "notice": API_SIMULATED_NOTICE if contains else "",
        "generated_at": utc_now(),
        "api_version": API_VERSION,
        "data_source": data_source,
    }
