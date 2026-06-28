"""Python-side evaluator boundary for TradingView versioned datasets."""

from .dataset import (
    DatasetBar,
    FeatureRecord,
    VersionedDataset,
    load_versioned_dataset,
)
from .nautilus import NautilusBarInput, to_nautilus_bar_inputs, to_nautilus_bars
from .smoke import SmokeBacktestResult, run_smoke_backtest
from .search import (
    BoundedSearchConfig,
    EvaluatedSearchCandidate,
    StrategySearchResult,
    StrategyTemplate,
    generate_bounded_template_specs,
    reproduce_search_winner,
    run_bounded_strategy_search,
)
from .strategy import (
    CostModel,
    FitnessConstraints,
    FitnessResult,
    StrategyBacktestResult,
    StrategyOrder,
    WalkForwardBacktestResult,
    WalkForwardConfig,
    WalkForwardWindow,
    WalkForwardWindowResult,
    WindowRange,
    run_strategy_backtest,
    run_walk_forward_backtest,
)
from .strategy_spec import StrategySpec, validate_strategy_spec

__all__ = [
    "BoundedSearchConfig",
    "CostModel",
    "DatasetBar",
    "EvaluatedSearchCandidate",
    "FeatureRecord",
    "FitnessConstraints",
    "FitnessResult",
    "NautilusBarInput",
    "SmokeBacktestResult",
    "StrategyBacktestResult",
    "StrategyOrder",
    "StrategySearchResult",
    "StrategySpec",
    "StrategyTemplate",
    "VersionedDataset",
    "WalkForwardBacktestResult",
    "WalkForwardConfig",
    "WalkForwardWindow",
    "WalkForwardWindowResult",
    "WindowRange",
    "generate_bounded_template_specs",
    "load_versioned_dataset",
    "reproduce_search_winner",
    "run_bounded_strategy_search",
    "run_smoke_backtest",
    "run_strategy_backtest",
    "run_walk_forward_backtest",
    "to_nautilus_bar_inputs",
    "to_nautilus_bars",
    "validate_strategy_spec",
]
