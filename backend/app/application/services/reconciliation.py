import re
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any, Iterable

_AMOUNT_FIELD = re.compile(r"^amount(?:_([a-z]{3}))?$", re.IGNORECASE)
_ZERO = Decimal("0.00")


@dataclass(frozen=True, slots=True)
class SettlementRules:
    amount_field: str | None = None
    id_field: str = "order_id"
    source_key_field: str = "source_key"
    status_field: str = "status"
    currency_field: str = "currency"
    paid_statuses: frozenset[str] = field(default_factory=lambda: frozenset({"paid"}))
    cancelled_statuses: frozenset[str] = field(default_factory=lambda: frozenset({"cancelled", "canceled"}))
    confirmed_payout_statuses: frozenset[str] = field(default_factory=lambda: frozenset({"confirmed"}))
    in_transit_payout_statuses: frozenset[str] = field(default_factory=lambda: frozenset({"in_transit"}))
    cost_fields: frozenset[str] = field(default_factory=lambda: frozenset({"cost", "cogs"}))


@dataclass(frozen=True, slots=True)
class ReconciliationSummary:
    currency: str | None
    gross_paid_orders: Decimal
    cancelled_orders_excluded: Decimal
    quarantined_duplicate_orders: Decimal
    total_refunds: Decimal
    net_sales: Decimal
    total_fees: Decimal
    eligible_settlement: Decimal
    confirmed_payouts: Decimal
    in_transit_payouts: Decimal
    unexplained_variance: Decimal
    conclusion: str
    profit_answerable: bool
    missing_cost_reason: str | None


def _amount_key(row: dict[str, Any], rules: SettlementRules) -> str:
    if rules.amount_field:
        return rules.amount_field
    for key in row:
        if _AMOUNT_FIELD.match(key):
            return key
    raise ValueError(
        f"No amount column found in row with columns {sorted(row)}; set SettlementRules.amount_field"
    )


def _amount(row: dict[str, Any], rules: SettlementRules) -> Decimal:
    return Decimal(str(row[_amount_key(row, rules)]))


def _status(row: dict[str, Any], rules: SettlementRules) -> str:
    return str(row.get(rules.status_field, "")).strip().lower()


def _resolve_currency(rows: Iterable[dict[str, Any]], rules: SettlementRules) -> str | None:
    rows = list(rows)
    declared = {str(r[rules.currency_field]).upper() for r in rows if r.get(rules.currency_field)}
    if not declared:
        for r in rows:
            match = _AMOUNT_FIELD.match(_amount_key(r, rules))
            if match and match.group(1):
                declared.add(match.group(1).upper())
    if len(declared) > 1:
        raise ValueError(f"Mixed currencies detected without conversion rule: {sorted(declared)}")
    return next(iter(declared), None)


def _total(rows: Iterable[dict[str, Any]], rules: SettlementRules, statuses: frozenset[str] | None = None) -> Decimal:
    return sum(
        (_amount(r, rules) for r in rows if statuses is None or _status(r, rules) in statuses),
        _ZERO,
    )


def reconcile_orders_settlement(
    orders: list[dict[str, Any]],
    refunds: list[dict[str, Any]],
    fees: list[dict[str, Any]],
    payouts: list[dict[str, Any]],
    *,
    rules: SettlementRules | None = None,
    source_key_field: str | None = None,
) -> ReconciliationSummary:
    rules = rules or SettlementRules()
    if source_key_field:
        rules = replace(rules, source_key_field=source_key_field)

    currency = _resolve_currency((*orders, *refunds, *fees, *payouts), rules)

    seen_keys: set[str] = set()
    gross_paid = _ZERO
    cancelled_excluded = _ZERO
    quarantined_dups = _ZERO
    for row in orders:
        amount = _amount(row, rules)
        key = str(row.get(rules.source_key_field) or row.get(rules.id_field))
        if key in seen_keys:
            quarantined_dups += amount
            continue
        seen_keys.add(key)
        status = _status(row, rules)
        if status in rules.cancelled_statuses:
            cancelled_excluded += amount
        elif status in rules.paid_statuses:
            gross_paid += amount

    total_refunds = _total(refunds, rules)
    net_sales = gross_paid - total_refunds
    total_fees = _total(fees, rules)
    eligible_settlement = net_sales - total_fees
    confirmed_payouts = _total(payouts, rules, rules.confirmed_payout_statuses)
    in_transit_payouts = _total(payouts, rules, rules.in_transit_payout_statuses)
    unexplained = eligible_settlement - (confirmed_payouts + in_transit_payouts)

    if unexplained != _ZERO:
        conclusion = "conflicting_evidence"
    elif in_transit_payouts > _ZERO:
        conclusion = "timing_difference"
    else:
        conclusion = "reconciled"

    has_cost_column = any(any(f in r for f in rules.cost_fields) for r in orders)
    return ReconciliationSummary(
        currency=currency,
        gross_paid_orders=gross_paid,
        cancelled_orders_excluded=cancelled_excluded,
        quarantined_duplicate_orders=quarantined_dups,
        total_refunds=total_refunds,
        net_sales=net_sales,
        total_fees=total_fees,
        eligible_settlement=eligible_settlement,
        confirmed_payouts=confirmed_payouts,
        in_transit_payouts=in_transit_payouts,
        unexplained_variance=unexplained,
        conclusion=conclusion,
        profit_answerable=has_cost_column,
        missing_cost_reason=None if has_cost_column else "Missing COGS/cost column prevents computing profit",
    )
