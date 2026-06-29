from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import json
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "evaluator"))

from nautilus_evaluator import CostModel, FitnessConstraints, WalkForwardConfig
from nautilus_evaluator import (
    BoundedSearchConfig,
    LuxAlgoIctSmcLongTemplateConfig,
    StrategyTemplate,
    create_luxalgo_ict_smc_long_strategy_template,
    generate_bounded_template_specs,
    reproduce_search_winner,
    run_bounded_strategy_search,
    validate_strategy_spec,
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
    def test_luxalgo_ict_smc_long_template_generates_schema_valid_signal_specs(self):
        template = create_luxalgo_ict_smc_long_strategy_template(
            LuxAlgoIctSmcLongTemplateConfig(
                indicator_id="LUX;ICT_SMC",
                event_types=("bos", "mss"),
                zone_types=("order_block", "fair_value_gap"),
                zone_preferences=("nearest-any", "prefer-OB"),
                confirmation_modes=("touch", "reclaim"),
                max_bars_after_structure_event=(4, 8),
                cooldown_bars_after_exit=(0, 2),
                stop_loss_ticks=(8, 12),
                max_bars_in_trade=(12, 24),
            )
        )

        specs = generate_bounded_template_specs(
            [template],
            BoundedSearchConfig(method="deterministic", max_candidates=5),
        )

        self.assertEqual(len(specs), 5)
        first = validate_strategy_spec(specs[0])
        self.assertEqual([rule.feature_type for rule in first.entry_rules], ["signal", "signal"])
        self.assertEqual(first.entry_rules[0].name, "bullish_bos")
        self.assertEqual(first.entry_rules[1].name, "bullish_liquidity_zone_touch_entry")
        self.assertEqual(first.exits.reverse_signal_rules[0].name, "bearish_bos")
        self.assertEqual(first.risk_controls.cooldown_bars_after_exit, 0)
        self.assertEqual(specs[0]["parameters"]["liquidityZoneType"], "order_block")
        self.assertIn("parameters.zonePreference", template.choices)
        self.assertIn("parameters.maxBarsAfterStructureEvent", template.choices)
        self.assertIn("riskControls.cooldownBarsAfterExit", template.choices)
        self.assertIn("riskControls.stopLossTicks", template.choices)
        self.assertIn("exits.maxBarsInTrade", template.choices)

    def test_bounded_search_records_mocked_signal_walk_forward_selection_trade_threshold(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dataset_path = _write_search_dataset(Path(temp_dir) / "dataset")
            registry_path = Path(temp_dir) / "run-registry"
            template = create_luxalgo_ict_smc_long_strategy_template(
                LuxAlgoIctSmcLongTemplateConfig(
                    indicator_id="LUX;ICT_SMC",
                    event_types=("bos",),
                    zone_types=("order_block",),
                    zone_preferences=("nearest-any",),
                    confirmation_modes=("touch",),
                    max_bars_after_structure_event=(4,),
                    cooldown_bars_after_exit=(0,),
                    stop_loss_ticks=(8, 12),
                    max_bars_in_trade=(12,),
                )
            )

            def fake_candidate_result(*, strategy_spec, **kwargs):
                return _fake_walk_forward_result(
                    registry_path,
                    strategy_spec["strategyId"],
                    trade_count=12 if strategy_spec["riskControls"]["stopLossTicks"] == 8 else 35,
                    survived=True,
                )

            def fake_selection_result(*, candidate_specs, **kwargs):
                selected = candidate_specs[1]
                return _fake_walk_forward_result(
                    registry_path,
                    "walk-forward-selected-candidates",
                    trade_count=12,
                    survived=True,
                    selection_results=[
                        SimpleNamespace(
                            window_id="wf-1",
                            selected_candidate_id="candidate-2",
                            selected_strategy_id=selected["strategyId"],
                        )
                    ],
                )

            with patch(
                "nautilus_evaluator.search.run_walk_forward_backtest",
                side_effect=fake_candidate_result,
            ), patch(
                "nautilus_evaluator.search.run_walk_forward_candidate_selection_backtest",
                side_effect=fake_selection_result,
            ), patch(
                "nautilus_evaluator.search._nautilus_validation_rejection_reasons",
                return_value=[],
            ):
                result = run_bounded_strategy_search(
                    dataset_path=dataset_path,
                    templates=[template],
                    cost_model=CostModel(fixed_fee=0, slippage_ticks=0, tick_size=0.25),
                    registry_path=registry_path,
                    walk_forward=WalkForwardConfig(training_sessions=1, scoring_sessions=1),
                    fitness_constraints=FitnessConstraints(min_trades=0, min_profitable_windows=0),
                    search_config=BoundedSearchConfig(method="deterministic", max_candidates=2),
                )

            self.assertEqual(result.winning_candidate.strategy_id, "walk-forward-selected-candidates")
            self.assertEqual(
                result.winning_candidate.result.selection_results[0].selected_strategy_id,
                "luxalgo-ict-smc-long-2",
            )
            search_record = json.loads(result.registry_record_path.read_text(encoding="utf-8"))
            self.assertEqual(search_record["evaluatedSpecs"][0]["totalTradeCount"], 12)
            self.assertEqual(search_record["evaluatedSpecs"][0]["tradeComparison"]["threshold"], 30)
            self.assertEqual(search_record["evaluatedSpecs"][0]["tradeComparison"]["status"], "below_threshold")
            self.assertEqual(search_record["evaluatedSpecs"][1]["tradeComparison"]["status"], "eligible")
            self.assertEqual(search_record["winningRun"]["tradeComparison"]["status"], "below_threshold")

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
            self.assertEqual([trial["trialNumber"] for trial in result.trial_results], [1, 2, 3, 4])
            self.assertEqual(len(result.evaluated_candidates), 4)
            self.assertEqual(len(result.surviving_candidates), 1)
            self.assertIsNotNone(result.winning_candidate)
            self.assertEqual(result.winning_candidate.strategy_id, "walk-forward-selected-candidates")
            self.assertEqual(
                result.winning_candidate.strategy_spec["selectionStrategy"],
                "training-window-candidate-selection",
            )
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
            self.assertEqual(search_record["sampler"]["name"], "optuna-tpe-sampler")
            self.assertEqual(search_record["sampler"]["samplerClass"], "TPESampler")
            self.assertEqual(search_record["sampler"]["objective"], "maximize_fitness_score")
            self.assertEqual(search_record["sampler"]["seed"], 11)
            self.assertEqual(len(search_record["trials"]), 4)
            self.assertEqual([trial["state"] for trial in search_record["trials"]], ["complete"] * 4)
            self.assertEqual([trial["trialNumber"] for trial in search_record["trials"]], [1, 2, 3, 4])
            self.assertEqual(search_record["winningRun"]["trialNumber"], None)
            self.assertEqual(
                search_record["winningRun"]["objectiveValue"],
                result.winning_candidate.fitness.score,
            )
            self.assertEqual(
                search_record["winningRun"]["trialParameters"]["selectedCandidates"][0]["strategyId"],
                "direction-template-2",
            )
            self.assertEqual(len(search_record["generatedCandidates"]), 4)
            self.assertEqual(len(search_record["evaluatedSpecs"]), 4)
            self.assertEqual(len(search_record["survivingCandidates"]), 1)
            self.assertEqual(len(search_record["rejectedCandidates"]), 0)
            self.assertEqual(
                [candidate["optimizerPhase"] for candidate in search_record["evaluatedSpecs"]],
                ["trial", "trial", "trial", "trial"],
            )
            self.assertEqual(
                [candidate["objectiveValue"] for candidate in search_record["evaluatedSpecs"]],
                [trial["objectiveValue"] for trial in search_record["trials"]],
            )
            self.assertEqual(search_record["ranking"][0], result.winning_candidate.strategy_id)
            self.assertEqual(
                [candidate["candidateId"] for candidate in search_record["evaluatedSpecs"]],
                ["candidate-1", "candidate-2", "candidate-3", "candidate-4"],
            )
            self.assertEqual(search_record["winningRun"]["strategySpec"], result.winning_candidate.strategy_spec)
            self.assertEqual(
                search_record["winningRun"]["strategySpec"]["selectionStrategy"],
                "training-window-candidate-selection",
            )
            self.assertEqual(search_record["winningRun"]["provenance"]["recordType"], "Nautilus Walk-Forward Validation")
            self.assertTrue(search_record["winningRun"]["provenance"]["requiredNautilusProvenance"])
            self.assertIsNotNone(search_record["bestRejectedCandidate"])
            self.assertTrue(search_record["bestRejectedCandidate"]["diagnosticOnly"])
            self.assertIn("reproducibilityInputs", search_record)
            self.assertIn("trialsHash", search_record["reproducibilityInputs"])
            self.assertTrue(
                (result.registry_record_path.parent / search_record["winningRun"]["artifacts"]["runRecord"]).exists()
            )
            self.assertTrue(
                (result.registry_record_path.parent / search_record["winningRun"]["artifacts"]["ordersByWindow"]).exists()
            )

            shutil.rmtree(dataset_path)
            reproduced = reproduce_search_winner(result.registry_record_path)
            self.assertEqual(reproduced.strategy_id, result.winning_candidate.strategy_id)
            self.assertEqual(reproduced.fitness.score, result.winning_candidate.fitness.score)

    def test_optuna_style_trial_search_is_seed_reproducible(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dataset_path = _write_search_dataset(Path(temp_dir) / "dataset")
            template = StrategyTemplate(
                template_id="direction-template",
                base_spec=BASE_SPEC,
                choices={
                    "entryRules.0.value": [1, -1],
                    "sizing.quantity": [1, 2],
                },
            )
            search_config = BoundedSearchConfig(method="optuna_style", max_candidates=4, seed=11)

            first = run_bounded_strategy_search(
                dataset_path=dataset_path,
                templates=[template],
                cost_model=CostModel(fixed_fee=0, slippage_ticks=0, tick_size=0.25),
                registry_path=Path(temp_dir) / "run-registry-first",
                walk_forward=WalkForwardConfig(training_sessions=1, scoring_sessions=1, step_sessions=2),
                fitness_constraints=FitnessConstraints(min_trades=1, min_profitable_windows=1),
                search_config=search_config,
            )
            second = run_bounded_strategy_search(
                dataset_path=dataset_path,
                templates=[template],
                cost_model=CostModel(fixed_fee=0, slippage_ticks=0, tick_size=0.25),
                registry_path=Path(temp_dir) / "run-registry-second",
                walk_forward=WalkForwardConfig(training_sessions=1, scoring_sessions=1, step_sessions=2),
                fitness_constraints=FitnessConstraints(min_trades=1, min_profitable_windows=1),
                search_config=search_config,
            )

            self.assertEqual(first.generated_candidates, second.generated_candidates)
            self.assertEqual(
                [trial["parameters"] for trial in first.trial_results],
                [trial["parameters"] for trial in second.trial_results],
            )
            self.assertEqual(
                first.winning_candidate.strategy_spec,
                second.winning_candidate.strategy_spec,
            )
            self.assertEqual(
                first.winning_candidate.objective_value,
                second.winning_candidate.objective_value,
            )

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
            self.assertEqual(len(result.rejected_candidates), 5)

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

    def test_deterministic_bounded_search_winner_uses_training_window_selection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dataset_path = _write_selection_leakage_dataset(Path(temp_dir) / "dataset")
            training_candidate = {
                **BASE_SPEC,
                "strategyId": "training-window-winner",
                "entryRules": [
                    {
                        "type": "feature_equals",
                        "feature": {"indicatorId": "STD;Supertrend", "name": "direction"},
                        "value": -1,
                        "side": "long",
                    }
                ],
            }
            scoring_candidate = {
                **BASE_SPEC,
                "strategyId": "scoring-window-only-winner",
                "entryRules": [
                    {
                        "type": "feature_equals",
                        "feature": {"indicatorId": "STD;Supertrend", "name": "direction"},
                        "value": 1,
                        "side": "long",
                    }
                ],
            }

            result = run_bounded_strategy_search(
                dataset_path=dataset_path,
                templates=[],
                proposed_candidates=[scoring_candidate, training_candidate],
                cost_model=CostModel(fixed_fee=0, slippage_ticks=0, tick_size=0.25),
                registry_path=Path(temp_dir) / "run-registry",
                walk_forward=WalkForwardConfig(training_sessions=1, scoring_sessions=1),
                fitness_constraints=FitnessConstraints(min_trades=0, min_profitable_windows=0),
                search_config=BoundedSearchConfig(method="deterministic", max_candidates=2),
            )

            self.assertIsNotNone(result.winning_candidate)
            self.assertEqual(result.winning_candidate.strategy_id, "walk-forward-selected-candidates")
            self.assertEqual(
                result.winning_candidate.strategy_spec["selectionStrategy"],
                "training-window-candidate-selection",
            )
            self.assertEqual(
                result.winning_candidate.result.selection_results[0].selected_strategy_id,
                "training-window-winner",
            )
            self.assertEqual(
                [candidate.strategy_id for candidate in result.evaluated_candidates],
                ["scoring-window-only-winner", "training-window-winner"],
            )

            search_record = json.loads(result.registry_record_path.read_text(encoding="utf-8"))
            self.assertEqual(search_record["ranking"], ["walk-forward-selected-candidates"])
            self.assertEqual(
                search_record["winningRun"]["trialParameters"]["selectedCandidates"][0]["strategyId"],
                "training-window-winner",
            )
            self.assertEqual(
                search_record["winningRun"]["strategySpec"]["selectionStrategy"],
                "training-window-candidate-selection",
            )
            self.assertTrue(search_record["bestRejectedCandidate"]["diagnosticOnly"])
            self.assertEqual(
                search_record["bestRejectedCandidate"]["strategyId"],
                "scoring-window-only-winner",
            )

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
            self.assertEqual([candidate.strategy_id for candidate in result.surviving_candidates], ["walk-forward-selected-candidates"])
            self.assertEqual(
                result.surviving_candidates[0].result.selection_results[0].selected_strategy_id,
                "valid-llm-proposal",
            )

    def test_optuna_style_proposals_cannot_bypass_strategy_spec_validation(self):
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
                search_config=BoundedSearchConfig(method="optuna_style", max_candidates=2, seed=3),
            )

            self.assertEqual(len(result.rejected_candidates), 1)
            self.assertEqual(result.rejected_candidates[0]["trialNumber"], 1)
            self.assertIn("schemaVersion must be 1", result.rejected_candidates[0]["error"])
            self.assertEqual([trial["state"] for trial in result.trial_results], ["rejected", "complete"])
            self.assertEqual([candidate.strategy_id for candidate in result.evaluated_candidates], ["valid-llm-proposal"])

            search_record = json.loads(result.registry_record_path.read_text(encoding="utf-8"))
            self.assertEqual(search_record["trials"][0]["state"], "rejected")
            self.assertIsNone(search_record["trials"][0]["objectiveValue"])
            self.assertEqual(search_record["trials"][1]["state"], "complete")
            self.assertEqual(search_record["evaluatedSpecs"][0]["strategyId"], "valid-llm-proposal")

    def test_proposed_candidates_cannot_exceed_remaining_search_budget(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dataset_path = _write_search_dataset(Path(temp_dir) / "dataset")
            template = StrategyTemplate(
                template_id="budget-template",
                base_spec=BASE_SPEC,
                choices={
                    "entryRules.0.value": [1, -1],
                    "sizing.quantity": [1],
                },
            )
            proposed = [
                {**BASE_SPEC, "strategyId": "proposed-over-budget-1"},
                {**BASE_SPEC, "strategyId": "proposed-over-budget-2"},
            ]

            result = run_bounded_strategy_search(
                dataset_path=dataset_path,
                templates=[template],
                proposed_candidates=proposed,
                cost_model=CostModel(fixed_fee=0, slippage_ticks=0, tick_size=0.25),
                registry_path=Path(temp_dir) / "run-registry",
                walk_forward=WalkForwardConfig(training_sessions=1, scoring_sessions=1),
                fitness_constraints=FitnessConstraints(min_trades=0, min_profitable_windows=0),
                search_config=BoundedSearchConfig(method="deterministic", max_candidates=2),
            )

            self.assertEqual(len(result.generated_candidates), 2)
            self.assertEqual(
                [candidate.strategy_id for candidate in result.evaluated_candidates],
                [
                    "budget-template-1",
                    "budget-template-2",
                ],
            )


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
        _direction_feature("feature-train-1", "2026-06-25T13:30:00.000Z", 1),
        _direction_feature("feature-score-1", "2026-06-26T13:30:00.000Z", 1),
        _direction_feature("feature-train-2", "2026-06-27T13:30:00.000Z", 1),
        _direction_feature("feature-score-2", "2026-06-28T13:30:00.000Z", 1),
    ]
    (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (path / "bars.json").write_text(json.dumps(bars), encoding="utf-8")
    (path / "features.json").write_text(json.dumps(features), encoding="utf-8")
    return path


def _write_selection_leakage_dataset(path: Path):
    path.mkdir()
    session_dates = ["2026-06-25", "2026-06-26"]
    bars_per_session = 3
    manifest = {
        "schemaVersion": 1,
        "datasetId": "selection-leakage-fixture",
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
    prices = [100, 100, 104, 100, 100, 120]
    bars = [
        _bar(_session_bar_time(session_date, bar_index).isoformat().replace("+00:00", ".000Z"), price)
        for session_index, session_date in enumerate(session_dates)
        for bar_index, price in enumerate(
            prices[session_index * bars_per_session : (session_index + 1) * bars_per_session]
        )
    ]
    features = [
        _direction_feature("training-only-negative", "2026-06-25T13:30:00.000Z", -1),
        _direction_feature("scoring-only-positive", "2026-06-26T13:30:00.000Z", 1),
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


def _fake_walk_forward_result(
    registry_path: Path,
    strategy_id: str,
    *,
    trade_count: int,
    survived: bool,
    selection_results=None,
):
    run_path = registry_path / f"mock-{strategy_id}"
    run_path.mkdir(parents=True, exist_ok=True)
    record_path = run_path / "run.json"
    record_path.write_text(json.dumps({"recordType": "Nautilus Walk-Forward Validation"}), encoding="utf-8")
    fitness = SimpleNamespace(
        survived=survived,
        score=1.0 if survived else None,
        rejection_reasons=[] if survived else ["min_trades"],
        survival_checks={},
        ranking_inputs={
            "outOfSampleSharpe": 1.0,
            "netPnl": 100,
            "grossPnl": 100,
            "totalCosts": 0,
            "tradeCount": trade_count,
            "maxDrawdown": 0,
        },
    )
    return SimpleNamespace(
        strategy_id=strategy_id,
        fitness=fitness,
        registry_record_path=record_path,
        training_window_results=[],
        window_results=[],
        selection_results=selection_results or [],
    )


if __name__ == "__main__":
    unittest.main()
