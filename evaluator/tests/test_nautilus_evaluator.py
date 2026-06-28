from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "evaluator"))

from nautilus_evaluator import (
    load_versioned_dataset,
    run_smoke_backtest,
    to_nautilus_bar_inputs,
    to_nautilus_bars,
)


FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "es-rth-5m-dataset"


class NautilusEvaluatorTests(unittest.TestCase):
    def test_loads_fixture_dataset(self):
        dataset = load_versioned_dataset(FIXTURE_PATH)

        self.assertEqual(dataset.dataset_id, "es-rth-5m-fixture")
        self.assertEqual(dataset.ticker, "CME_MINI:ES1!")
        self.assertEqual(dataset.interval, "5m")
        self.assertEqual(len(dataset.bars), 81)
        self.assertEqual(len(dataset.features), 2)
        self.assertEqual(dataset.features[1].name, "pivot_high_confirmed")
        self.assertEqual(dataset.features[1].value, {"price": 5502.5, "text": "PH"})

    def test_converts_bars_to_nautilus_compatible_inputs(self):
        dataset = load_versioned_dataset(FIXTURE_PATH)

        bars = to_nautilus_bar_inputs(dataset)

        self.assertEqual(len(bars), 81)
        self.assertEqual(bars[0].instrument_id, "CME_MINI:ES1!")
        self.assertEqual(bars[0].bar_type, "ES1!.CME_MINI-5-MINUTE-LAST-EXTERNAL")
        self.assertEqual(bars[0].ts_event, 1782394200000000000)
        self.assertEqual(bars[0].open, 550025)
        self.assertEqual(bars[0].high, 550250)
        self.assertEqual(bars[0].low, 549875)
        self.assertEqual(bars[0].close, 550100)
        self.assertEqual(bars[0].volume, 1200)

    def test_smoke_backtest_replays_fixture_bars_and_features(self):
        result = run_smoke_backtest(FIXTURE_PATH)

        self.assertEqual(result.dataset_id, "es-rth-5m-fixture")
        self.assertEqual(len(result.replayed_bars), 81)
        self.assertEqual([feature.id for feature in result.available_features], ["feature-1", "feature-2"])
        self.assertEqual(result.final_close, 551725)
        self.assertEqual(result.engine, "nautilus-compatible-replay")

    def test_real_nautilus_bar_conversion_builds_nautilus_bars(self):
        dataset = load_versioned_dataset(FIXTURE_PATH)

        bars = to_nautilus_bars(dataset)

        self.assertEqual(len(bars), 81)
        self.assertEqual(str(bars[0].bar_type), "ES1!.CME_MINI-5-MINUTE-LAST-EXTERNAL")


if __name__ == "__main__":
    unittest.main()
