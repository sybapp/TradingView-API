"""Smoke replay path for the first Nautilus evaluator slice."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Union

from .dataset import FeatureRecord, load_versioned_dataset
from .nautilus import (
    NautilusBarInput,
    replay_bar_inputs,
    timestamp_to_nanoseconds,
    to_nautilus_bar_inputs,
)


@dataclass(frozen=True)
class SmokeBacktestResult:
    dataset_id: str
    replayed_bars: List[NautilusBarInput]
    available_features: List[FeatureRecord]
    final_close: int
    engine: str


def run_smoke_backtest(dataset_path: Union[str, Path]) -> SmokeBacktestResult:
    dataset = load_versioned_dataset(dataset_path)
    replayed_bars = replay_bar_inputs(to_nautilus_bar_inputs(dataset))
    if not replayed_bars:
        raise ValueError("dataset must contain at least one bar for smoke replay")

    final_timestamp = replayed_bars[-1].ts_event
    available_features = [
        feature
        for feature in dataset.features
        if timestamp_to_nanoseconds(feature.availability_time) <= final_timestamp
    ]

    return SmokeBacktestResult(
        dataset_id=dataset.dataset_id,
        replayed_bars=replayed_bars,
        available_features=available_features,
        final_close=replayed_bars[-1].close,
        engine="nautilus-compatible-replay",
    )
