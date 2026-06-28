"""Python-side evaluator boundary for TradingView versioned datasets."""

from .dataset import (
    DatasetBar,
    FeatureRecord,
    VersionedDataset,
    load_versioned_dataset,
)
from .nautilus import NautilusBarInput, to_nautilus_bar_inputs, to_nautilus_bars
from .smoke import SmokeBacktestResult, run_smoke_backtest
from .strategy import CostModel, StrategyBacktestResult, StrategyOrder, run_strategy_backtest
from .strategy_spec import StrategySpec, validate_strategy_spec

__all__ = [
    "CostModel",
    "DatasetBar",
    "FeatureRecord",
    "NautilusBarInput",
    "SmokeBacktestResult",
    "StrategyBacktestResult",
    "StrategyOrder",
    "StrategySpec",
    "VersionedDataset",
    "load_versioned_dataset",
    "run_smoke_backtest",
    "run_strategy_backtest",
    "to_nautilus_bar_inputs",
    "to_nautilus_bars",
    "validate_strategy_spec",
]
