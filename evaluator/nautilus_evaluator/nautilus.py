"""NautilusTrader-compatible conversion helpers.

The first evaluator slice keeps the dependency boundary explicit: it converts
TradingView bars into the timestamped, price-scaled primitives a Nautilus data
adapter can consume, without requiring a live TradingView session.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Iterable, List

from .dataset import DatasetBar, VersionedDataset


@dataclass(frozen=True)
class NautilusBarInput:
    instrument_id: str
    bar_type: str
    ts_event: int
    ts_init: int
    open: int
    high: int
    low: int
    close: int
    volume: int


def to_nautilus_bar_inputs(dataset: VersionedDataset) -> List[NautilusBarInput]:
    return [
        to_nautilus_bar_input(
            bar=bar,
            instrument_id=dataset.ticker,
            interval=dataset.interval,
            price_scale=dataset.price_scale,
        )
        for bar in dataset.bars
    ]


def to_nautilus_bars(dataset: VersionedDataset) -> List[Any]:
    """Construct real NautilusTrader Bar objects when the dependency is installed."""
    try:
        from nautilus_trader.model.data import Bar, BarType
    except ModuleNotFoundError as exc:
        raise RuntimeError("nautilus_trader is required to construct Nautilus Bar objects") from exc

    precision = _price_precision(dataset.price_scale)
    return [
        Bar.from_raw(
            bar_type=BarType.from_str(bar.bar_type),
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            price_prec=precision,
            volume=bar.volume,
            size_prec=0,
            ts_event=bar.ts_event,
            ts_init=bar.ts_init,
        )
        for bar in to_nautilus_bar_inputs(dataset)
    ]


def to_nautilus_bar_input(
    *,
    bar: DatasetBar,
    instrument_id: str,
    interval: str,
    price_scale: int,
) -> NautilusBarInput:
    ts_event = timestamp_to_nanoseconds(bar.time)
    return NautilusBarInput(
        instrument_id=instrument_id,
        bar_type=f"{_nautilus_instrument_id(instrument_id)}-{_nautilus_bar_step(interval)}-LAST-EXTERNAL",
        ts_event=ts_event,
        ts_init=ts_event,
        open=_scaled_price(bar.open, price_scale),
        high=_scaled_price(bar.high, price_scale),
        low=_scaled_price(bar.low, price_scale),
        close=_scaled_price(bar.close, price_scale),
        volume=bar.volume,
    )


def _nautilus_instrument_id(value: str) -> str:
    if ":" not in value:
        return value
    venue, symbol = value.split(":", 1)
    return f"{symbol}.{venue}"


def _nautilus_bar_step(interval: str) -> str:
    if interval.endswith("m") and interval[:-1].isdigit():
        return f"{interval[:-1]}-MINUTE"
    if interval.endswith("h") and interval[:-1].isdigit():
        return f"{interval[:-1]}-HOUR"
    return interval


def replay_bar_inputs(bars: Iterable[NautilusBarInput]) -> List[NautilusBarInput]:
    return sorted(bars, key=lambda bar: bar.ts_event)


def timestamp_to_nanoseconds(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    delta = value.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (
        delta.days * 86_400 * 1_000_000_000
        + delta.seconds * 1_000_000_000
        + delta.microseconds * 1_000
    )


def _scaled_price(value: float, price_scale: int) -> int:
    scaled = Decimal(str(value)) * Decimal(price_scale)
    return int(scaled.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _price_precision(price_scale: int) -> int:
    scale = price_scale
    precision = 0
    while scale > 1 and scale % 10 == 0:
        scale = scale // 10
        precision += 1
    return precision
