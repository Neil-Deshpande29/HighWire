"""Who may approve a plan: the operator alone, or only with a supervisor's sign-off.

The limits below are this project's demo protocol, not any utility's policy. A utility would put its own numbers
here. The screen always shows which rule was hit, so the limit is never hidden from the person deciding, and the
approve route enforces it: a plan over the limit is refused unless it is approved in the supervisor role.
"""
from __future__ import annotations

OPERATOR_MAX_CUSTOMERS = 50_000  # customers losing power that an operator may approve alone
BASIS = (f"Demo protocol: an operator may approve alone when no more than {OPERATOR_MAX_CUSTOMERS:,} customers lose power and no "
         "hospital goes to backup power. Set in backend/la/authority.py.")


def assess(is_safe: bool, customers_out: int, hospitals_on_backup: int) -> dict:
    """`level` is "blocked" (fails the physics check: nobody may approve it), "supervisor" or "operator"."""
    if not is_safe:
        return {"level": "blocked", "reasons": ["it fails the physics check"], "basis": BASIS}
    reasons = []
    if customers_out > OPERATOR_MAX_CUSTOMERS:
        reasons.append(f"{customers_out:,} customers lose power (operator limit {OPERATOR_MAX_CUSTOMERS:,})")
    if hospitals_on_backup:
        reasons.append(f"{hospitals_on_backup} hospital{'s' if hospitals_on_backup != 1 else ''} would run on backup power")
    return {"level": "supervisor" if reasons else "operator", "reasons": reasons, "basis": BASIS}


def of_result(result: dict) -> dict:
    """Authority for a plan's verified result (the `result` block every plan carries)."""
    hospitals = result.get("hospitals_on_backup")
    if hospitals is None:
        hospitals = (result.get("buildings_out") or {}).get("hospitals", 0)
    return assess(bool(result.get("is_safe")), int(result.get("customers_out") or 0), int(hospitals or 0))
