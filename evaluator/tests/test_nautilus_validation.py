from pathlib import Path
import json
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "evaluator"))

from nautilus_evaluator import CostModel, run_nautilus_validation_backtest


FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "es-rth-5m-dataset"
SPEC_PATH = REPO_ROOT / "tests" / "fixtures" / "strategy-specs" / "supertrend-long.json"
HAND_WRITTEN_SPEC = json.loads(SPEC_PATH.read_text(encoding="utf-8"))


class NautilusValidationTests(unittest.TestCase):
    def test_runs_fixture_strategy_spec_inside_real_nautilus_engine_and_records_validation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_nautilus_validation_backtest(
                dataset_path=FIXTURE_PATH,
                strategy_spec=HAND_WRITTEN_SPEC,
                cost_model=CostModel(fixed_fee=2.50, slippage_ticks=1, tick_size=0.25),
                registry_path=Path(temp_dir) / "run-registry",
            )

            self.assertEqual(result.dataset_id, "es-rth-5m-fixture")
            self.assertEqual(result.strategy_id, "fixture-supertrend-long")
            self.assertEqual(result.engine, "nautilus-trader-backtest-engine")
            self.assertEqual(len(result.orders), 2)
            self.assertEqual(result.orders[0].reason, "entry:feature_equals")
            self.assertEqual(result.orders[0].signal_bar_time.isoformat(), "2026-06-25T13:35:00+00:00")
            self.assertEqual(result.orders[0].execution_bar_time.isoformat(), "2026-06-25T13:40:00+00:00")
            self.assertEqual(result.orders[-1].reason, "intraday-flat-before-close")
            self.assertEqual(result.orders[-1].execution_bar_time.isoformat(), "2026-06-25T19:55:00+00:00")
            self.assertEqual(result.position_quantity, 0)

            registry_record = json.loads(result.registry_record_path.read_text(encoding="utf-8"))
            self.assertEqual(registry_record["recordType"], "Nautilus Validation")
            self.assertEqual(registry_record["nautilusTrader"]["version"], "1.229.0")
            self.assertFalse(registry_record["nautilusTrader"]["runtimeImportFromThirdPartyReference"])
            self.assertEqual(registry_record["dataset"]["datasetId"], "es-rth-5m-fixture")
            self.assertEqual(registry_record["strategySpecIdentity"]["strategyId"], "fixture-supertrend-long")
            self.assertEqual(registry_record["instrument"]["instrumentId"], "ESU6.GLBX")
            self.assertEqual(registry_record["venue"]["name"], "GLBX")
            self.assertEqual(registry_record["barType"]["value"], "ESU6.GLBX-5-MINUTE-LAST-EXTERNAL")
            self.assertEqual(
                registry_record["costConfiguration"]["nautilusExecution"]["feeModel"]["class"],
                "PerContractFeeModel",
            )
            self.assertEqual(
                registry_record["costConfiguration"]["nautilusExecution"]["fillModel"]["class"],
                "OneTickSlippageFillModel",
            )
            for artifact in registry_record["artifacts"].values():
                self.assertTrue((result.registry_record_path.parent / artifact).exists())

    def test_nautilus_validation_fails_fast_for_unsupported_slippage_mapping(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "supports only 0 or 1 slippage tick"):
                run_nautilus_validation_backtest(
                    dataset_path=FIXTURE_PATH,
                    strategy_spec=HAND_WRITTEN_SPEC,
                    cost_model=CostModel(fixed_fee=2.50, slippage_ticks=2, tick_size=0.25),
                    registry_path=Path(temp_dir) / "run-registry",
                )


if __name__ == "__main__":
    unittest.main()
