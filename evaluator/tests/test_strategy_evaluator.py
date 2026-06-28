from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "evaluator"))

from nautilus_evaluator import CostModel, run_strategy_backtest, validate_strategy_spec
from nautilus_evaluator import FitnessConstraints, WalkForwardConfig, run_walk_forward_backtest


FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "es-rth-5m-dataset"
SPEC_PATH = REPO_ROOT / "tests" / "fixtures" / "strategy-specs" / "supertrend-long.json"
HAND_WRITTEN_SPEC = json.loads(SPEC_PATH.read_text(encoding="utf-8"))


class StrategyEvaluatorTests(unittest.TestCase):
    def test_runs_hand_written_strategy_spec_end_to_end_and_records_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registry_path = Path(temp_dir) / "run-registry"

            result = run_strategy_backtest(
                dataset_path=FIXTURE_PATH,
                strategy_spec=HAND_WRITTEN_SPEC,
                cost_model=CostModel(fixed_fee=2.50, slippage_ticks=1, tick_size=0.25),
                registry_path=registry_path,
            )

            self.assertEqual(result.dataset_id, "es-rth-5m-fixture")
            self.assertEqual(result.strategy_id, "fixture-supertrend-long")
            self.assertEqual(result.engine, "nautilus-compatible-strategy-replay")
            self.assertEqual(result.orders[0].reason, "entry:feature_equals")
            self.assertEqual(result.orders[0].signal_bar_time.isoformat(), "2026-06-25T13:35:00+00:00")
            self.assertEqual(result.orders[0].execution_bar_time.isoformat(), "2026-06-25T13:40:00+00:00")
            self.assertEqual(result.orders[-1].reason, "intraday-flat-before-close")
            self.assertEqual(result.orders[-1].execution_bar_time.isoformat(), "2026-06-25T19:55:00+00:00")
            self.assertEqual(result.position_quantity, 0)
            self.assertEqual(result.gross_pnl, 1025)
            self.assertEqual(result.total_costs, 550)
            self.assertEqual(result.net_pnl, 475)

            registry_record = json.loads(result.registry_record_path.read_text(encoding="utf-8"))
            self.assertEqual(registry_record["recordType"], "Evaluator Replay Helper")
            self.assertFalse(registry_record["authoritative"])
            self.assertEqual(registry_record["dataset"]["datasetId"], "es-rth-5m-fixture")
            self.assertEqual(registry_record["dataset"]["collectedAt"], "2026-06-28T12:00:00.000Z")
            self.assertEqual(registry_record["strategySpec"], HAND_WRITTEN_SPEC)
            self.assertEqual(registry_record["costModel"]["fixedFee"], 2.5)
            self.assertEqual(registry_record["evaluatorVersion"], "strategy-replay-v1")
            self.assertEqual(registry_record["artifacts"]["orders"], "orders.json")

    def test_strategy_spec_validation_rejects_missing_intraday_flat_control(self):
        invalid_spec = {
            **HAND_WRITTEN_SPEC,
            "riskControls": {
                **HAND_WRITTEN_SPEC["riskControls"],
                "intradayFlat": False,
            },
        }

        with self.assertRaisesRegex(ValueError, "riskControls.intradayFlat must be true"):
            validate_strategy_spec(invalid_spec)

    def test_walk_forward_scoring_does_not_use_training_window_features(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dataset_path = _write_walk_forward_dataset(
                Path(temp_dir) / "dataset",
                features=[
                    _direction_feature("feature-train", "2026-06-25T13:35:00.000Z"),
                ],
            )

            result = run_walk_forward_backtest(
                dataset_path=dataset_path,
                strategy_spec=HAND_WRITTEN_SPEC,
                cost_model=CostModel(fixed_fee=0, slippage_ticks=0, tick_size=0.25),
                registry_path=Path(temp_dir) / "run-registry",
                walk_forward=WalkForwardConfig(training_bars=2, scoring_bars=4),
                fitness_constraints=FitnessConstraints(min_trades=0),
            )

            self.assertEqual(len(result.windows), 1)
            self.assertEqual(result.windows[0].training.start, "2026-06-25T13:30:00+00:00")
            self.assertEqual(result.windows[0].training.end, "2026-06-25T13:35:00+00:00")
            self.assertEqual(result.windows[0].scoring.start, "2026-06-25T13:40:00+00:00")
            self.assertEqual(result.windows[0].scoring.end, "2026-06-25T13:55:00+00:00")
            self.assertEqual(result.window_results[0].order_count, 0)
            self.assertEqual(result.window_results[0].net_pnl, 0)

    def test_walk_forward_config_can_create_multiple_scoring_windows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dataset_path = _write_walk_forward_dataset(Path(temp_dir) / "dataset", features=[])

            result = run_walk_forward_backtest(
                dataset_path=dataset_path,
                strategy_spec=HAND_WRITTEN_SPEC,
                cost_model=CostModel(fixed_fee=0, slippage_ticks=0, tick_size=0.25),
                registry_path=Path(temp_dir) / "run-registry",
                walk_forward=WalkForwardConfig(training_bars=2, scoring_bars=2, step_bars=2),
                fitness_constraints=FitnessConstraints(min_trades=0, min_profitable_windows=0),
            )

            self.assertEqual([window.window_id for window in result.windows], ["wf-1", "wf-2"])
            self.assertEqual(result.windows[0].training.end_index, 1)
            self.assertEqual(result.windows[0].scoring.start_index, 2)
            self.assertEqual(result.windows[0].scoring.end_index, 3)
            self.assertEqual(result.windows[1].training.start_index, 2)
            self.assertEqual(result.windows[1].scoring.start_index, 4)
            self.assertEqual(result.windows[1].scoring.end_index, 5)
            self.assertEqual([window_result.order_count for window_result in result.window_results], [0, 0])

    def test_walk_forward_fitness_rejects_high_sharpe_candidate_that_fails_survival(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registry_path = Path(temp_dir) / "run-registry"
            dataset_path = _write_walk_forward_dataset(
                Path(temp_dir) / "dataset",
                prices=[100, 100, 100, 100, 100, 110, 100, 100, 100, 100, 100, 112],
                features=[
                    _direction_feature("feature-score-1", "2026-06-25T13:45:00.000Z"),
                    _direction_feature("feature-score-2", "2026-06-25T14:15:00.000Z"),
                ],
            )

            result = run_walk_forward_backtest(
                dataset_path=dataset_path,
                strategy_spec=HAND_WRITTEN_SPEC,
                cost_model=CostModel(fixed_fee=2.50, slippage_ticks=1, tick_size=0.25),
                registry_path=registry_path,
                walk_forward=WalkForwardConfig(training_bars=2, scoring_bars=4, step_bars=6),
                fitness_constraints=FitnessConstraints(min_trades=3, max_drawdown=2000),
            )

            self.assertEqual([window_result.trade_count for window_result in result.window_results], [1, 1])
            self.assertGreater(result.fitness.ranking_inputs["outOfSampleSharpe"], 0)
            self.assertFalse(result.fitness.survived)
            self.assertEqual(result.fitness.rejection_reasons, ["min_trades"])
            self.assertIsNone(result.fitness.score)

            registry_record = json.loads(result.registry_record_path.read_text(encoding="utf-8"))
            self.assertEqual(registry_record["recordType"], "Evaluator Walk-Forward Replay Helper")
            self.assertFalse(registry_record["authoritative"])
            self.assertEqual(registry_record["walkForward"]["config"]["trainingBars"], 2)
            self.assertEqual(registry_record["walkForward"]["windows"][0]["scoring"]["start"], "2026-06-25T13:40:00+00:00")
            self.assertEqual(registry_record["trainingWindowResults"][0]["tradeCount"], 0)
            self.assertEqual(registry_record["perWindowResults"][0]["tradeCount"], 1)
            self.assertEqual(registry_record["fitness"]["survivalChecks"]["minTrades"]["passed"], False)
            self.assertEqual(registry_record["fitness"]["rejectionReasons"], ["min_trades"])
            self.assertGreater(registry_record["fitness"]["rankingInputs"]["outOfSampleSharpe"], 0)


def _write_walk_forward_dataset(path: Path, features, prices=None):
    path.mkdir()
    prices = prices or [100, 100, 100, 100, 100, 110]
    manifest = {
        "schemaVersion": 1,
        "datasetId": "walk-forward-fixture",
        "collectedAt": "2026-06-28T12:00:00.000Z",
        "source": {"kind": "tradingview"},
        "symbol": {"ticker": "CME_MINI:ES1!"},
        "bar": {"interval": "5m", "priceScale": 100},
        "session": {"timezone": "UTC", "start": "13:30", "end": "23:59"},
    }
    start_time = datetime(2026, 6, 25, 13, 30, tzinfo=timezone.utc)
    bars = [
        _bar((start_time + timedelta(minutes=5 * index)).isoformat().replace("+00:00", ".000Z"), price)
        for index, price in enumerate(prices)
    ]
    (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (path / "bars.json").write_text(json.dumps(bars), encoding="utf-8")
    (path / "features.json").write_text(json.dumps(features), encoding="utf-8")
    return path


def _bar(timestamp: str, open_price: float):
    return {
        "time": timestamp,
        "open": open_price,
        "high": open_price,
        "low": open_price,
        "close": open_price,
        "volume": 100,
    }


def _direction_feature(feature_id: str, timestamp: str):
    return {
        "id": feature_id,
        "source": "tradingview",
        "indicatorId": "STD;Supertrend",
        "type": "plot",
        "name": "direction",
        "eventTime": timestamp,
        "availabilityTime": timestamp,
        "repaintingRisk": "confirmed",
        "value": 1,
    }


if __name__ == "__main__":
    unittest.main()
