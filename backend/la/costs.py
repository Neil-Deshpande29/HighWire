"""What a shut-off costs the customers who lose power: a rough dollar figure, from published survey rates.

Rates are the modelled cost per interruption event from Lawrence Berkeley National Laboratory's ICE Calculator 2
(Final Report for Phase 1 and 2, ES Table 3, 2025 dollars), built from surveys of utility customers.
They are what an outage of that length costs one average customer.

Two things make the result rough, and the screen says both in one line:
  * the number of customers cut off is itself modelled (customers per megawatt), not counted from utility records;
  * the split between homes and businesses is an assumption (BUSINESS_SHARE), and businesses carry most of the cost.
It is the cost of the outage to customers. It is not the utility's cost, and it puts no price on a fire avoided.
"""
from __future__ import annotations

from math import floor, log10

SOURCE = "LBNL ICE Calculator 2 final report, ES Table 3, 2025 dollars"
# cost per interruption event for one average customer, by how long the power is out (hours)
PER_EVENT = {
    "home": {2: 10.91, 8: 26.62, 24: 56.66},
    "business": {2: 3571.0, 8: 7708.0, 24: 13878.0},
}
BUSINESS_SHARE = 0.125  # assumed: one customer in eight is a business. Not measured for this grid.
HOURS = (8, 24)  # the lengths shown: a working day and a full day, both published points (no interpolation)


def _two_figures(x: float) -> int:
    """Round to two significant figures: the inputs do not support more."""
    if x <= 0:
        return 0
    return int(round(x, 1 - int(floor(log10(x)))))


def outage_cost(customers: int) -> dict:
    """Dollars lost by `customers` who lose power, if the shut-off lasts each of HOURS."""
    per_customer = {
        h: (1 - BUSINESS_SHARE) * PER_EVENT["home"][h] + BUSINESS_SHARE * PER_EVENT["business"][h] for h in HOURS
    }
    return {
        "customers": int(customers),
        "usd": {str(h): _two_figures(customers * per_customer[h]) for h in HOURS},
        "business_share": BUSINESS_SHARE,
        "source": SOURCE,
    }
