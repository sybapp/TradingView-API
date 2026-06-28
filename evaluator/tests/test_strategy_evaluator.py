from pathlib import Path
import json
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "evaluator"))

from nautilus_evaluator import CostModel, run_strategy_backtest, validate_strategy_spec


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


if __name__ == "__main__":
    unittest.main()
