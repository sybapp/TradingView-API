"""Strategy Spec replay through Nautilus-compatible bar inputs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta
import hashlib
import json
import math
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
class WalkForwardConfig:
    training_bars: int
    scoring_bars: int
    step_bars: Optional[int] = None


@dataclass(frozen=True)
class FitnessConstraints:
    min_trades: int = 10
    max_drawdown: Optional[int] = None
    max_cost_to_gross_ratio: Optional[float] = None
    max_slippage_costs: Optional[int] = None
    min_profitable_windows: int = 1
    min_profitable_window_ratio: Optional[float] = None


@dataclass(frozen=True)
class WindowRange:
    start: str
    end: str
    start_index: int
    end_index: int


@dataclass(frozen=True)
class WalkForwardWindow:
    window_id: str
    training: WindowRange
    scoring: WindowRange


@dataclass(frozen=True)
class WalkForwardWindowResult:
    window_id: str
    orders: List[StrategyOrder]
    gross_pnl: int
    total_costs: int
    net_pnl: int
    order_count: int
    trade_count: int
    max_drawdown: int


@dataclass(frozen=True)
class FitnessResult:
    survived: bool
    score: Optional[float]
    rejection_reasons: List[str]
    survival_checks: Dict[str, Dict[str, Any]]
    ranking_inputs: Dict[str, Any]


@dataclass(frozen=True)
class WalkForwardBacktestResult:
    dataset_id: str
    strategy_id: str
    engine: str
    windows: List[WalkForwardWindow]
    training_window_results: List[WalkForwardWindowResult]
    window_results: List[WalkForwardWindowResult]
    fitness: FitnessResult
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
    result = _evaluate_dataset_slice(dataset=dataset, spec=spec, cost_model=cost_model)
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


def run_walk_forward_backtest(
    *,
    dataset_path: Union[str, Path],
    strategy_spec: Mapping[str, Any],
    cost_model: CostModel,
    registry_path: Union[str, Path],
    walk_forward: WalkForwardConfig,
    fitness_constraints: FitnessConstraints,
) -> WalkForwardBacktestResult:
    dataset = load_versioned_dataset(dataset_path)
    spec = validate_strategy_spec(strategy_spec)
    windows = _build_walk_forward_windows(dataset, walk_forward)

    training_window_results: List[WalkForwardWindowResult] = []
    scoring_window_results: List[WalkForwardWindowResult] = []
    for window in windows:
        training_dataset = _slice_dataset_for_window(dataset, window.training)
        training_result = _evaluate_dataset_slice(
            dataset=training_dataset,
            spec=spec,
            cost_model=cost_model,
            force_flat_at_end=True,
        )
        training_window_results.append(_walk_forward_result(window.window_id, training_result))

        scoring_dataset = _slice_dataset_for_window(dataset, window.scoring)
        scoring_result = _evaluate_dataset_slice(
            dataset=scoring_dataset,
            spec=spec,
            cost_model=cost_model,
            force_flat_at_end=True,
        )
        scoring_window_results.append(_walk_forward_result(window.window_id, scoring_result))

    fitness = _evaluate_fitness(scoring_window_results, fitness_constraints)
    record_path = _write_walk_forward_registry_record(
        registry_path=Path(registry_path),
        dataset=dataset,
        spec=spec,
        cost_model=cost_model,
        walk_forward=walk_forward,
        constraints=fitness_constraints,
        windows=windows,
        training_window_results=training_window_results,
        scoring_window_results=scoring_window_results,
        fitness=fitness,
    )

    return WalkForwardBacktestResult(
        dataset_id=dataset.dataset_id,
        strategy_id=spec.strategy_id,
        engine="nautilus-compatible-walk-forward-replay",
        windows=windows,
        training_window_results=training_window_results,
        window_results=scoring_window_results,
        fitness=fitness,
        registry_record_path=record_path,
    )


def _evaluate_dataset_slice(
    *,
    dataset: VersionedDataset,
    spec: StrategySpec,
    cost_model: CostModel,
    force_flat_at_end: bool = False,
) -> StrategyBacktestResult:
    bars = replay_bar_inputs(to_nautilus_bar_inputs(dataset))
    if len(bars) < 2:
        raise ValueError("dataset must contain at least two bars for next-bar strategy replay")

    orders = _run_compiled_strategy(
        dataset=dataset,
        spec=spec,
        bars=bars,
        cost_model=cost_model,
        force_flat_at_end=force_flat_at_end,
    )
    gross_pnl = _gross_pnl(orders)
    total_costs = sum(order.fixed_fee + order.slippage_cost for order in orders)
    return StrategyBacktestResult(
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


def _run_compiled_strategy(
    *,
    dataset: VersionedDataset,
    spec: StrategySpec,
    bars: List[NautilusBarInput],
    cost_model: CostModel,
    force_flat_at_end: bool = False,
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

    if position_quantity != 0 and force_flat_at_end:
        last_bar = bars[-1]
        orders.append(
            _execute_order(
                PendingOrder(
                    side="sell",
                    quantity=position_quantity,
                    reason="window-end-flat",
                    signal_bar=last_bar,
                ),
                last_bar,
                cost_model,
                dataset.price_scale,
            )
        )
        position_quantity = 0

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


def _build_walk_forward_windows(
    dataset: VersionedDataset,
    config: WalkForwardConfig,
) -> List[WalkForwardWindow]:
    if config.training_bars <= 1:
        raise ValueError("walk_forward.training_bars must be greater than 1")
    if config.scoring_bars <= 1:
        raise ValueError("walk_forward.scoring_bars must be greater than 1")
    step_bars = config.step_bars if config.step_bars is not None else config.scoring_bars
    if step_bars <= 0:
        raise ValueError("walk_forward.step_bars must be positive")

    windows: List[WalkForwardWindow] = []
    start_index = 0
    while start_index + config.training_bars + config.scoring_bars <= len(dataset.bars):
        training_start = start_index
        training_end = start_index + config.training_bars
        scoring_start = training_end
        scoring_end = scoring_start + config.scoring_bars
        windows.append(
            WalkForwardWindow(
                window_id=f"wf-{len(windows) + 1}",
                training=_window_range(dataset, training_start, training_end),
                scoring=_window_range(dataset, scoring_start, scoring_end),
            )
        )
        start_index += step_bars

    if not windows:
        raise ValueError("dataset does not contain enough bars for the walk-forward configuration")
    return windows


def _window_range(dataset: VersionedDataset, start_index: int, end_index: int) -> WindowRange:
    return WindowRange(
        start=dataset.bars[start_index].time.isoformat(),
        end=dataset.bars[end_index - 1].time.isoformat(),
        start_index=start_index,
        end_index=end_index - 1,
    )


def _slice_dataset_for_window(dataset: VersionedDataset, scoring: WindowRange) -> VersionedDataset:
    bars = dataset.bars[scoring.start_index : scoring.end_index + 1]
    start_time = bars[0].time
    end_time = bars[-1].time
    features = [
        feature
        for feature in dataset.features
        if start_time <= feature.availability_time <= end_time
    ]
    return VersionedDataset(
        manifest=dataset.manifest,
        bars=bars,
        features=features,
        path=dataset.path,
    )


def _walk_forward_result(
    window_id: str,
    result: StrategyBacktestResult,
) -> WalkForwardWindowResult:
    return WalkForwardWindowResult(
        window_id=window_id,
        orders=result.orders,
        gross_pnl=result.gross_pnl,
        total_costs=result.total_costs,
        net_pnl=result.net_pnl,
        order_count=len(result.orders),
        trade_count=len(result.orders) // 2,
        max_drawdown=_max_drawdown(_trade_net_pnls(result.orders)),
    )


def _evaluate_fitness(
    window_results: List[WalkForwardWindowResult],
    constraints: FitnessConstraints,
) -> FitnessResult:
    trade_count = sum(result.trade_count for result in window_results)
    gross_pnl = sum(result.gross_pnl for result in window_results)
    total_costs = sum(result.total_costs for result in window_results)
    net_pnl = sum(result.net_pnl for result in window_results)
    max_drawdown = max((result.max_drawdown for result in window_results), default=0)
    slippage_costs = sum(
        order.slippage_cost
        for result in window_results
        for order in result.orders
    )
    profitable_windows = sum(1 for result in window_results if result.net_pnl > 0)
    profitable_window_ratio = profitable_windows / len(window_results) if window_results else 0.0
    cost_to_gross_ratio = _cost_to_gross_ratio(total_costs, gross_pnl)
    out_of_sample_sharpe = _sharpe_ratio([result.net_pnl for result in window_results])

    checks: Dict[str, Dict[str, Any]] = {
        "minTrades": {
            "actual": trade_count,
            "required": constraints.min_trades,
            "passed": trade_count >= constraints.min_trades,
        },
        "minProfitableWindows": {
            "actual": profitable_windows,
            "required": constraints.min_profitable_windows,
            "passed": profitable_windows >= constraints.min_profitable_windows,
        },
    }
    if constraints.max_drawdown is not None:
        checks["maxDrawdown"] = {
            "actual": max_drawdown,
            "maximum": constraints.max_drawdown,
            "passed": max_drawdown <= constraints.max_drawdown,
        }
    if constraints.max_cost_to_gross_ratio is not None:
        checks["maxCostToGrossRatio"] = {
            "actual": cost_to_gross_ratio,
            "maximum": constraints.max_cost_to_gross_ratio,
            "passed": (
                cost_to_gross_ratio is not None
                and cost_to_gross_ratio <= constraints.max_cost_to_gross_ratio
            ),
        }
    if constraints.max_slippage_costs is not None:
        checks["maxSlippageCosts"] = {
            "actual": slippage_costs,
            "maximum": constraints.max_slippage_costs,
            "passed": slippage_costs <= constraints.max_slippage_costs,
        }
    if constraints.min_profitable_window_ratio is not None:
        checks["minProfitableWindowRatio"] = {
            "actual": profitable_window_ratio,
            "required": constraints.min_profitable_window_ratio,
            "passed": profitable_window_ratio >= constraints.min_profitable_window_ratio,
        }

    rejection_reasons = [_check_rejection_reason(name) for name, check in checks.items() if not check["passed"]]
    survived = len(rejection_reasons) == 0
    ranking_inputs = {
        "outOfSampleSharpe": out_of_sample_sharpe,
        "netPnl": net_pnl,
        "grossPnl": gross_pnl,
        "totalCosts": total_costs,
        "tradeCount": trade_count,
        "maxDrawdown": max_drawdown,
        "slippageCosts": slippage_costs,
        "profitableWindows": profitable_windows,
        "profitableWindowRatio": profitable_window_ratio,
        "costToGrossRatio": cost_to_gross_ratio,
    }
    return FitnessResult(
        survived=survived,
        score=out_of_sample_sharpe if survived else None,
        rejection_reasons=rejection_reasons,
        survival_checks=checks,
        ranking_inputs=ranking_inputs,
    )


def _check_rejection_reason(check_name: str) -> str:
    reasons = {
        "minTrades": "min_trades",
        "maxDrawdown": "max_drawdown",
        "maxCostToGrossRatio": "max_cost_to_gross_ratio",
        "maxSlippageCosts": "max_slippage_costs",
        "minProfitableWindows": "min_profitable_windows",
        "minProfitableWindowRatio": "min_profitable_window_ratio",
    }
    return reasons[check_name]


def _trade_net_pnls(orders: List[StrategyOrder]) -> List[int]:
    pnls: List[int] = []
    for index in range(0, len(orders), 2):
        entry = orders[index]
        exit_order = orders[index + 1]
        gross = (exit_order.market_price - entry.market_price) * entry.quantity
        costs = entry.fixed_fee + entry.slippage_cost + exit_order.fixed_fee + exit_order.slippage_cost
        pnls.append(gross - costs)
    return pnls


def _max_drawdown(pnls: List[int]) -> int:
    equity = 0
    peak = 0
    drawdown = 0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def _cost_to_gross_ratio(total_costs: int, gross_pnl: int) -> Optional[float]:
    if gross_pnl == 0:
        return 0.0 if total_costs == 0 else None
    return total_costs / abs(gross_pnl)


def _sharpe_ratio(values: List[int]) -> float:
    if len(values) < 2:
        return 0.0
    average = sum(values) / len(values)
    variance = sum((value - average) ** 2 for value in values) / (len(values) - 1)
    standard_deviation = math.sqrt(variance)
    if standard_deviation == 0:
        return average
    return average / standard_deviation


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


def _write_walk_forward_registry_record(
    *,
    registry_path: Path,
    dataset: VersionedDataset,
    spec: StrategySpec,
    cost_model: CostModel,
    walk_forward: WalkForwardConfig,
    constraints: FitnessConstraints,
    windows: List[WalkForwardWindow],
    training_window_results: List[WalkForwardWindowResult],
    scoring_window_results: List[WalkForwardWindowResult],
    fitness: FitnessResult,
) -> Path:
    run_id = _walk_forward_run_id(dataset, spec, cost_model, walk_forward, constraints)
    run_path = registry_path / run_id
    run_path.mkdir(parents=True, exist_ok=True)

    orders_path = run_path / "orders-by-window.json"
    orders_path.write_text(
        json.dumps(
            {
                f"{phase}:{result.window_id}": [_order_to_json(order) for order in result.orders]
                for phase, results in (
                    ("training", training_window_results),
                    ("scoring", scoring_window_results),
                )
                for result in results
            },
            indent=2,
            sort_keys=True,
        ),
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
        "costModel": _cost_model_to_json(cost_model),
        "evaluatorVersion": EVALUATOR_VERSION,
        "searchConfiguration": {
            "type": "fixed-strategy-spec",
            "description": "No optimizer is run in this slice; training windows are replayed separately from scoring windows.",
        },
        "walkForward": {
            "config": _walk_forward_config_to_json(walk_forward),
            "windows": [_walk_forward_window_to_json(window) for window in windows],
        },
        "trainingWindowResults": [
            _walk_forward_window_result_to_json(result)
            for result in training_window_results
        ],
        "perWindowResults": [
            _walk_forward_window_result_to_json(result)
            for result in scoring_window_results
        ],
        "fitness": _fitness_to_json(fitness),
        "artifacts": {
            "ordersByWindow": "orders-by-window.json",
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


def _walk_forward_run_id(
    dataset: VersionedDataset,
    spec: StrategySpec,
    cost_model: CostModel,
    walk_forward: WalkForwardConfig,
    constraints: FitnessConstraints,
) -> str:
    payload = {
        "datasetId": dataset.dataset_id,
        "strategySpec": spec.raw,
        "costModel": asdict(cost_model),
        "walkForward": asdict(walk_forward),
        "fitnessConstraints": asdict(constraints),
        "evaluatorVersion": EVALUATOR_VERSION,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return f"{spec.strategy_id}-walk-forward-{digest[:12]}"


def _cost_model_to_json(cost_model: CostModel) -> Dict[str, Any]:
    return {
        "fixedFee": cost_model.fixed_fee,
        "slippageTicks": cost_model.slippage_ticks,
        "tickSize": cost_model.tick_size,
    }


def _walk_forward_config_to_json(config: WalkForwardConfig) -> Dict[str, Any]:
    return {
        "trainingBars": config.training_bars,
        "scoringBars": config.scoring_bars,
        "stepBars": config.step_bars if config.step_bars is not None else config.scoring_bars,
    }


def _walk_forward_window_to_json(window: WalkForwardWindow) -> Dict[str, Any]:
    return {
        "windowId": window.window_id,
        "training": _window_range_to_json(window.training),
        "scoring": _window_range_to_json(window.scoring),
    }


def _window_range_to_json(window_range: WindowRange) -> Dict[str, Any]:
    return {
        "start": window_range.start,
        "end": window_range.end,
        "startIndex": window_range.start_index,
        "endIndex": window_range.end_index,
    }


def _walk_forward_window_result_to_json(result: WalkForwardWindowResult) -> Dict[str, Any]:
    return {
        "windowId": result.window_id,
        "grossPnl": result.gross_pnl,
        "totalCosts": result.total_costs,
        "netPnl": result.net_pnl,
        "orderCount": result.order_count,
        "tradeCount": result.trade_count,
        "maxDrawdown": result.max_drawdown,
    }


def _fitness_to_json(fitness: FitnessResult) -> Dict[str, Any]:
    return {
        "survived": fitness.survived,
        "score": fitness.score,
        "rejectionReasons": fitness.rejection_reasons,
        "survivalChecks": fitness.survival_checks,
        "rankingInputs": fitness.ranking_inputs,
    }


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
