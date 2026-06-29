"""Python-side evaluator boundary for TradingView versioned datasets."""

from .dataset import (
    DatasetBar,
    FeatureRecord,
    VersionedDataset,
    load_versioned_dataset,
)
from .nautilus import (
    NautilusBarInput,
    concrete_bar_type_string,
    to_nautilus_bar_inputs,
    to_nautilus_bars,
    to_nautilus_bars_for_instrument,
)
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
    WalkForwardCandidateWindowResult,
    WalkForwardConfig,
    WalkForwardWindowSelection,
    WalkForwardWindow,
    WalkForwardWindowResult,
    WindowRange,
    run_walk_forward_candidate_selection_backtest,
    run_strategy_backtest,
    run_walk_forward_backtest,
)
from .strategy_spec import StrategySpec, validate_strategy_spec
from .validation import NAUTILUS_VALIDATION_VERSION, run_nautilus_validation_backtest

__all__ = [
    "BoundedSearchConfig",
    "CostModel",
    "DatasetBar",
    "EvaluatedSearchCandidate",
    "FeatureRecord",
    "FitnessConstraints",
    "FitnessResult",
    "NautilusBarInput",
    "NAUTILUS_VALIDATION_VERSION",
    "SmokeBacktestResult",
    "StrategyBacktestResult",
    "StrategyOrder",
    "StrategySearchResult",
    "StrategySpec",
    "StrategyTemplate",
    "VersionedDataset",
    "WalkForwardBacktestResult",
    "WalkForwardCandidateWindowResult",
    "WalkForwardConfig",
    "WalkForwardWindow",
    "WalkForwardWindowSelection",
    "WalkForwardWindowResult",
    "WindowRange",
    "concrete_bar_type_string",
    "generate_bounded_template_specs",
    "load_versioned_dataset",
    "reproduce_search_winner",
    "run_bounded_strategy_search",
    "run_nautilus_validation_backtest",
    "run_smoke_backtest",
    "run_strategy_backtest",
    "run_walk_forward_candidate_selection_backtest",
    "run_walk_forward_backtest",
    "to_nautilus_bar_inputs",
    "to_nautilus_bars",
    "to_nautilus_bars_for_instrument",
    "validate_strategy_spec",
]
