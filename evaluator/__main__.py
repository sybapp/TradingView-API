"""CLI entry point for smoke-replaying a Versioned Dataset."""

from __future__ import annotations

import argparse
import json

from .nautilus_evaluator import run_smoke_backtest


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay a TradingView versioned dataset.")
    parser.add_argument("dataset_path", help="Path to a Versioned Dataset directory")
    args = parser.parse_args()

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
