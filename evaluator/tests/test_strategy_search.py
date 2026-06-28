from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import shutil
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "evaluator"))

from nautilus_evaluator import CostModel, FitnessConstraints, WalkForwardConfig
from nautilus_evaluator import (
    BoundedSearchConfig,
    StrategyTemplate,
    generate_bounded_template_specs,
    reproduce_search_winner,
    run_bounded_strategy_search,
)


BASE_SPEC = {
    "schemaVersion": 1,
    "strategyId": "search-template",
    "description": "Search fixture candidate.",
    "parameters": {"direction_feature": "direction"},
    "entryRules": [
        {
            "type": "feature_equals",
            "feature": {"indicatorId": "STD;Supertrend", "name": "direction"},
            "value": 1,
            "side": "long",
        }
    ],
    "exits": {"maxBarsInTrade": 100},
    "sizing": {"type": "fixed", "quantity": 1},
    "riskControls": {
        "intradayFlat": True,
        "flatBeforeCloseMinutes": 5,
        "stopLossTicks": 12,
        "takeProfitTicks": 20,
    },
    "tunableParameters": {},
}


class StrategySearchTests(unittest.TestCase):
    def test_bounded_template_search_generates_schema_valid_specs(self):
        specs = generate_bounded_template_specs(
            [
                StrategyTemplate(
                    template_id="supertrend-direction",
                    base_spec=BASE_SPEC,
                    choices={
                        "entryRules.0.value": [1, -1],
                        "sizing.quantity": [1, 2],
                    },
                )
            ],
            BoundedSearchConfig(method="deterministic", max_candidates=3),
        )

        self.assertEqual([spec["strategyId"] for spec in specs], [
            "supertrend-direction-1",
            "supertrend-direction-2",
            "supertrend-direction-3",
        ])
        self.assertEqual(specs[0]["entryRules"][0]["value"], 1)
        self.assertEqual(specs[1]["sizing"]["quantity"], 2)
        self.assertEqual(specs[2]["entryRules"][0]["value"], -1)

    def test_random_template_search_is_seeded_and_bounded(self):
        template = StrategyTemplate(
            template_id="seeded",
            base_spec=BASE_SPEC,
            choices={
                "entryRules.0.value": [1, -1, 2],
                "sizing.quantity": [1, 2],
            },
        )

        first = generate_bounded_template_specs([template], BoundedSearchConfig(method="random", max_candidates=4, seed=7))
        second = generate_bounded_template_specs([template], BoundedSearchConfig(method="random", max_candidates=4, seed=7))

        self.assertEqual(first, second)
        self.assertEqual(len(first), 4)

    def test_bounded_search_evaluates_ranks_records_and_reproduces_winner(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dataset_path = _write_search_dataset(Path(temp_dir) / "dataset")
            registry_path = Path(temp_dir) / "run-registry"
            template = StrategyTemplate(
                template_id="direction-template",
                base_spec=BASE_SPEC,
                choices={
                    "entryRules.0.value": [1, -1],
                    "sizing.quantity": [1, 2],
                },
            )

            result = run_bounded_strategy_search(
                dataset_path=dataset_path,
                templates=[template],
                cost_model=CostModel(fixed_fee=0, slippage_ticks=0, tick_size=0.25),
                registry_path=registry_path,
                walk_forward=WalkForwardConfig(training_sessions=1, scoring_sessions=1, step_sessions=2),
                fitness_constraints=FitnessConstraints(min_trades=1, min_profitable_windows=1),
                search_config=BoundedSearchConfig(method="optuna_style", max_candidates=4, seed=11),
            )

            self.assertEqual(result.optimizer_config["method"], "optuna_style")
            self.assertEqual(len(result.evaluated_candidates), 4)
            self.assertEqual(len(result.surviving_candidates), 2)
            self.assertIsNotNone(result.winning_candidate)
            self.assertEqual(result.winning_candidate.strategy_spec["entryRules"][0]["value"], 1)
            self.assertEqual(result.winning_candidate.strategy_spec["sizing"]["quantity"], 2)
            self.assertTrue(result.winning_candidate.fitness.survived)
            self.assertGreater(result.winning_candidate.fitness.score, 0)

            search_record = json.loads(result.registry_record_path.read_text(encoding="utf-8"))
            self.assertEqual(search_record["recordType"], "Nautilus Validation Search")
            self.assertTrue(search_record["authoritative"])
            self.assertEqual(search_record["status"], "completed")
            self.assertEqual(search_record["evaluatorVersion"], "strategy-replay-v1")
            self.assertEqual(search_record["dataset"]["datasetId"], "search-fixture")
            self.assertEqual(search_record["dataset"]["artifacts"]["snapshot"], "dataset")
            self.assertEqual(search_record["optimizerConfig"]["method"], "optuna_style")
            self.assertEqual(len(search_record["generatedCandidates"]), 4)
            self.assertEqual(len(search_record["evaluatedSpecs"]), 4)
            self.assertEqual(len(search_record["survivingCandidates"]), 2)
            self.assertEqual(len(search_record["rejectedCandidates"]), 2)
            self.assertEqual(
                [candidate["optimizerPhase"] for candidate in search_record["evaluatedSpecs"]],
                ["exploration", "exploration", "exploitation", "exploitation"],
            )
            self.assertEqual(search_record["ranking"][0], result.winning_candidate.strategy_id)
            self.assertEqual(search_record["winningRun"]["strategySpec"], result.winning_candidate.strategy_spec)
            self.assertEqual(search_record["winningRun"]["provenance"]["recordType"], "Nautilus Walk-Forward Validation")
            self.assertTrue(search_record["winningRun"]["provenance"]["requiredNautilusProvenance"])
            self.assertIsNotNone(search_record["bestRejectedCandidate"])
            self.assertTrue(search_record["bestRejectedCandidate"]["diagnosticOnly"])
            self.assertIn("reproducibilityInputs", search_record)
            self.assertTrue((result.registry_record_path.parent / search_record["winningRun"]["artifacts"]["runRecord"]).exists())

            shutil.rmtree(dataset_path)
            reproduced = reproduce_search_winner(result.registry_record_path)
            self.assertEqual(reproduced.strategy_id, result.winning_candidate.strategy_id)
            self.assertEqual(reproduced.fitness.score, result.winning_candidate.fitness.score)

    def test_bounded_search_records_no_survivors_without_winner(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dataset_path = _write_search_dataset(Path(temp_dir) / "dataset")
            registry_path = Path(temp_dir) / "run-registry"
            template = StrategyTemplate(
                template_id="direction-template",
                base_spec=BASE_SPEC,
                choices={
                    "entryRules.0.value": [1, -1],
                    "sizing.quantity": [1, 2],
                },
            )

            result = run_bounded_strategy_search(
                dataset_path=dataset_path,
                templates=[template],
                cost_model=CostModel(fixed_fee=0, slippage_ticks=0, tick_size=0.25),
                registry_path=registry_path,
                walk_forward=WalkForwardConfig(training_sessions=1, scoring_sessions=1, step_sessions=2),
                fitness_constraints=FitnessConstraints(min_trades=99, min_profitable_windows=1),
                search_config=BoundedSearchConfig(method="deterministic", max_candidates=4, seed=11),
            )

            self.assertEqual(len(result.evaluated_candidates), 4)
            self.assertEqual(result.surviving_candidates, [])
            self.assertIsNone(result.winning_candidate)
            self.assertEqual(len(result.rejected_candidates), 4)

            search_record = json.loads(result.registry_record_path.read_text(encoding="utf-8"))
            self.assertEqual(search_record["status"], "completed_no_survivors")
            self.assertEqual(search_record["ranking"], [])
            self.assertEqual(search_record["survivingCandidates"], [])
            self.assertIsNone(search_record["winningRun"])
            self.assertIsNotNone(search_record["bestRejectedCandidate"])
            self.assertTrue(search_record["bestRejectedCandidate"]["diagnosticOnly"])
            self.assertIn("min_trades", search_record["rejectedCandidates"][0]["rejectionReasons"])

            with self.assertRaisesRegex(ValueError, "no surviving Nautilus Validation winner"):
                reproduce_search_winner(result.registry_record_path)

    def test_llm_proposed_candidates_are_validated_before_evaluation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dataset_path = _write_search_dataset(Path(temp_dir) / "dataset")
            invalid_proposal = {**BASE_SPEC, "schemaVersion": 2, "strategyId": "invalid-llm-proposal"}
            valid_proposal = {**BASE_SPEC, "strategyId": "valid-llm-proposal"}

            result = run_bounded_strategy_search(
                dataset_path=dataset_path,
                templates=[],
                proposed_candidates=[invalid_proposal, valid_proposal],
                cost_model=CostModel(fixed_fee=0, slippage_ticks=0, tick_size=0.25),
                registry_path=Path(temp_dir) / "run-registry",
                walk_forward=WalkForwardConfig(training_sessions=1, scoring_sessions=1),
                fitness_constraints=FitnessConstraints(min_trades=0, min_profitable_windows=0),
                search_config=BoundedSearchConfig(method="deterministic", max_candidates=4),
            )

            self.assertEqual(len(result.rejected_candidates), 1)
            self.assertIn("schemaVersion must be 1", result.rejected_candidates[0]["error"])
            self.assertEqual([candidate.strategy_id for candidate in result.evaluated_candidates], ["valid-llm-proposal"])
            self.assertEqual([candidate.strategy_id for candidate in result.surviving_candidates], ["valid-llm-proposal"])


def _write_search_dataset(path: Path):
    path.mkdir()
    session_dates = ["2026-06-25", "2026-06-26", "2026-06-27", "2026-06-28"]
    bars_per_session = 3
    manifest = {
        "schemaVersion": 1,
        "datasetId": "search-fixture",
        "collectedAt": "2026-06-28T12:00:00.000Z",
        "source": {"kind": "tradingview"},
        "symbol": {"ticker": "CME_MINI:ES1!"},
        "bar": {"interval": "5m", "priceScale": 100},
        "session": {
            "timezone": "UTC",
            "start": "13:30",
            "end": "13:45",
            "sessions": [
                {
                    "id": session_date,
                    "firstBarTime": _session_bar_time(session_date, 0).isoformat().replace("+00:00", ".000Z"),
                    "lastBarTime": _session_bar_time(session_date, bars_per_session - 1).isoformat().replace("+00:00", ".000Z"),
                    "barCount": bars_per_session,
                }
                for session_date in session_dates
            ],
        },
    }
    prices = [100, 100, 100, 100, 100, 106, 100, 100, 100, 100, 100, 110]
    bars = [
        _bar(_session_bar_time(session_date, bar_index).isoformat().replace("+00:00", ".000Z"), price)
        for session_index, session_date in enumerate(session_dates)
        for bar_index, price in enumerate(
            prices[session_index * bars_per_session : (session_index + 1) * bars_per_session]
        )
    ]
    features = [
        _direction_feature("feature-score-1", "2026-06-26T13:30:00.000Z", 1),
        _direction_feature("feature-score-2", "2026-06-28T13:30:00.000Z", 1),
    ]
    (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (path / "bars.json").write_text(json.dumps(bars), encoding="utf-8")
    (path / "features.json").write_text(json.dumps(features), encoding="utf-8")
    return path


def _session_bar_time(session_date: str, index: int) -> datetime:
    date = datetime.fromisoformat(session_date)
    return datetime(date.year, date.month, date.day, 13, 30, tzinfo=timezone.utc) + timedelta(minutes=5 * index)


def _bar(timestamp: str, open_price: float):
    return {
        "time": timestamp,
        "open": open_price,
        "high": open_price,
        "low": open_price,
        "close": open_price,
        "volume": 100,
    }


def _direction_feature(feature_id: str, timestamp: str, value: int):
    return {
        "id": feature_id,
        "source": "tradingview",
        "indicatorId": "STD;Supertrend",
        "type": "plot",
        "name": "direction",
        "eventTime": timestamp,
        "availabilityTime": timestamp,
        "repaintingRisk": "confirmed",
        "value": value,
    }


if __name__ == "__main__":
    unittest.main()
