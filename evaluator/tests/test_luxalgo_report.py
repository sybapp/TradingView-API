from pathlib import Path
import json
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "evaluator"))

from nautilus_evaluator.luxalgo_report import build_report_summary, render_markdown_report


class LuxAlgoReportTests(unittest.TestCase):
    def test_report_marks_sub_threshold_candidates_as_smoke_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dataset_path = root / "dataset"
            search_path = root / "runs" / "searches" / "strategy-search-fixture"
            dataset_path.mkdir()
            search_path.mkdir(parents=True)
            _write_dataset(dataset_path)
            search_record_path = search_path / "search.json"
            search_record = {
                "recordType": "Nautilus Validation Search",
                "status": "completed",
                "dataset": {"datasetId": "fixture"},
                "walkForward": {"config": {"trainingSessions": 1, "scoringSessions": 1, "stepSessions": 1}},
                "costModel": {"fixedFee": 2.5, "slippageTicks": 1, "tickSize": 0.25},
                "fitnessConstraints": {"minTrades": 1},
                "generatedCandidates": [{}, {}],
                "evaluatedSpecs": [
                    {
                        "candidateId": "candidate-1",
                        "strategyId": "luxalgo-1",
                        "totalTradeCount": 12,
                        "tradeComparison": {"threshold": 30, "actual": 12, "status": "below_threshold"},
                    }
                ],
                "survivingCandidates": [],
                "rejectedCandidates": [],
                "winningRun": {
                    "candidateId": "training-window-selection",
                    "strategyId": "walk-forward-selected-candidates",
                    "totalTradeCount": 12,
                    "tradeComparison": {"threshold": 30, "actual": 12, "status": "below_threshold"},
                    "artifacts": {"runRecord": "winning-run/run.json"},
                },
                "reproducibilityInputs": {"datasetSnapshot": "dataset"},
            }
            search_record_path.write_text(json.dumps(search_record), encoding="utf-8")

            summary = build_report_summary(
                dataset_path=dataset_path,
                search_record_path=search_record_path,
                search_record=search_record,
            )

            self.assertEqual(summary["search"]["comparisonStatus"], "smoke-only")
            self.assertEqual(summary["search"]["actualTradeCount"], 12)
            self.assertEqual(summary["dataset"]["bullishStructureEventSignalCount"], 1)
            self.assertEqual(summary["dataset"]["rejectedOrAmbiguousDerivationCount"], 2)
            self.assertIn("Comparison status: `smoke-only`", render_markdown_report(summary))


def _write_dataset(dataset_path: Path) -> None:
    (dataset_path / "manifest.json").write_text(json.dumps({
        "schemaVersion": 1,
        "datasetId": "fixture",
        "collectedAt": "2026-06-28T12:00:00.000Z",
        "source": "tradingview",
        "symbol": {"ticker": "CME_MINI:ES1!", "root": "ES", "assetClass": "equity_index_futures"},
        "session": {"name": "RTH", "timezone": "America/New_York", "start": "09:30", "end": "16:00"},
        "bar": {"interval": "5m", "priceScale": 100, "volumeUnit": "contracts"},
        "contract": {"type": "continuous_futures", "continuous": True, "rollPolicy": {"source": "fixture"}},
        "indicators": [],
    }), encoding="utf-8")
    (dataset_path / "bars.json").write_text(json.dumps([
        {"time": "2026-06-25T13:30:00.000Z", "open": 1, "high": 2, "low": 0, "close": 1, "volume": 1}
    ]), encoding="utf-8")
    (dataset_path / "features.json").write_text(json.dumps([
        {"type": "label", "name": "BOS", "indicatorId": "LUX", "value": {}, "source": "tradingview", "id": "1", "eventTime": "2026-06-25T13:30:00.000Z", "availabilityTime": "2026-06-25T13:30:00.000Z", "repaintingRisk": "confirmed"},
        {"type": "box", "name": "bullish_order_block", "indicatorId": "LUX", "value": {"text": "Bullish OB"}, "source": "tradingview", "id": "2", "eventTime": "2026-06-25T13:30:00.000Z", "availabilityTime": "2026-06-25T13:30:00.000Z", "repaintingRisk": "confirmed"},
        {"type": "signal", "name": "bullish_bos", "indicatorId": "LUX", "value": True, "source": "tradingview", "id": "3", "eventTime": "2026-06-25T13:30:00.000Z", "availabilityTime": "2026-06-25T13:30:00.000Z", "repaintingRisk": "confirmed"},
        {"type": "signal", "name": "bullish_liquidity_zone_touch_entry", "indicatorId": "LUX", "value": True, "source": "tradingview", "id": "4", "eventTime": "2026-06-25T13:30:00.000Z", "availabilityTime": "2026-06-25T13:30:00.000Z", "repaintingRisk": "confirmed"},
    ]), encoding="utf-8")
    (dataset_path / "derivation-diagnostics.json").write_text(json.dumps({
        "schemaVersion": 1,
        "rules": [
            {"rule": "luxalgo-structure-event-direction", "counts": {"unresolvedDirection": 1}},
            {"rule": "luxalgo-liquidity-zone-entry", "counts": {"invalidZoneGeometry": 1}},
        ],
    }), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
