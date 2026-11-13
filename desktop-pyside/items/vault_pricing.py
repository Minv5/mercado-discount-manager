"""Single definition of how a vault product's price figures relate.

Stored columns: price (售价), cost_price (成本), profit (利润).
Net proceeds (净收益) is the target payout: cost + profit. A product that has
no profit recorded yet falls back to its price.
"""
from __future__ import annotations

from typing import Any, Mapping


def _num(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def net_proceeds(product: Mapping[str, Any]) -> float:
    profit = _num(product.get("profit"))
    if profit != 0.0:
        return round(_num(product.get("cost_price")) + profit, 2)
    return round(_num(product.get("price")), 2)


def profit_for_net(net: Any, cost: Any) -> float:
    return round(_num(net) - _num(cost), 2)
