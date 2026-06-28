"""CLI entry point for replaying a Versioned Dataset."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .nautilus_evaluator import CostModel, run_smoke_backtest, run_strategy_backtest


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay a TradingView versioned dataset.")
    parser.add_argument("dataset_path", help="Path to a Versioned Dataset directory")
    parser.add_argument("--strategy-spec", help="Path to a Strategy Spec JSON file")
    parser.add_argument("--run-registry", default="runs", help="Directory for strategy run records")
    parser.add_argument("--fixed-fee", type=float, default=2.50, help="Fixed fee per order")
    parser.add_argument("--slippage-ticks", type=int, default=1, help="Slippage ticks per order")
    parser.add_argument("--tick-size", type=float, default=0.25, help="Instrument tick size")
    args = parser.parse_args()

    if args.strategy_spec:
        strategy_spec = json.loads(Path(args.strategy_spec).read_text(encoding="utf-8"))
        result = run_strategy_backtest(
            dataset_path=args.dataset_path,
            strategy_spec=strategy_spec,
            cost_model=CostModel(
                fixed_fee=args.fixed_fee,
                slippage_ticks=args.slippage_ticks,
                tick_size=args.tick_size,
            ),
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
