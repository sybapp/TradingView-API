"""Strategy Spec replay through Nautilus-compatible bar inputs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Union
from zoneinfo import ZoneInfo

from .dataset import FeatureRecord, VersionedDataset, load_versioned_dataset
from .nautilus import NautilusBarInput, replay_bar_inputs, timestamp_to_nanoseconds, to_nautilus_bar_inputs
from .strategy_spec import FeatureEqualsEntryRule, StrategySpec, validate_strategy_spec


EVALUATOR_VERSION = "strategy-replay-v1"


@dataclass(frozen=True)
class CostModel:
    fixed_fee: float
    slippage_ticks: int
    tick_size: float


@dataclass(frozen=True)
class StrategyOrder:
    side: str
    quantity: int
    reason: str
    signal_bar_time: datetime
    execution_bar_time: datetime
    market_price: int
    execution_price: int
    fixed_fee: int
    slippage_cost: int


@dataclass(frozen=True)
class StrategyBacktestResult:
    dataset_id: str
    strategy_id: str
    engine: str
    orders: List[StrategyOrder]
    position_quantity: int
    gross_pnl: int
    total_costs: int
    net_pnl: int
    registry_record_path: Path


@dataclass(frozen=True)
class PendingOrder:
    side: str
    quantity: int
    reason: str
    signal_bar: NautilusBarInput


def run_strategy_backtest(
    *,
    dataset_path: Union[str, Path],
    strategy_spec: Mapping[str, Any],
    cost_model: CostModel,
    registry_path: Union[str, Path],
) -> StrategyBacktestResult:
    dataset = load_versioned_dataset(dataset_path)
    spec = validate_strategy_spec(strategy_spec)
    bars = replay_bar_inputs(to_nautilus_bar_inputs(dataset))
    if len(bars) < 2:
        raise ValueError("dataset must contain at least two bars for next-bar strategy replay")

    orders = _run_compiled_strategy(dataset=dataset, spec=spec, bars=bars, cost_model=cost_model)
    gross_pnl = _gross_pnl(orders)
    total_costs = sum(order.fixed_fee + order.slippage_cost for order in orders)
    result = StrategyBacktestResult(
        dataset_id=dataset.dataset_id,
        strategy_id=spec.strategy_id,
        engine="nautilus-compatible-strategy-replay",
        orders=orders,
        position_quantity=_position_quantity(orders),
        gross_pnl=gross_pnl,
        total_costs=total_costs,
        net_pnl=gross_pnl - total_costs,
        registry_record_path=Path(),
    )
    record_path = _write_run_registry_record(
        registry_path=Path(registry_path),
        dataset=dataset,
        spec=spec,
        cost_model=cost_model,
        result=result,
    )
    return StrategyBacktestResult(
        dataset_id=result.dataset_id,
        strategy_id=result.strategy_id,
        engine=result.engine,
        orders=result.orders,
        position_quantity=result.position_quantity,
        gross_pnl=result.gross_pnl,
        total_costs=result.total_costs,
        net_pnl=result.net_pnl,
        registry_record_path=record_path,
    )


def _run_compiled_strategy(
    *,
    dataset: VersionedDataset,
    spec: StrategySpec,
    bars: List[NautilusBarInput],
    cost_model: CostModel,
) -> List[StrategyOrder]:
    orders: List[StrategyOrder] = []
    pending_order: Optional[PendingOrder] = None
    position_quantity = 0
    flat_at_ns = _flat_at_nanoseconds(dataset, spec.risk_controls.flat_before_close_minutes)

    for index, bar in enumerate(bars):
        if pending_order is not None:
            order = _execute_order(pending_order, bar, cost_model, dataset.price_scale)
            orders.append(order)
            position_quantity += order.quantity if order.side == "buy" else -order.quantity
            pending_order = None

        if bar.ts_event >= flat_at_ns and position_quantity > 0:
            orders.append(
                _execute_order(
                    PendingOrder(
                        side="sell",
                        quantity=position_quantity,
                        reason="intraday-flat-before-close",
                        signal_bar=bar,
                    ),
                    bar,
                    cost_model,
                    dataset.price_scale,
                )
            )
            position_quantity = 0

        is_last_bar = index == len(bars) - 1
        can_enter = not is_last_bar and position_quantity == 0 and pending_order is None
        if can_enter and bars[index + 1].ts_event < flat_at_ns:
            matched_rule = _matching_entry_rule(spec.entry_rules, dataset.features, bar.ts_event)
            if matched_rule is not None:
                pending_order = PendingOrder(
                    side="buy",
                    quantity=spec.sizing.quantity,
                    reason="entry:feature_equals",
                    signal_bar=bar,
                )

    if position_quantity != 0:
        raise ValueError("strategy replay ended with an open position")

    return orders


def _matching_entry_rule(
    rules: List[FeatureEqualsEntryRule],
    features: List[FeatureRecord],
    bar_time_ns: int,
) -> Optional[FeatureEqualsEntryRule]:
    available_features = [
        feature
        for feature in features
        if timestamp_to_nanoseconds(feature.availability_time) <= bar_time_ns
    ]
    for rule in rules:
        if any(
            feature.indicator_id == rule.indicator_id
            and feature.name == rule.name
            and feature.value == rule.value
            for feature in available_features
        ):
            return rule
    return None


def _execute_order(
    pending_order: PendingOrder,
    execution_bar: NautilusBarInput,
    cost_model: CostModel,
    price_scale: int,
) -> StrategyOrder:
    slippage = round(cost_model.slippage_ticks * cost_model.tick_size * price_scale)
    if pending_order.side == "buy":
        execution_price = execution_bar.open + slippage
    else:
        execution_price = execution_bar.open - slippage

    return StrategyOrder(
        side=pending_order.side,
        quantity=pending_order.quantity,
        reason=pending_order.reason,
        signal_bar_time=_datetime_from_nanoseconds(pending_order.signal_bar.ts_event),
        execution_bar_time=_datetime_from_nanoseconds(execution_bar.ts_event),
        market_price=execution_bar.open,
        execution_price=execution_price,
        fixed_fee=round(cost_model.fixed_fee * price_scale) * pending_order.quantity,
        slippage_cost=slippage * pending_order.quantity,
    )


def _gross_pnl(orders: List[StrategyOrder]) -> int:
    if len(orders) % 2 != 0:
        raise ValueError("orders must be paired entries and exits")
    total = 0
    for index in range(0, len(orders), 2):
        entry = orders[index]
        exit_order = orders[index + 1]
        total += (exit_order.market_price - entry.market_price) * entry.quantity
    return total


def _position_quantity(orders: List[StrategyOrder]) -> int:
    quantity = 0
    for order in orders:
        quantity += order.quantity if order.side == "buy" else -order.quantity
    return quantity


def _flat_at_nanoseconds(dataset: VersionedDataset, flat_before_close_minutes: int) -> int:
    session = dataset.manifest["session"]
    timezone = ZoneInfo(str(session["timezone"]))
    session_end = time.fromisoformat(str(session["end"]))
    first_bar_date = dataset.bars[0].time.astimezone(timezone).date()
    session_close = datetime.combine(first_bar_date, session_end, tzinfo=timezone)
    flat_at = session_close - timedelta(minutes=flat_before_close_minutes)
    return timestamp_to_nanoseconds(flat_at)


def _write_run_registry_record(
    *,
    registry_path: Path,
    dataset: VersionedDataset,
    spec: StrategySpec,
    cost_model: CostModel,
    result: StrategyBacktestResult,
) -> Path:
    run_id = _run_id(dataset, spec, cost_model)
    run_path = registry_path / run_id
    run_path.mkdir(parents=True, exist_ok=True)

    orders_path = run_path / "orders.json"
    orders_path.write_text(
        json.dumps([_order_to_json(order) for order in result.orders], indent=2, sort_keys=True),
        encoding="utf-8",
    )

    record = {
        "runId": run_id,
        "dataset": {
            "datasetId": dataset.dataset_id,
            "path": str(dataset.path),
            "schemaVersion": dataset.manifest["schemaVersion"],
            "collectedAt": dataset.manifest["collectedAt"],
            "source": dataset.manifest["source"],
            "symbol": dataset.manifest["symbol"],
            "bar": dataset.manifest["bar"],
            "session": dataset.manifest["session"],
        },
        "strategySpec": spec.raw,
        "costModel": {
            "fixedFee": cost_model.fixed_fee,
            "slippageTicks": cost_model.slippage_ticks,
            "tickSize": cost_model.tick_size,
        },
        "evaluatorVersion": EVALUATOR_VERSION,
        "results": {
            "grossPnl": result.gross_pnl,
            "totalCosts": result.total_costs,
            "netPnl": result.net_pnl,
            "positionQuantity": result.position_quantity,
            "orderCount": len(result.orders),
        },
        "artifacts": {
            "orders": "orders.json",
        },
    }
    record_path = run_path / "run.json"
    record_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    return record_path


def _run_id(dataset: VersionedDataset, spec: StrategySpec, cost_model: CostModel) -> str:
    payload = {
        "datasetId": dataset.dataset_id,
        "strategySpec": spec.raw,
        "costModel": asdict(cost_model),
        "evaluatorVersion": EVALUATOR_VERSION,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return f"{spec.strategy_id}-{digest[:12]}"


def _order_to_json(order: StrategyOrder) -> Dict[str, Any]:
    return {
        "side": order.side,
        "quantity": order.quantity,
        "reason": order.reason,
        "signalBarTime": order.signal_bar_time.isoformat(),
        "executionBarTime": order.execution_bar_time.isoformat(),
        "marketPrice": order.market_price,
        "executionPrice": order.execution_price,
        "fixedFee": order.fixed_fee,
        "slippageCost": order.slippage_cost,
    }


def _datetime_from_nanoseconds(value: int) -> datetime:
    seconds, nanoseconds = divmod(value, 1_000_000_000)
    return datetime.fromtimestamp(seconds, tz=ZoneInfo("UTC")).replace(microsecond=nanoseconds // 1000)
