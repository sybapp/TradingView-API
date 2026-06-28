"""Real NautilusTrader validation path for fixture Strategy Specs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sys
from typing import Any, Dict, List, Mapping, Optional, Union
from zoneinfo import ZoneInfo

from .dataset import DatasetBar, FeatureRecord, VersionedDataset, load_versioned_dataset
from .nautilus import (
    concrete_bar_type_string,
    timestamp_to_nanoseconds,
    to_nautilus_bars_for_instrument,
)
from .strategy import CostModel, StrategyBacktestResult, StrategyOrder
from .strategy_spec import FeatureEqualsEntryRule, StrategySpec, validate_strategy_spec


NAUTILUS_VALIDATION_VERSION = "nautilus-validation-v1"


class _PendingSignal:
    def __init__(self, *, side: str, quantity: int, reason: str, signal_bar_ns: int):
        self.side = side
        self.quantity = quantity
        self.reason = reason
        self.signal_bar_ns = signal_bar_ns


@dataclass(frozen=True)
class _SessionWindow:
    session_id: str
    first_bar_ns: int
    last_bar_ns: int
    flat_at_ns: int


@dataclass(frozen=True)
class _OpenPosition:
    quantity: int
    entry_bar_ns: int
    entry_bar_index: int
    entry_price: float


def run_nautilus_validation_backtest(
    *,
    dataset_path: Union[str, Path],
    strategy_spec: Mapping[str, Any],
    cost_model: CostModel,
    registry_path: Union[str, Path],
) -> StrategyBacktestResult:
    dataset = load_versioned_dataset(dataset_path)
    spec = validate_strategy_spec(strategy_spec)
    _validate_cost_model(cost_model)

    nautilus = _load_nautilus_runtime()
    instrument = nautilus["TestInstrumentProvider"].es_future(2026, 9)
    bar_type_string = concrete_bar_type_string(dataset, instrument.id.value)
    bar_type = nautilus["BarType"].from_str(bar_type_string)
    bars = to_nautilus_bars_for_instrument(dataset, instrument=instrument, bar_type=bar_type)
    if len(bars) < 2:
        raise ValueError("dataset must contain at least two bars for Nautilus validation")

    _validate_instrument_cost_mapping(cost_model, instrument)
    engine = nautilus["BacktestEngine"](
        config=nautilus["BacktestEngineConfig"](
            logging=nautilus["LoggingConfig"](log_level="ERROR", bypass_logging=True),
            run_analysis=False,
        ),
    )
    venue = nautilus["Venue"](instrument.id.venue.value)
    fill_model = _nautilus_fill_model(cost_model, nautilus)
    fee_model = nautilus["PerContractFeeModel"](nautilus["Money"](cost_model.fixed_fee, nautilus["USD"]))
    engine.add_venue(
        venue=venue,
        oms_type=nautilus["OmsType"].NETTING,
        account_type=nautilus["AccountType"].MARGIN,
        starting_balances=[nautilus["Money"](1_000_000, nautilus["USD"])],
        base_currency=nautilus["USD"],
        default_leverage=Decimal(1),
        fill_model=fill_model,
        fee_model=fee_model,
        bar_execution=True,
        trade_execution=False,
    )
    engine.add_instrument(instrument)
    engine.add_data(bars)

    strategy = _StrategySpecNautilusAdapter(
        nautilus=nautilus,
        dataset=dataset,
        spec=spec,
        instrument_id=instrument.id,
        bar_type=bar_type,
        tick_size=cost_model.tick_size,
    )
    engine.add_strategy(strategy)
    try:
        engine.run()
        fills_report = engine.trader.generate_order_fills_report()
        orders = _orders_from_fills(
            fills_report=fills_report,
            submitted_orders=strategy.submitted_orders,
            price_scale=dataset.price_scale,
        )
        positions_report = engine.trader.generate_positions_report()
        account_report = engine.trader.generate_account_report(venue)
        result = _result_from_orders(dataset, spec, orders)
        record_path = _write_nautilus_validation_record(
            registry_path=Path(registry_path),
            dataset=dataset,
            spec=spec,
            cost_model=cost_model,
            result=result,
            instrument=instrument,
            venue=venue,
            bar_type_string=bar_type_string,
            nautilus=nautilus,
            fills_report=fills_report,
            positions_report=positions_report,
            account_report=account_report,
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
    finally:
        engine.dispose()


class _StrategySpecNautilusAdapter:
    def __new__(cls, *args, **kwargs):
        nautilus = kwargs["nautilus"]
        strategy_base = nautilus["Strategy"]

        class StrategySpecNautilusAdapter(strategy_base):
            def __init__(
                self,
                *,
                dataset: VersionedDataset,
                spec: StrategySpec,
                instrument_id: Any,
                bar_type: Any,
                tick_size: float,
            ):
                super().__init__()
                self.dataset = dataset
                self.spec = spec
                self.instrument_id = instrument_id
                self.bar_type = bar_type
                self.tick_size = tick_size
                self.pending_signal: Optional[_PendingSignal] = None
                self.submitted_orders: Dict[str, Dict[str, Any]] = {}
                self.open_position: Optional[_OpenPosition] = None
                self.entry_taken = False
                self.bars_by_ns = {timestamp_to_nanoseconds(item.time): item for item in dataset.bars}
                self.bar_indices_by_ns = {
                    timestamp_to_nanoseconds(item.time): index for index, item in enumerate(dataset.bars)
                }
                self.session_windows = _session_windows(dataset, spec.risk_controls.flat_before_close_minutes)

            def on_start(self):
                self.subscribe_bars(self.bar_type)

            def on_bar(self, bar):
                if self.pending_signal is not None:
                    self._submit_pending_signal(bar)

                exit_reason = self._exit_reason(bar)
                if self.open_position is not None and exit_reason is not None:
                    self._submit_order(
                        side="sell",
                        quantity=self.open_position.quantity,
                        reason=exit_reason,
                        signal_bar_ns=bar.ts_event,
                        execution_bar_ns=bar.ts_event,
                    )
                    return

                if self.pending_signal is not None or self.open_position is not None or self.entry_taken:
                    return

                if not self._next_bar_can_execute_before_flat(bar.ts_event):
                    return

                session = self._session_for_bar(bar.ts_event)
                if session is None:
                    return
                matched_rule = _matching_entry_rule(
                    self.spec.entry_rules,
                    self.dataset.features,
                    bar.ts_event,
                    earliest_feature_ns=session.first_bar_ns,
                )
                if matched_rule is not None:
                    self.pending_signal = _PendingSignal(
                        side="buy",
                        quantity=self.spec.sizing.quantity,
                        reason="entry:feature_equals",
                        signal_bar_ns=bar.ts_event,
                    )

            def _submit_pending_signal(self, bar):
                pending = self.pending_signal
                self.pending_signal = None
                self._submit_order(
                    side=pending.side,
                    quantity=pending.quantity,
                    reason=pending.reason,
                    signal_bar_ns=pending.signal_bar_ns,
                    execution_bar_ns=bar.ts_event,
                )

            def _submit_order(
                self,
                *,
                side: str,
                quantity: int,
                reason: str,
                signal_bar_ns: int,
                execution_bar_ns: int,
            ):
                order_side = nautilus["OrderSide"].BUY if side == "buy" else nautilus["OrderSide"].SELL
                instrument = self.cache.instrument(self.instrument_id)
                order = self.order_factory.market(
                    self.instrument_id,
                    order_side,
                    instrument.make_qty(Decimal(quantity)),
                )
                self.submitted_orders[order.client_order_id.value] = {
                    "side": side,
                    "quantity": quantity,
                    "reason": reason,
                    "signalBarNs": signal_bar_ns,
                }
                self.submit_order(order)
                if side == "buy":
                    self.entry_taken = True
                    self.open_position = _OpenPosition(
                        quantity=quantity,
                        entry_bar_ns=execution_bar_ns,
                        entry_bar_index=self.bar_indices_by_ns[execution_bar_ns],
                        entry_price=self.bars_by_ns[execution_bar_ns].open,
                    )
                else:
                    self.open_position = None

            def _next_bar_can_execute_before_flat(self, current_bar_ns: int) -> bool:
                next_bar = self._next_dataset_bar(current_bar_ns)
                if next_bar is None:
                    return False
                current_session = self._session_for_bar(current_bar_ns)
                next_session = self._session_for_bar(timestamp_to_nanoseconds(next_bar.time))
                return (
                    current_session is not None
                    and next_session is not None
                    and current_session.session_id == next_session.session_id
                    and timestamp_to_nanoseconds(next_bar.time) < current_session.flat_at_ns
                )

            def _exit_reason(self, bar) -> Optional[str]:
                position = self.open_position
                if position is None:
                    return None

                session = self._session_for_bar(bar.ts_event)
                if session is not None and bar.ts_event >= session.flat_at_ns:
                    return "intraday-flat-before-close"

                dataset_bar = self.bars_by_ns[bar.ts_event]
                stop_price = position.entry_price - (
                    self.spec.risk_controls.stop_loss_ticks * self.tick_size
                )
                if dataset_bar.low <= stop_price:
                    return "stop-loss"

                take_profit_price = position.entry_price + (
                    self.spec.risk_controls.take_profit_ticks * self.tick_size
                )
                if dataset_bar.high >= take_profit_price:
                    return "take-profit"

                max_bars = self.spec.exits.max_bars_in_trade
                if max_bars is not None:
                    current_index = self.bar_indices_by_ns[bar.ts_event]
                    if current_index - position.entry_bar_index >= max_bars:
                        return "max-bars-in-trade"

                return None

            def _next_dataset_bar(self, current_bar_ns: int) -> Optional[DatasetBar]:
                current_index = self.bar_indices_by_ns.get(current_bar_ns)
                if current_index is None or current_index + 1 >= len(self.dataset.bars):
                    return None
                return self.dataset.bars[current_index + 1]

            def _session_for_bar(self, bar_ns: int) -> Optional[_SessionWindow]:
                for session in self.session_windows:
                    if session.first_bar_ns <= bar_ns <= session.last_bar_ns:
                        return session
                return None

        return StrategySpecNautilusAdapter(
            dataset=kwargs["dataset"],
            spec=kwargs["spec"],
            instrument_id=kwargs["instrument_id"],
            bar_type=kwargs["bar_type"],
            tick_size=kwargs["tick_size"],
        )


def _load_nautilus_runtime() -> Dict[str, Any]:
    try:
        import nautilus_trader
        from nautilus_trader.backtest.engine import BacktestEngine
        from nautilus_trader.backtest.models import FillModel, OneTickSlippageFillModel, PerContractFeeModel
        from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
        from nautilus_trader.model.currencies import USD
        from nautilus_trader.model.data import BarType
        from nautilus_trader.model.enums import AccountType, OmsType, OrderSide
        from nautilus_trader.model.identifiers import Venue
        from nautilus_trader.model.objects import Money
        from nautilus_trader.test_kit.providers import TestInstrumentProvider
        from nautilus_trader.trading.strategy import Strategy
    except ModuleNotFoundError as exc:
        raise RuntimeError("nautilus_trader is required for Nautilus Validation") from exc

    return {
        "AccountType": AccountType,
        "BacktestEngine": BacktestEngine,
        "BacktestEngineConfig": BacktestEngineConfig,
        "BarType": BarType,
        "FillModel": FillModel,
        "LoggingConfig": LoggingConfig,
        "Money": Money,
        "OmsType": OmsType,
        "OneTickSlippageFillModel": OneTickSlippageFillModel,
        "OrderSide": OrderSide,
        "PerContractFeeModel": PerContractFeeModel,
        "Strategy": Strategy,
        "TestInstrumentProvider": TestInstrumentProvider,
        "USD": USD,
        "Venue": Venue,
        "nautilus_trader": nautilus_trader,
    }


def _validate_cost_model(cost_model: CostModel) -> None:
    if cost_model.fixed_fee < 0:
        raise ValueError("Nautilus Validation supports only non-negative fixed_fee cost models")
    if cost_model.slippage_ticks not in (0, 1):
        raise ValueError("Nautilus Validation supports only 0 or 1 slippage tick")
    if cost_model.tick_size <= 0:
        raise ValueError("Nautilus Validation requires a positive tick_size")


def _validate_instrument_cost_mapping(cost_model: CostModel, instrument: Any) -> None:
    instrument_tick = Decimal(str(instrument.price_increment))
    requested_tick = Decimal(str(cost_model.tick_size))
    if requested_tick != instrument_tick:
        raise ValueError(
            "Nautilus Validation cost_model.tick_size must match the Nautilus instrument price_increment"
        )


def _nautilus_fill_model(cost_model: CostModel, nautilus: Mapping[str, Any]) -> Any:
    if cost_model.slippage_ticks == 0:
        return nautilus["FillModel"](prob_slippage=0.0, random_seed=0)
    return nautilus["OneTickSlippageFillModel"]()


def _matching_entry_rule(
    rules: List[FeatureEqualsEntryRule],
    features: List[FeatureRecord],
    bar_time_ns: int,
    *,
    earliest_feature_ns: Optional[int] = None,
) -> Optional[FeatureEqualsEntryRule]:
    available_features = [
        feature
        for feature in features
        if timestamp_to_nanoseconds(feature.availability_time) <= bar_time_ns
        and (
            earliest_feature_ns is None
            or timestamp_to_nanoseconds(feature.availability_time) >= earliest_feature_ns
        )
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


def _session_windows(dataset: VersionedDataset, flat_before_close_minutes: int) -> List[_SessionWindow]:
    declared_sessions = dataset.manifest["session"].get("sessions")
    if isinstance(declared_sessions, list) and declared_sessions:
        return [
            _SessionWindow(
                session_id=str(item["id"]),
                first_bar_ns=timestamp_to_nanoseconds(_parse_manifest_timestamp(str(item["firstBarTime"]))),
                last_bar_ns=timestamp_to_nanoseconds(_parse_manifest_timestamp(str(item["lastBarTime"]))),
                flat_at_ns=_flat_at_for_session_date(
                    dataset,
                    _parse_manifest_timestamp(str(item["firstBarTime"])),
                    flat_before_close_minutes,
                ),
            )
            for item in declared_sessions
        ]

    timezone = ZoneInfo(str(dataset.manifest["session"]["timezone"]))
    windows: List[_SessionWindow] = []
    bars_by_date: Dict[str, List[DatasetBar]] = {}
    for bar in dataset.bars:
        session_id = bar.time.astimezone(timezone).date().isoformat()
        bars_by_date.setdefault(session_id, []).append(bar)
    for session_id, bars in bars_by_date.items():
        windows.append(
            _SessionWindow(
                session_id=session_id,
                first_bar_ns=timestamp_to_nanoseconds(bars[0].time),
                last_bar_ns=timestamp_to_nanoseconds(bars[-1].time),
                flat_at_ns=_flat_at_for_session_date(dataset, bars[0].time, flat_before_close_minutes),
            )
        )
    return windows


def _flat_at_for_session_date(
    dataset: VersionedDataset,
    session_time: datetime,
    flat_before_close_minutes: int,
) -> int:
    session = dataset.manifest["session"]
    timezone = ZoneInfo(str(session["timezone"]))
    session_end = time.fromisoformat(str(session["end"]))
    session_date = session_time.astimezone(timezone).date()
    session_close = datetime.combine(session_date, session_end, tzinfo=timezone)
    flat_at = session_close - timedelta(minutes=flat_before_close_minutes)
    return timestamp_to_nanoseconds(flat_at)


def _parse_manifest_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=ZoneInfo("UTC"))
    return parsed.astimezone(ZoneInfo("UTC"))


def _orders_from_fills(
    *,
    fills_report: Any,
    submitted_orders: Mapping[str, Mapping[str, Any]],
    price_scale: int,
) -> List[StrategyOrder]:
    orders: List[StrategyOrder] = []
    if "ts_last" not in fills_report.columns:
        return orders
    for client_order_id, row in fills_report.sort_values("ts_last").iterrows():
        submitted = submitted_orders[str(client_order_id)]
        commission = _commission_to_scaled_int(row["commissions"], price_scale)
        market_price = _price_to_scaled_int(row["avg_px"], price_scale)
        orders.append(
            StrategyOrder(
                side=submitted["side"],
                quantity=int(submitted["quantity"]),
                reason=str(submitted["reason"]),
                signal_bar_time=_datetime_from_nanoseconds(int(submitted["signalBarNs"])),
                execution_bar_time=row["ts_last"].to_pydatetime(),
                market_price=market_price,
                execution_price=market_price,
                fixed_fee=commission,
                slippage_cost=0,
            )
        )
    return orders


def _result_from_orders(
    dataset: VersionedDataset,
    spec: StrategySpec,
    orders: List[StrategyOrder],
) -> StrategyBacktestResult:
    gross_pnl = _gross_pnl(orders)
    total_costs = sum(order.fixed_fee + order.slippage_cost for order in orders)
    return StrategyBacktestResult(
        dataset_id=dataset.dataset_id,
        strategy_id=spec.strategy_id,
        engine="nautilus-trader-backtest-engine",
        orders=orders,
        position_quantity=_position_quantity(orders),
        gross_pnl=gross_pnl,
        total_costs=total_costs,
        net_pnl=gross_pnl - total_costs,
        registry_record_path=Path(),
    )


def _write_nautilus_validation_record(
    *,
    registry_path: Path,
    dataset: VersionedDataset,
    spec: StrategySpec,
    cost_model: CostModel,
    result: StrategyBacktestResult,
    instrument: Any,
    venue: Any,
    bar_type_string: str,
    nautilus: Mapping[str, Any],
    fills_report: Any,
    positions_report: Any,
    account_report: Any,
) -> Path:
    run_id = _nautilus_run_id(dataset, spec, cost_model, bar_type_string)
    run_path = registry_path / run_id
    run_path.mkdir(parents=True, exist_ok=True)

    _write_json(run_path / "orders.json", [_order_to_json(order) for order in result.orders])
    fills_report.to_csv(run_path / "nautilus-order-fills.csv")
    positions_report.to_csv(run_path / "nautilus-positions.csv")
    account_report.to_csv(run_path / "nautilus-account.csv")

    record = {
        "runId": run_id,
        "recordType": "Nautilus Validation",
        "evaluatorVersion": NAUTILUS_VALIDATION_VERSION,
        "nautilusTrader": _nautilus_provenance(nautilus),
        "environment": _environment_details(),
        "dataset": {
            "datasetId": dataset.dataset_id,
            "path": str(dataset.path),
            "schemaVersion": dataset.manifest["schemaVersion"],
            "collectedAt": dataset.manifest["collectedAt"],
            "source": dataset.manifest["source"],
            "symbol": dataset.manifest["symbol"],
            "bar": dataset.manifest["bar"],
            "session": dataset.manifest["session"],
            "identityHash": _json_hash(dataset.manifest),
        },
        "strategySpec": spec.raw,
        "strategySpecIdentity": {
            "strategyId": spec.strategy_id,
            "identityHash": _json_hash(spec.raw),
        },
        "instrument": {
            "instrumentId": instrument.id.value,
            "rawSymbol": str(instrument.raw_symbol),
            "provider": "nautilus_trader.test_kit.providers.TestInstrumentProvider.es_future",
            "expiryYear": 2026,
            "expiryMonth": 9,
            "priceIncrement": str(instrument.price_increment),
            "pricePrecision": int(instrument.price_precision),
        },
        "venue": {
            "name": venue.value,
            "omsType": "NETTING",
            "accountType": "MARGIN",
            "baseCurrency": "USD",
            "startingBalances": ["1000000 USD"],
        },
        "barType": {
            "value": bar_type_string,
            "aggregation": dataset.interval,
            "priceType": "LAST",
            "source": "EXTERNAL",
        },
        "costConfiguration": _cost_configuration(cost_model),
        "results": {
            "grossPnl": result.gross_pnl,
            "totalCosts": result.total_costs,
            "netPnl": result.net_pnl,
            "positionQuantity": result.position_quantity,
            "orderCount": len(result.orders),
        },
        "artifacts": {
            "orders": "orders.json",
            "nautilusOrderFills": "nautilus-order-fills.csv",
            "nautilusPositions": "nautilus-positions.csv",
            "nautilusAccount": "nautilus-account.csv",
        },
    }
    record_path = run_path / "run.json"
    _write_json(record_path, record)
    return record_path


def _nautilus_provenance(nautilus: Mapping[str, Any]) -> Dict[str, Any]:
    package = nautilus["nautilus_trader"]
    return {
        "package": "nautilus_trader",
        "version": importlib.metadata.version("nautilus-trader"),
        "moduleFile": str(Path(package.__file__).resolve()),
        "engine": "BacktestEngine",
        "runtimeImportFromThirdPartyReference": "third_party/nautilus_trader" in str(Path(package.__file__).resolve()),
    }


def _environment_details() -> Dict[str, Any]:
    return {
        "pythonVersion": sys.version.split()[0],
        "pythonExecutable": sys.executable,
        "platform": platform.platform(),
        "cwd": os.getcwd(),
        "argv": sys.argv,
    }


def _cost_configuration(cost_model: CostModel) -> Dict[str, Any]:
    return {
        "requested": asdict(cost_model),
        "nautilusExecution": {
            "feeModel": {
                "class": "PerContractFeeModel",
                "commission": f"{cost_model.fixed_fee} USD",
            },
            "fillModel": (
                {"class": "FillModel", "probSlippage": 0.0, "randomSeed": 0}
                if cost_model.slippage_ticks == 0
                else {"class": "OneTickSlippageFillModel", "slippageTicks": 1}
            ),
        },
    }


def _nautilus_run_id(
    dataset: VersionedDataset,
    spec: StrategySpec,
    cost_model: CostModel,
    bar_type_string: str,
) -> str:
    payload = {
        "datasetId": dataset.dataset_id,
        "strategySpec": spec.raw,
        "costModel": asdict(cost_model),
        "barType": bar_type_string,
        "evaluatorVersion": NAUTILUS_VALIDATION_VERSION,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return f"{spec.strategy_id}-nautilus-{digest[:12]}"


def _gross_pnl(orders: List[StrategyOrder]) -> int:
    if len(orders) % 2 != 0:
        raise ValueError("Nautilus Validation ended with unpaired entry/exit orders")
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


def _commission_to_scaled_int(value: Any, price_scale: int) -> int:
    if isinstance(value, list):
        return sum(_commission_to_scaled_int(item, price_scale) for item in value)
    text = str(value)
    amount = text.split(" ", 1)[0].strip("[]")
    return round(float(amount) * price_scale)


def _price_to_scaled_int(value: Any, price_scale: int) -> int:
    return round(float(value) * price_scale)


def _datetime_from_nanoseconds(value: int) -> datetime:
    seconds, nanoseconds = divmod(value, 1_000_000_000)
    return datetime.fromtimestamp(seconds, tz=ZoneInfo("UTC")).replace(microsecond=nanoseconds // 1000)


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


def _json_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
