"""Strategy Spec replay through Nautilus-compatible bar inputs."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timedelta
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union
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
    training_sessions: Optional[int] = None
    scoring_sessions: Optional[int] = None
    step_sessions: Optional[int] = None


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
    start_session_date: str
    end_session_date: str
    session_count: int
    bar_count: int


@dataclass(frozen=True)
class WalkForwardWindow:
    window_id: str
    training: WindowRange
    scoring: WindowRange


@dataclass(frozen=True)
class WalkForwardWindowResult:
    window_id: str
    start_session_date: str
    end_session_date: str
    session_count: int
    bar_count: int
    orders: List[StrategyOrder]
    gross_pnl: int
    total_costs: int
    net_pnl: int
    order_count: int
    trade_count: int
    max_drawdown: int
    result_summary: Dict[str, Any]
    nautilus_provenance: Dict[str, Any]
    environment: Dict[str, Any]
    instrument: Dict[str, Any]
    venue: Dict[str, Any]
    bar_type: Dict[str, Any]
    cost_configuration: Dict[str, Any]
    fills_report: Any
    positions_report: Any
    account_report: Any


@dataclass(frozen=True)
class WalkForwardCandidateWindowResult:
    candidate_id: str
    strategy_id: str
    strategy_spec: Dict[str, Any]
    training_result: WalkForwardWindowResult
    selection_ranking_inputs: Dict[str, Any]


@dataclass(frozen=True)
class WalkForwardWindowSelection:
    window_id: str
    training: WindowRange
    scoring: WindowRange
    candidate_results: List[WalkForwardCandidateWindowResult]
    selected_candidate_id: str
    selected_strategy_id: str
    selected_strategy_spec: Dict[str, Any]
    selected_training_result: WalkForwardWindowResult
    selection_ranking_inputs: Dict[str, Any]


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
    selection_results: List[WalkForwardWindowSelection] = field(default_factory=list)


@dataclass(frozen=True)
class PendingOrder:
    side: str
    quantity: int
    reason: str
    signal_bar: NautilusBarInput


@dataclass(frozen=True)
class _DatasetSession:
    session_id: str
    session_date: str
    start_index: int
    end_index: int


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
    return StrategyBacktestResult(
        dataset_id=result.dataset_id,
        strategy_id=result.strategy_id,
        engine=result.engine,
        orders=result.orders,
        position_quantity=result.position_quantity,
        gross_pnl=result.gross_pnl,
        total_costs=result.total_costs,
        net_pnl=result.net_pnl,
        registry_record_path=Path(),
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
    from .validation import run_nautilus_validation_dataset

    dataset = load_versioned_dataset(dataset_path)
    spec = validate_strategy_spec(strategy_spec)
    windows = _build_walk_forward_windows(dataset, walk_forward)

    training_window_results: List[WalkForwardWindowResult] = []
    scoring_window_results: List[WalkForwardWindowResult] = []
    for window in windows:
        training_dataset = _slice_dataset_for_window(dataset, window.training)
        training_validation = run_nautilus_validation_dataset(
            dataset=training_dataset,
            spec=spec,
            cost_model=cost_model,
        )
        training_window_results.append(_walk_forward_result(window.window_id, window.training, training_validation))

        scoring_dataset = _slice_dataset_for_window(dataset, window.scoring)
        scoring_validation = run_nautilus_validation_dataset(
            dataset=scoring_dataset,
            spec=spec,
            cost_model=cost_model,
        )
        scoring_window_results.append(_walk_forward_result(window.window_id, window.scoring, scoring_validation))

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
        engine="nautilus-trader-walk-forward-validation",
        windows=windows,
        training_window_results=training_window_results,
        window_results=scoring_window_results,
        fitness=fitness,
        registry_record_path=record_path,
    )


def run_walk_forward_candidate_selection_backtest(
    *,
    dataset_path: Union[str, Path],
    candidate_specs: Sequence[Mapping[str, Any]],
    cost_model: CostModel,
    registry_path: Union[str, Path],
    walk_forward: WalkForwardConfig,
    fitness_constraints: FitnessConstraints,
) -> WalkForwardBacktestResult:
    from .validation import run_nautilus_validation_dataset

    if not candidate_specs:
        raise ValueError("candidate_specs must contain at least one Strategy Spec")

    dataset = load_versioned_dataset(dataset_path)
    specs = [validate_strategy_spec(candidate) for candidate in candidate_specs]
    windows = _build_walk_forward_windows(dataset, walk_forward)

    selection_results: List[WalkForwardWindowSelection] = []
    selected_training_results: List[WalkForwardWindowResult] = []
    scoring_window_results: List[WalkForwardWindowResult] = []
    for window in windows:
        training_dataset = _slice_dataset_for_window(dataset, window.training)
        candidate_results: List[WalkForwardCandidateWindowResult] = []
        for index, spec in enumerate(specs, start=1):
            training_validation = run_nautilus_validation_dataset(
                dataset=training_dataset,
                spec=spec,
                cost_model=cost_model,
            )
            training_result = _walk_forward_result(window.window_id, window.training, training_validation)
            candidate_results.append(
                WalkForwardCandidateWindowResult(
                    candidate_id=f"candidate-{index}",
                    strategy_id=spec.strategy_id,
                    strategy_spec=spec.raw,
                    training_result=training_result,
                    selection_ranking_inputs=_selection_ranking_inputs(training_result),
                )
            )

        selected_candidate = _select_training_window_candidate(candidate_results)
        selected_training_results.append(selected_candidate.training_result)

        scoring_dataset = _slice_dataset_for_window(dataset, window.scoring)
        scoring_validation = run_nautilus_validation_dataset(
            dataset=scoring_dataset,
            spec=validate_strategy_spec(selected_candidate.strategy_spec),
            cost_model=cost_model,
        )
        scoring_window_results.append(_walk_forward_result(window.window_id, window.scoring, scoring_validation))
        selection_results.append(
            WalkForwardWindowSelection(
                window_id=window.window_id,
                training=window.training,
                scoring=window.scoring,
                candidate_results=candidate_results,
                selected_candidate_id=selected_candidate.candidate_id,
                selected_strategy_id=selected_candidate.strategy_id,
                selected_strategy_spec=selected_candidate.strategy_spec,
                selected_training_result=selected_candidate.training_result,
                selection_ranking_inputs=selected_candidate.selection_ranking_inputs,
            )
        )

    fitness = _evaluate_fitness(scoring_window_results, fitness_constraints)
    record_path = _write_walk_forward_registry_record(
        registry_path=Path(registry_path),
        dataset=dataset,
        spec=specs[0],
        cost_model=cost_model,
        walk_forward=walk_forward,
        constraints=fitness_constraints,
        windows=windows,
        training_window_results=selected_training_results,
        scoring_window_results=scoring_window_results,
        fitness=fitness,
        selection_results=selection_results,
    )

    return WalkForwardBacktestResult(
        dataset_id=dataset.dataset_id,
        strategy_id="walk-forward-selected-candidates",
        engine="nautilus-trader-walk-forward-training-window-selection",
        windows=windows,
        training_window_results=selected_training_results,
        window_results=scoring_window_results,
        fitness=fitness,
        registry_record_path=record_path,
        selection_results=selection_results,
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
    entry_bar_price: Optional[int] = None
    entry_bar_index: Optional[int] = None
    entry_bar_time_ns: Optional[int] = None
    entry_taken = False
    allow_reentry = "cooldownBarsAfterExit" in spec.raw.get("riskControls", {})
    last_exit_bar_index: Optional[int] = None

    for index, bar in enumerate(bars):
        if pending_order is not None:
            if (
                pending_order.side == "sell"
                and pending_order.reason == "exit:reverse-signal"
                and entry_bar_price is not None
            ):
                stop_price = entry_bar_price - round(
                    spec.risk_controls.stop_loss_ticks * cost_model.tick_size * dataset.price_scale
                )
                if bar.low <= stop_price:
                    pending_order = PendingOrder(
                        side="sell",
                        quantity=pending_order.quantity,
                        reason="stop-loss",
                        signal_bar=bar,
                    )
            order = _execute_order(pending_order, bar, cost_model, dataset.price_scale)
            orders.append(order)
            position_quantity += order.quantity if order.side == "buy" else -order.quantity
            if order.side == "buy":
                entry_taken = True
                entry_bar_price = bar.open
                entry_bar_index = index
                entry_bar_time_ns = bar.ts_event
            else:
                if allow_reentry:
                    entry_taken = False
                entry_bar_price = None
                entry_bar_index = None
                entry_bar_time_ns = None
                last_exit_bar_index = index
            pending_order = None

        if (
            position_quantity > 0
            and entry_bar_price is not None
            and entry_bar_index is not None
            and entry_bar_time_ns is not None
        ):
            stop_price = entry_bar_price - round(spec.risk_controls.stop_loss_ticks * cost_model.tick_size * dataset.price_scale)
            if bar.low <= stop_price:
                orders.append(
                    _execute_order(
                        PendingOrder(
                            side="sell",
                            quantity=position_quantity,
                            reason="stop-loss",
                            signal_bar=bar,
                        ),
                        bar,
                        cost_model,
                        dataset.price_scale,
                    )
                )
                position_quantity = 0
                if allow_reentry:
                    entry_taken = False
                entry_bar_price = None
                entry_bar_index = None
                entry_bar_time_ns = None
                last_exit_bar_index = index
                continue

            matched_exit_rule = _matching_feature_rule(
                spec.exits.reverse_signal_rules,
                dataset.features,
                bar.ts_event,
                earliest_feature_ns=entry_bar_time_ns,
            )
            if matched_exit_rule is not None and index + 1 < len(bars) and bars[index + 1].ts_event < flat_at_ns:
                pending_order = PendingOrder(
                    side="sell",
                    quantity=position_quantity,
                    reason="exit:reverse-signal",
                    signal_bar=bar,
                )
                continue

            max_bars = spec.exits.max_bars_in_trade
            if max_bars is not None and index - entry_bar_index >= max_bars:
                orders.append(
                    _execute_order(
                        PendingOrder(
                            side="sell",
                            quantity=position_quantity,
                            reason="max-bars-in-trade",
                            signal_bar=bar,
                        ),
                        bar,
                        cost_model,
                        dataset.price_scale,
                    )
                )
                position_quantity = 0
                if allow_reentry:
                    entry_taken = False
                entry_bar_price = None
                entry_bar_index = None
                entry_bar_time_ns = None
                last_exit_bar_index = index
                continue

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
            if allow_reentry:
                entry_taken = False
            entry_bar_price = None
            entry_bar_index = None
            entry_bar_time_ns = None
            last_exit_bar_index = index
            continue

        is_last_bar = index == len(bars) - 1
        can_enter = (
            not is_last_bar
            and position_quantity == 0
            and pending_order is None
            and not entry_taken
        )
        cooldown_complete = (
            last_exit_bar_index is None
            or index - last_exit_bar_index > spec.risk_controls.cooldown_bars_after_exit
        )
        if can_enter and cooldown_complete and bars[index + 1].ts_event < flat_at_ns:
            if _all_feature_rules_match(spec.entry_rules, dataset.features, bar.ts_event):
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


def _all_feature_rules_match(
    rules: List[FeatureEqualsEntryRule],
    features: List[FeatureRecord],
    bar_time_ns: int,
    *,
    earliest_feature_ns: Optional[int] = None,
) -> bool:
    return bool(rules) and all(
        _feature_rule_matches(
            rule,
            features,
            bar_time_ns,
            earliest_feature_ns=earliest_feature_ns,
        )
        for rule in rules
    )


def _matching_feature_rule(
    rules: List[FeatureEqualsEntryRule],
    features: List[FeatureRecord],
    bar_time_ns: int,
    *,
    earliest_feature_ns: Optional[int] = None,
) -> Optional[FeatureEqualsEntryRule]:
    for rule in rules:
        if _feature_rule_matches(
            rule,
            features,
            bar_time_ns,
            earliest_feature_ns=earliest_feature_ns,
        ):
            return rule
    return None


def _feature_rule_matches(
    rule: FeatureEqualsEntryRule,
    features: List[FeatureRecord],
    bar_time_ns: int,
    *,
    earliest_feature_ns: Optional[int] = None,
) -> bool:
    available_features = [
        feature
        for feature in features
        if timestamp_to_nanoseconds(feature.availability_time) <= bar_time_ns
        and (
            earliest_feature_ns is None
            or timestamp_to_nanoseconds(feature.availability_time) >= earliest_feature_ns
        )
    ]
    return any(
        feature.indicator_id == rule.indicator_id
        and (rule.feature_type is None or feature.type == rule.feature_type)
        and feature.name == rule.name
        and feature.value == rule.value
        for feature in available_features
    )


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
    training_sessions, scoring_sessions, step_sessions = _normalized_walk_forward_sessions(config)
    sessions = _dataset_sessions(dataset)

    windows: List[WalkForwardWindow] = []
    start_session_index = 0
    while start_session_index + training_sessions + scoring_sessions <= len(sessions):
        training_start = start_session_index
        training_end = start_session_index + training_sessions
        scoring_start = training_end
        scoring_end = scoring_start + scoring_sessions
        windows.append(
            WalkForwardWindow(
                window_id=f"wf-{len(windows) + 1}",
                training=_window_range(dataset, sessions[training_start:training_end]),
                scoring=_window_range(dataset, sessions[scoring_start:scoring_end]),
            )
        )
        start_session_index += step_sessions

    if not windows:
        raise ValueError("dataset does not contain enough bars for the walk-forward configuration")
    return windows


def _normalized_walk_forward_sessions(config: WalkForwardConfig) -> tuple[int, int, int]:
    has_session_fields = config.training_sessions is not None or config.scoring_sessions is not None
    if not has_session_fields:
        raise ValueError("walk_forward requires training_sessions and scoring_sessions")
    if config.training_sessions is None or config.scoring_sessions is None:
        raise ValueError("walk_forward.training_sessions and walk_forward.scoring_sessions must be provided together")
    training_sessions = config.training_sessions
    scoring_sessions = config.scoring_sessions
    step_sessions = config.step_sessions if config.step_sessions is not None else scoring_sessions

    if training_sessions <= 0:
        raise ValueError("walk_forward.training_sessions must be positive")
    if scoring_sessions <= 0:
        raise ValueError("walk_forward.scoring_sessions must be positive")
    if step_sessions is None or step_sessions <= 0:
        raise ValueError("walk_forward.step_sessions must be positive")
    return training_sessions, scoring_sessions, step_sessions


def _dataset_sessions(dataset: VersionedDataset) -> List[_DatasetSession]:
    declared_sessions = dataset.manifest["session"].get("sessions")
    if isinstance(declared_sessions, list) and declared_sessions:
        sessions: List[_DatasetSession] = []
        for item in declared_sessions:
            first_bar = _parse_manifest_timestamp(str(item["firstBarTime"]))
            last_bar = _parse_manifest_timestamp(str(item["lastBarTime"]))
            start_index = _bar_index_at_time(dataset, first_bar)
            end_index = _bar_index_at_time(dataset, last_bar)
            if start_index is None or end_index is None:
                raise ValueError(f"declared session {item['id']} does not align to dataset bars")
            sessions.append(
                _DatasetSession(
                    session_id=str(item["id"]),
                    session_date=_session_date(dataset, first_bar),
                    start_index=start_index,
                    end_index=end_index,
                )
            )
        sessions.sort(key=lambda session: session.start_index)
        return sessions

    timezone = ZoneInfo(str(dataset.manifest["session"]["timezone"]))
    sessions_by_date: Dict[str, _DatasetSession] = {}
    for index, bar in enumerate(dataset.bars):
        session_date = bar.time.astimezone(timezone).date().isoformat()
        existing = sessions_by_date.get(session_date)
        if existing is None:
            sessions_by_date[session_date] = _DatasetSession(
                session_id=session_date,
                session_date=session_date,
                start_index=index,
                end_index=index,
            )
        else:
            sessions_by_date[session_date] = _DatasetSession(
                session_id=existing.session_id,
                session_date=existing.session_date,
                start_index=existing.start_index,
                end_index=index,
            )
    return sorted(sessions_by_date.values(), key=lambda session: session.start_index)


def _bar_index_at_time(dataset: VersionedDataset, value: datetime) -> Optional[int]:
    target = timestamp_to_nanoseconds(value)
    for index, bar in enumerate(dataset.bars):
        if timestamp_to_nanoseconds(bar.time) == target:
            return index
    return None


def _session_date(dataset: VersionedDataset, value: datetime) -> str:
    timezone = ZoneInfo(str(dataset.manifest["session"]["timezone"]))
    return value.astimezone(timezone).date().isoformat()


def _parse_manifest_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=ZoneInfo("UTC"))
    return parsed.astimezone(ZoneInfo("UTC"))


def _window_range(dataset: VersionedDataset, sessions: List[_DatasetSession]) -> WindowRange:
    start_index = sessions[0].start_index
    end_index = sessions[-1].end_index
    return WindowRange(
        start=dataset.bars[start_index].time.isoformat(),
        end=dataset.bars[end_index].time.isoformat(),
        start_index=start_index,
        end_index=end_index,
        start_session_date=sessions[0].session_date,
        end_session_date=sessions[-1].session_date,
        session_count=len(sessions),
        bar_count=end_index - start_index + 1,
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
    manifest = json.loads(json.dumps(dataset.manifest))
    declared_sessions = manifest["session"].get("sessions")
    if isinstance(declared_sessions, list) and declared_sessions:
        manifest["session"]["sessions"] = [
            session
            for session in declared_sessions
            if scoring.start <= _parse_manifest_timestamp(str(session["firstBarTime"])).isoformat() <= scoring.end
        ]
    return VersionedDataset(
        manifest=manifest,
        bars=bars,
        features=features,
        path=dataset.path,
    )


def _walk_forward_result(
    window_id: str,
    window_range: WindowRange,
    validation: Any,
) -> WalkForwardWindowResult:
    result = validation.result
    return WalkForwardWindowResult(
        window_id=window_id,
        start_session_date=window_range.start_session_date,
        end_session_date=window_range.end_session_date,
        session_count=window_range.session_count,
        bar_count=window_range.bar_count,
        orders=result.orders,
        gross_pnl=result.gross_pnl,
        total_costs=result.total_costs,
        net_pnl=result.net_pnl,
        order_count=len(result.orders),
        trade_count=len(result.orders) // 2,
        max_drawdown=_max_drawdown(_trade_net_pnls(result.orders)),
        result_summary=_result_summary(result),
        nautilus_provenance=validation.nautilus_provenance,
        environment=validation.environment,
        instrument=validation.instrument,
        venue=validation.venue,
        bar_type=validation.bar_type,
        cost_configuration=validation.cost_configuration,
        fills_report=validation.fills_report,
        positions_report=validation.positions_report,
        account_report=validation.account_report,
    )


def _result_summary(result: StrategyBacktestResult) -> Dict[str, Any]:
    return {
        "engine": result.engine,
        "grossPnl": result.gross_pnl,
        "totalCosts": result.total_costs,
        "netPnl": result.net_pnl,
        "positionQuantity": result.position_quantity,
        "orderCount": len(result.orders),
        "tradeCount": len(result.orders) // 2,
    }


def _selection_ranking_inputs(result: WalkForwardWindowResult) -> Dict[str, Any]:
    return {
        "netPnl": result.net_pnl,
        "grossPnl": result.gross_pnl,
        "totalCosts": result.total_costs,
        "tradeCount": result.trade_count,
        "maxDrawdown": result.max_drawdown,
    }


def _select_training_window_candidate(
    candidates: List[WalkForwardCandidateWindowResult],
) -> WalkForwardCandidateWindowResult:
    if not candidates:
        raise ValueError("cannot select from an empty candidate list")
    return sorted(candidates, key=_training_candidate_rank_key, reverse=True)[0]


def _training_candidate_rank_key(candidate: WalkForwardCandidateWindowResult):
    inputs = candidate.selection_ranking_inputs
    return (
        inputs["netPnl"],
        inputs["tradeCount"],
        -inputs["maxDrawdown"],
        candidate.strategy_id,
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
        "recordType": "Evaluator Replay Helper",
        "authoritative": False,
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
    selection_results: Optional[List[WalkForwardWindowSelection]] = None,
) -> Path:
    run_id = _walk_forward_run_id(dataset, spec, cost_model, walk_forward, constraints, selection_results)
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

    selection_results = selection_results or []
    uses_training_selection = bool(selection_results)
    record = {
        "runId": run_id,
        "recordType": "Nautilus Walk-Forward Validation",
        "authoritative": True,
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
        "searchConfiguration": _walk_forward_search_configuration(uses_training_selection),
        "walkForward": {
            "config": _walk_forward_config_to_json(walk_forward),
            "windows": [_walk_forward_window_to_json(window) for window in windows],
        },
        "trainingWindowSelection": [
            _walk_forward_selection_to_json(selection)
            for selection in selection_results
        ],
        "trainingWindowResults": [
            _walk_forward_window_result_to_json(
                result,
                artifacts=_write_walk_forward_window_artifacts(run_path, "training", result),
            )
            for result in training_window_results
        ],
        "perWindowResults": [
            _walk_forward_window_result_to_json(
                result,
                artifacts=_write_walk_forward_window_artifacts(run_path, "scoring", result),
            )
            for result in scoring_window_results
        ],
        "fitness": _fitness_to_json(fitness),
        "finalRankingInputs": fitness.ranking_inputs,
        "artifacts": {
            "ordersByWindow": "orders-by-window.json",
        },
    }
    record_path = run_path / "run.json"
    record_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    return record_path


def _write_walk_forward_window_artifacts(
    run_path: Path,
    phase: str,
    result: WalkForwardWindowResult,
) -> Dict[str, Any]:
    reports_path = run_path / "nautilus-reports"
    reports_path.mkdir(exist_ok=True)
    prefix = f"{phase}-{result.window_id}"
    fills_path = reports_path / f"{prefix}-order-fills.csv"
    positions_path = reports_path / f"{prefix}-positions.csv"
    account_path = reports_path / f"{prefix}-account.csv"

    result.fills_report.to_csv(fills_path)
    result.positions_report.to_csv(positions_path)
    result.account_report.to_csv(account_path)

    return {
        "ordersByWindow": "orders-by-window.json",
        "ordersByWindowKey": f"{phase}:{result.window_id}",
        "nautilusOrderFills": str(fills_path.relative_to(run_path)),
        "nautilusPositions": str(positions_path.relative_to(run_path)),
        "nautilusAccount": str(account_path.relative_to(run_path)),
    }


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
    selection_results: Optional[List[WalkForwardWindowSelection]] = None,
) -> str:
    payload = {
        "datasetId": dataset.dataset_id,
        "strategySpec": spec.raw,
        "costModel": asdict(cost_model),
        "walkForward": asdict(walk_forward),
        "fitnessConstraints": asdict(constraints),
        "selectedCandidates": [
            {
                "windowId": selection.window_id,
                "selectedCandidateId": selection.selected_candidate_id,
                "selectedStrategyId": selection.selected_strategy_id,
            }
            for selection in (selection_results or [])
        ],
        "evaluatorVersion": EVALUATOR_VERSION,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return f"{spec.strategy_id}-walk-forward-{digest[:12]}"


def _walk_forward_search_configuration(uses_training_selection: bool) -> Dict[str, Any]:
    if uses_training_selection:
        return {
            "type": "training-window-candidate-selection",
            "description": "Each split selects the Strategy Spec using only the training window, then scores the selected spec on the later out-of-sample window.",
        }
    return {
        "type": "fixed-strategy-spec-diagnostic",
        "description": "No optimizer is run in this diagnostic slice; the same fixed Strategy Spec is validated separately on training and scoring windows.",
    }


def _cost_model_to_json(cost_model: CostModel) -> Dict[str, Any]:
    return {
        "fixedFee": cost_model.fixed_fee,
        "slippageTicks": cost_model.slippage_ticks,
        "tickSize": cost_model.tick_size,
    }


def _walk_forward_config_to_json(config: WalkForwardConfig) -> Dict[str, Any]:
    training_sessions, scoring_sessions, step_sessions = _normalized_walk_forward_sessions(config)
    return {
        "trainingSessions": training_sessions,
        "scoringSessions": scoring_sessions,
        "stepSessions": step_sessions,
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
        "startSessionDate": window_range.start_session_date,
        "endSessionDate": window_range.end_session_date,
        "sessionCount": window_range.session_count,
        "barCount": window_range.bar_count,
    }


def _walk_forward_window_result_to_json(
    result: WalkForwardWindowResult,
    *,
    artifacts: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "windowId": result.window_id,
        "startSessionDate": result.start_session_date,
        "endSessionDate": result.end_session_date,
        "sessionCount": result.session_count,
        "barCount": result.bar_count,
        "grossPnl": result.gross_pnl,
        "totalCosts": result.total_costs,
        "netPnl": result.net_pnl,
        "orderCount": result.order_count,
        "tradeCount": result.trade_count,
        "maxDrawdown": result.max_drawdown,
        "resultSummary": result.result_summary,
        "nautilusTrader": result.nautilus_provenance,
        "environment": result.environment,
        "instrument": result.instrument,
        "venue": result.venue,
        "barType": result.bar_type,
        "costConfiguration": result.cost_configuration,
        "artifacts": artifacts,
    }


def _walk_forward_selection_to_json(selection: WalkForwardWindowSelection) -> Dict[str, Any]:
    return {
        "windowId": selection.window_id,
        "trainingWindow": _window_range_to_json(selection.training),
        "scoringWindow": _window_range_to_json(selection.scoring),
        "selectionInputs": [
            _walk_forward_candidate_result_to_json(candidate)
            for candidate in selection.candidate_results
        ],
        "selectedCandidate": {
            "candidateId": selection.selected_candidate_id,
            "strategyId": selection.selected_strategy_id,
            "strategySpec": selection.selected_strategy_spec,
            "trainingResult": _walk_forward_window_result_to_json(
                selection.selected_training_result,
                artifacts={},
            ),
            "selectionRankingInputs": selection.selection_ranking_inputs,
        },
        "scoringResultWindowId": selection.window_id,
    }


def _walk_forward_candidate_result_to_json(
    candidate: WalkForwardCandidateWindowResult,
) -> Dict[str, Any]:
    return {
        "candidateId": candidate.candidate_id,
        "strategyId": candidate.strategy_id,
        "strategySpec": candidate.strategy_spec,
        "trainingResult": _walk_forward_window_result_to_json(
            candidate.training_result,
            artifacts={},
        ),
        "selectionRankingInputs": candidate.selection_ranking_inputs,
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
