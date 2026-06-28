"""CLI entry point for replaying a Versioned Dataset."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .nautilus_evaluator import (
    CostModel,
    FitnessConstraints,
    WalkForwardConfig,
    run_smoke_backtest,
    run_strategy_backtest,
    run_walk_forward_backtest,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay a TradingView versioned dataset.")
    parser.add_argument("dataset_path", help="Path to a Versioned Dataset directory")
    parser.add_argument("--strategy-spec", help="Path to a Strategy Spec JSON file")
    parser.add_argument("--run-registry", default="runs", help="Directory for strategy run records")
    parser.add_argument("--fixed-fee", type=float, default=2.50, help="Fixed fee per order")
    parser.add_argument("--slippage-ticks", type=int, default=1, help="Slippage ticks per order")
    parser.add_argument("--tick-size", type=float, default=0.25, help="Instrument tick size")
    parser.add_argument("--walk-forward-training-bars", type=int, help="Bars in each walk-forward training window")
    parser.add_argument("--walk-forward-scoring-bars", type=int, help="Bars in each walk-forward scoring window")
    parser.add_argument("--walk-forward-step-bars", type=int, help="Bars to advance between walk-forward windows")
    parser.add_argument("--min-trades", type=int, default=10, help="Minimum trades required to survive fitness checks")
    parser.add_argument("--max-drawdown", type=int, help="Maximum drawdown allowed by fitness checks")
    parser.add_argument("--max-cost-to-gross-ratio", type=float, help="Maximum total cost to absolute gross PnL ratio")
    parser.add_argument("--max-slippage-costs", type=int, help="Maximum total slippage costs allowed by fitness checks")
    parser.add_argument("--min-profitable-windows", type=int, default=1, help="Minimum profitable scoring windows required")
    parser.add_argument("--min-profitable-window-ratio", type=float, help="Minimum ratio of profitable scoring windows required")
    args = parser.parse_args()

    if args.strategy_spec:
        strategy_spec = json.loads(Path(args.strategy_spec).read_text(encoding="utf-8"))
        cost_model = CostModel(
            fixed_fee=args.fixed_fee,
            slippage_ticks=args.slippage_ticks,
            tick_size=args.tick_size,
        )
        if args.walk_forward_training_bars is not None or args.walk_forward_scoring_bars is not None:
            if args.walk_forward_training_bars is None or args.walk_forward_scoring_bars is None:
                raise SystemExit("--walk-forward-training-bars and --walk-forward-scoring-bars must be provided together")
            result = run_walk_forward_backtest(
                dataset_path=args.dataset_path,
                strategy_spec=strategy_spec,
                cost_model=cost_model,
                registry_path=args.run_registry,
                walk_forward=WalkForwardConfig(
                    training_bars=args.walk_forward_training_bars,
                    scoring_bars=args.walk_forward_scoring_bars,
                    step_bars=args.walk_forward_step_bars,
                ),
                fitness_constraints=FitnessConstraints(
                    min_trades=args.min_trades,
                    max_drawdown=args.max_drawdown,
                    max_cost_to_gross_ratio=args.max_cost_to_gross_ratio,
                    max_slippage_costs=args.max_slippage_costs,
                    min_profitable_windows=args.min_profitable_windows,
                    min_profitable_window_ratio=args.min_profitable_window_ratio,
                ),
            )
            print(json.dumps({
                "datasetId": result.dataset_id,
                "strategyId": result.strategy_id,
                "engine": result.engine,
                "windows": len(result.windows),
                "survived": result.fitness.survived,
                "score": result.fitness.score,
                "rejectionReasons": result.fitness.rejection_reasons,
                "rankingInputs": result.fitness.ranking_inputs,
                "registryRecord": str(result.registry_record_path),
            }))
            return

        result = run_strategy_backtest(
            dataset_path=args.dataset_path,
            strategy_spec=strategy_spec,
            cost_model=cost_model,
            registry_path=args.run_registry,
        )
        print(json.dumps({
            "datasetId": result.dataset_id,
            "strategyId": result.strategy_id,
            "engine": result.engine,
            "orders": len(result.orders),
            "grossPnl": result.gross_pnl,
            "totalCosts": result.total_costs,
            "netPnl": result.net_pnl,
            "registryRecord": str(result.registry_record_path),
        }))
        return

    result = run_smoke_backtest(args.dataset_path)
    print(json.dumps({
        "datasetId": result.dataset_id,
        "replayedBars": len(result.replayed_bars),
        "availableFeatures": len(result.available_features),
        "finalClose": result.final_close,
        "engine": result.engine,
    }))


if __name__ == "__main__":
    main()
