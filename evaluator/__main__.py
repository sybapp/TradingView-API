"""CLI entry point for replaying a Versioned Dataset."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .nautilus_evaluator import (
    CostModel,
    FitnessConstraints,
    WalkForwardConfig,
    run_nautilus_validation_backtest,
    run_smoke_backtest,
    run_walk_forward_candidate_selection_backtest,
    run_walk_forward_backtest,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay a TradingView versioned dataset.")
    parser.add_argument("dataset_path", help="Path to a Versioned Dataset directory")
    parser.add_argument("--strategy-spec", help="Path to a Strategy Spec JSON file")
    parser.add_argument(
        "--candidate-strategy-spec",
        action="append",
        default=[],
        help="Path to a candidate Strategy Spec JSON file for training-window walk-forward selection",
    )
    parser.add_argument("--run-registry", default="runs", help="Directory for strategy run records")
    parser.add_argument("--fixed-fee", type=float, default=2.50, help="Fixed fee per order")
    parser.add_argument("--slippage-ticks", type=int, default=1, help="Slippage ticks per order")
    parser.add_argument("--tick-size", type=float, default=0.25, help="Instrument tick size")
    parser.add_argument("--walk-forward-training-sessions", type=int, help="RTH sessions in each walk-forward training window")
    parser.add_argument("--walk-forward-scoring-sessions", type=int, help="RTH sessions in each walk-forward scoring window")
    parser.add_argument("--walk-forward-step-sessions", type=int, help="RTH sessions to advance between walk-forward windows")
    parser.add_argument("--min-trades", type=int, default=10, help="Minimum trades required to survive fitness checks")
    parser.add_argument("--max-drawdown", type=int, help="Maximum drawdown allowed by fitness checks")
    parser.add_argument("--max-cost-to-gross-ratio", type=float, help="Maximum total cost to absolute gross PnL ratio")
    parser.add_argument("--max-slippage-costs", type=int, help="Maximum total slippage costs allowed by fitness checks")
    parser.add_argument("--min-profitable-windows", type=int, default=1, help="Minimum profitable scoring windows required")
    parser.add_argument("--min-profitable-window-ratio", type=float, help="Minimum ratio of profitable scoring windows required")
    args = parser.parse_args()

    if args.strategy_spec or args.candidate_strategy_spec:
        strategy_spec = json.loads(Path(args.strategy_spec).read_text(encoding="utf-8")) if args.strategy_spec else None
        candidate_specs = [
            json.loads(Path(candidate_path).read_text(encoding="utf-8"))
            for candidate_path in args.candidate_strategy_spec
        ]
        cost_model = CostModel(
            fixed_fee=args.fixed_fee,
            slippage_ticks=args.slippage_ticks,
            tick_size=args.tick_size,
        )
        uses_session_walk_forward = (
            args.walk_forward_training_sessions is not None
            or args.walk_forward_scoring_sessions is not None
        )
        if uses_session_walk_forward:
            if args.walk_forward_training_sessions is None or args.walk_forward_scoring_sessions is None:
                raise SystemExit("--walk-forward-training-sessions and --walk-forward-scoring-sessions must be provided together")
            walk_forward = WalkForwardConfig(
                training_sessions=args.walk_forward_training_sessions,
                scoring_sessions=args.walk_forward_scoring_sessions,
                step_sessions=args.walk_forward_step_sessions,
            )
            fitness_constraints = FitnessConstraints(
                min_trades=args.min_trades,
                max_drawdown=args.max_drawdown,
                max_cost_to_gross_ratio=args.max_cost_to_gross_ratio,
                max_slippage_costs=args.max_slippage_costs,
                min_profitable_windows=args.min_profitable_windows,
                min_profitable_window_ratio=args.min_profitable_window_ratio,
            )
            if candidate_specs:
                if strategy_spec is not None:
                    candidate_specs.insert(0, strategy_spec)
                result = run_walk_forward_candidate_selection_backtest(
                    dataset_path=args.dataset_path,
                    candidate_specs=candidate_specs,
                    cost_model=cost_model,
                    registry_path=args.run_registry,
                    walk_forward=walk_forward,
                    fitness_constraints=fitness_constraints,
                )
            else:
                result = run_walk_forward_backtest(
                    dataset_path=args.dataset_path,
                    strategy_spec=strategy_spec,
                    cost_model=cost_model,
                    registry_path=args.run_registry,
                    walk_forward=walk_forward,
                    fitness_constraints=fitness_constraints,
                )
            print(json.dumps({
                "datasetId": result.dataset_id,
                "strategyId": result.strategy_id,
                "engine": result.engine,
                "windows": len(result.windows),
                "selectedCandidates": [
                    {
                        "windowId": selection.window_id,
                        "candidateId": selection.selected_candidate_id,
                        "strategyId": selection.selected_strategy_id,
                    }
                    for selection in result.selection_results
                ],
                "survived": result.fitness.survived,
                "score": result.fitness.score,
                "rejectionReasons": result.fitness.rejection_reasons,
                "rankingInputs": result.fitness.ranking_inputs,
                "registryRecord": str(result.registry_record_path),
            }))
            return

        if strategy_spec is None:
            raise SystemExit("--candidate-strategy-spec requires walk-forward training and scoring sessions")
        result = run_nautilus_validation_backtest(
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
