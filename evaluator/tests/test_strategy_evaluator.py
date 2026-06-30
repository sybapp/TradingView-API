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
    def test_runs_hand_written_strategy_spec_through_non_authoritative_replay(self):
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

            self.assertEqual(result.registry_record_path, Path())
            self.assertFalse(any(registry_path.rglob("run.json")))

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

    def test_strategy_spec_validation_accepts_optional_feature_type(self):
        spec = {
            **HAND_WRITTEN_SPEC,
            "entryRules": [
                {
                    "type": "feature_equals",
                    "feature": {
                        "indicatorId": "STD;LuxAlgo",
                        "type": "signal",
                        "name": "bullish_bos",
                    },
                    "value": True,
                    "side": "long",
                }
            ],
        }

        validated = validate_strategy_spec(spec)

        self.assertEqual(validated.entry_rules[0].indicator_id, "STD;LuxAlgo")
        self.assertEqual(validated.entry_rules[0].feature_type, "signal")
        self.assertEqual(validated.entry_rules[0].name, "bullish_bos")
        self.assertTrue(validated.entry_rules[0].value)

    def test_strategy_spec_validation_accepts_reverse_signal_exit_rules(self):
        spec = {
            **HAND_WRITTEN_SPEC,
            "exits": {
                "maxBarsInTrade": 100,
                "reverseSignalRules": [
                    {
                        "type": "feature_equals",
                        "feature": {
                            "indicatorId": "LUX;ICT_SMC",
                            "type": "signal",
                            "name": "bearish_bos",
                        },
                        "value": True,
                        "side": "long",
                    }
                ],
            },
        }

        validated = validate_strategy_spec(spec)

        self.assertEqual(validated.exits.max_bars_in_trade, 100)
        self.assertEqual(len(validated.exits.reverse_signal_rules), 1)
        self.assertEqual(validated.exits.reverse_signal_rules[0].name, "bearish_bos")
        self.assertEqual(validated.exits.reverse_signal_rules[0].feature_type, "signal")

    def test_strategy_replay_honors_signal_feature_type_without_breaking_legacy_specs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dataset_path = _write_walk_forward_dataset(
                Path(temp_dir) / "dataset",
                features=[
                    _typed_feature(
                        "raw-plot",
                        "2026-06-25T13:30:00.000Z",
                        indicator_id="LUX;ICT_SMC",
                        feature_type="plot",
                        name="bullish_bos",
                        value=True,
                    ),
                    _typed_feature(
                        "signal-entry",
                        "2026-06-25T13:30:00.000Z",
                        indicator_id="LUX;ICT_SMC",
                        feature_type="signal",
                        name="bullish_bos",
                        value=True,
                    ),
                ],
                prices=[100, 100, 104],
                session_dates=["2026-06-25"],
            )
            signal_only_spec = {
                **HAND_WRITTEN_SPEC,
                "strategyId": "fixture-signal-long",
                "entryRules": [
                    {
                        "type": "feature_equals",
                        "feature": {
                            "indicatorId": "LUX;ICT_SMC",
                            "type": "signal",
                            "name": "bullish_bos",
                        },
                        "value": True,
                        "side": "long",
                    }
                ],
            }

            result = run_strategy_backtest(
                dataset_path=dataset_path,
                strategy_spec=signal_only_spec,
                cost_model=CostModel(fixed_fee=0, slippage_ticks=0, tick_size=0.25),
                registry_path=Path(temp_dir) / "run-registry",
            )

            self.assertEqual([order.reason for order in result.orders], ["entry:feature_equals", "intraday-flat-before-close"])
            self.assertEqual(result.orders[0].signal_bar_time.isoformat(), "2026-06-25T13:30:00+00:00")
            self.assertEqual(result.orders[0].execution_bar_time.isoformat(), "2026-06-25T13:35:00+00:00")

    def test_strategy_replay_exits_long_on_reverse_signal_next_bar(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dataset_path = _write_walk_forward_dataset(
                Path(temp_dir) / "dataset",
                features=[
                    _typed_feature(
                        "signal-entry",
                        "2026-06-25T13:30:00.000Z",
                        indicator_id="LUX;ICT_SMC",
                        feature_type="signal",
                        name="bullish_bos",
                        value=True,
                    ),
                    _typed_feature(
                        "signal-exit",
                        "2026-06-25T13:35:00.000Z",
                        indicator_id="LUX;ICT_SMC",
                        feature_type="signal",
                        name="bearish_choch",
                        value=True,
                    ),
                ],
                prices=[100, 100, 104, 104],
                session_dates=["2026-06-25"],
                session_end="13:50",
            )
            reverse_exit_spec = {
                **HAND_WRITTEN_SPEC,
                "strategyId": "fixture-reverse-exit-long",
                "entryRules": [
                    {
                        "type": "feature_equals",
                        "feature": {
                            "indicatorId": "LUX;ICT_SMC",
                            "type": "signal",
                            "name": "bullish_bos",
                        },
                        "value": True,
                        "side": "long",
                    }
                ],
                "exits": {
                    "maxBarsInTrade": 100,
                    "reverseSignalRules": [
                        {
                            "type": "feature_equals",
                            "feature": {
                                "indicatorId": "LUX;ICT_SMC",
                                "type": "signal",
                                "name": "bearish_choch",
                            },
                            "value": True,
                            "side": "long",
                        }
                    ],
                },
                "riskControls": {
                    **HAND_WRITTEN_SPEC["riskControls"],
                    "stopLossTicks": 100,
                    "takeProfitTicks": 100,
                },
            }

            result = run_strategy_backtest(
                dataset_path=dataset_path,
                strategy_spec=reverse_exit_spec,
                cost_model=CostModel(fixed_fee=0, slippage_ticks=0, tick_size=0.25),
                registry_path=Path(temp_dir) / "run-registry",
            )

            self.assertEqual([order.reason for order in result.orders], ["entry:feature_equals", "exit:reverse-signal"])
            self.assertEqual(result.orders[-1].signal_bar_time.isoformat(), "2026-06-25T13:35:00+00:00")
            self.assertEqual(result.orders[-1].execution_bar_time.isoformat(), "2026-06-25T13:40:00+00:00")

    def test_strategy_replay_requires_all_entry_signal_rules(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dataset_path = _write_walk_forward_dataset(
                Path(temp_dir) / "dataset",
                features=[
                    _typed_feature(
                        "structure-entry",
                        "2026-06-25T13:30:00.000Z",
                        indicator_id="LUX;ICT_SMC",
                        feature_type="signal",
                        name="bullish_bos",
                        value=True,
                    ),
                    _typed_feature(
                        "zone-entry",
                        "2026-06-25T13:35:00.000Z",
                        indicator_id="LUX;ICT_SMC",
                        feature_type="signal",
                        name="bullish_liquidity_zone_touch_entry",
                        value=True,
                    ),
                ],
                prices=[100, 100, 104, 104],
                session_dates=["2026-06-25"],
                session_end="13:50",
            )
            conjunctive_entry_spec = {
                **HAND_WRITTEN_SPEC,
                "strategyId": "fixture-conjunctive-signal-entry",
                "entryRules": [
                    {
                        "type": "feature_equals",
                        "feature": {
                            "indicatorId": "LUX;ICT_SMC",
                            "type": "signal",
                            "name": "bullish_bos",
                        },
                        "value": True,
                        "side": "long",
                    },
                    {
                        "type": "feature_equals",
                        "feature": {
                            "indicatorId": "LUX;ICT_SMC",
                            "type": "signal",
                            "name": "bullish_liquidity_zone_touch_entry",
                        },
                        "value": True,
                        "side": "long",
                    },
                ],
                "riskControls": {
                    **HAND_WRITTEN_SPEC["riskControls"],
                    "stopLossTicks": 100,
                    "takeProfitTicks": 100,
                },
            }

            result = run_strategy_backtest(
                dataset_path=dataset_path,
                strategy_spec=conjunctive_entry_spec,
                cost_model=CostModel(fixed_fee=0, slippage_ticks=0, tick_size=0.25),
                registry_path=Path(temp_dir) / "run-registry",
            )

            self.assertEqual(result.orders[0].signal_bar_time.isoformat(), "2026-06-25T13:35:00+00:00")
            self.assertEqual(result.orders[0].execution_bar_time.isoformat(), "2026-06-25T13:40:00+00:00")

    def test_strategy_replay_honors_luxalgo_signal_provenance_selectors(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dataset_path = _write_walk_forward_dataset(
                Path(temp_dir) / "dataset",
                features=[
                    _typed_feature(
                        "structure-entry",
                        "2026-06-25T13:30:00.000Z",
                        indicator_id="LUX;ICT_SMC",
                        feature_type="signal",
                        name="bullish_bos",
                        value=True,
                    ),
                    _typed_feature(
                        "zone-entry",
                        "2026-06-25T13:40:00.000Z",
                        indicator_id="LUX;ICT_SMC",
                        feature_type="signal",
                        name="bullish_liquidity_zone_touch_entry",
                        value=True,
                        metadata={
                            "provenance": {
                                "structureEvent": {"availabilityTime": "2026-06-25T13:30:00.000Z"},
                                "selectedZone": {"kind": "order_block"},
                            }
                        },
                    ),
                ],
                prices=[100, 100, 100, 100, 100],
                session_dates=["2026-06-25"],
                session_end="14:00",
            )
            spec = {
                **HAND_WRITTEN_SPEC,
                "strategyId": "fixture-luxalgo-provenance-selector",
                "entryRules": [
                    {
                        "type": "feature_equals",
                        "feature": {
                            "indicatorId": "LUX;ICT_SMC",
                            "type": "signal",
                            "name": "bullish_bos",
                        },
                        "value": True,
                        "side": "long",
                    },
                    {
                        "type": "feature_equals",
                        "feature": {
                            "indicatorId": "LUX;ICT_SMC",
                            "type": "signal",
                            "name": "bullish_liquidity_zone_touch_entry",
                            "metadata": {"provenance": {"selectedZone": {"kind": "order_block"}}},
                            "maxBarsAfterStructureEvent": 2,
                        },
                        "value": True,
                        "side": "long",
                    },
                ],
                "exits": {"maxBarsInTrade": 1},
                "riskControls": {
                    **HAND_WRITTEN_SPEC["riskControls"],
                    "stopLossTicks": 100,
                    "takeProfitTicks": 100,
                },
            }

            result = run_strategy_backtest(
                dataset_path=dataset_path,
                strategy_spec=spec,
                cost_model=CostModel(fixed_fee=0, slippage_ticks=0, tick_size=0.25),
                registry_path=Path(temp_dir) / "run-registry",
            )

            self.assertEqual(result.orders[0].signal_bar_time.isoformat(), "2026-06-25T13:40:00+00:00")

            blocked = run_strategy_backtest(
                dataset_path=dataset_path,
                strategy_spec={
                    **spec,
                    "strategyId": "fixture-luxalgo-provenance-selector-blocked",
                    "entryRules": [
                        spec["entryRules"][0],
                        {
                            **spec["entryRules"][1],
                            "feature": {
                                **spec["entryRules"][1]["feature"],
                                "zonePreference": "prefer-FVG",
                            },
                        },
                    ],
                },
                cost_model=CostModel(fixed_fee=0, slippage_ticks=0, tick_size=0.25),
                registry_path=Path(temp_dir) / "run-registry",
            )
            self.assertEqual(blocked.orders, [])

    def test_strategy_replay_rejects_zone_signal_before_structure_provenance(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dataset_path = _write_walk_forward_dataset(
                Path(temp_dir) / "dataset",
                features=[
                    _typed_feature(
                        "structure-entry",
                        "2026-06-25T13:40:00.000Z",
                        indicator_id="LUX;ICT_SMC",
                        feature_type="signal",
                        name="bullish_bos",
                        value=True,
                    ),
                    _typed_feature(
                        "zone-entry",
                        "2026-06-25T13:35:00.000Z",
                        indicator_id="LUX;ICT_SMC",
                        feature_type="signal",
                        name="bullish_liquidity_zone_touch_entry",
                        value=True,
                        metadata={
                            "provenance": {
                                "structureEvent": {"availabilityTime": "2026-06-25T13:40:00.000Z"},
                                "selectedZone": {"kind": "order_block"},
                            }
                        },
                    ),
                ],
                prices=[100, 100, 100, 100],
                session_dates=["2026-06-25"],
                session_end="13:55",
            )
            spec = {
                **HAND_WRITTEN_SPEC,
                "strategyId": "fixture-luxalgo-provenance-reversed-time",
                "entryRules": [
                    {
                        "type": "feature_equals",
                        "feature": {
                            "indicatorId": "LUX;ICT_SMC",
                            "type": "signal",
                            "name": "bullish_bos",
                        },
                        "value": True,
                        "side": "long",
                    },
                    {
                        "type": "feature_equals",
                        "feature": {
                            "indicatorId": "LUX;ICT_SMC",
                            "type": "signal",
                            "name": "bullish_liquidity_zone_touch_entry",
                            "metadata": {"provenance": {"selectedZone": {"kind": "order_block"}}},
                            "maxBarsAfterStructureEvent": 2,
                        },
                        "value": True,
                        "side": "long",
                    },
                ],
                "exits": {"maxBarsInTrade": 1},
                "riskControls": {
                    **HAND_WRITTEN_SPEC["riskControls"],
                    "stopLossTicks": 100,
                    "takeProfitTicks": 100,
                },
            }

            result = run_strategy_backtest(
                dataset_path=dataset_path,
                strategy_spec=spec,
                cost_model=CostModel(fixed_fee=0, slippage_ticks=0, tick_size=0.25),
                registry_path=Path(temp_dir) / "run-registry",
            )

            self.assertEqual(result.orders, [])

    def test_strategy_replay_honors_cooldown_bars_after_exit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dataset_path = _write_walk_forward_dataset(
                Path(temp_dir) / "dataset",
                features=[
                    _typed_feature(
                        "signal-entry",
                        "2026-06-25T13:30:00.000Z",
                        indicator_id="LUX;ICT_SMC",
                        feature_type="signal",
                        name="bullish_bos",
                        value=True,
                    )
                ],
                prices=[100, 100, 100, 100, 100, 100, 100, 100],
                session_dates=["2026-06-25"],
                session_end="14:15",
            )
            cooldown_spec = {
                **HAND_WRITTEN_SPEC,
                "strategyId": "fixture-cooldown-long",
                "entryRules": [
                    {
                        "type": "feature_equals",
                        "feature": {
                            "indicatorId": "LUX;ICT_SMC",
                            "type": "signal",
                            "name": "bullish_bos",
                        },
                        "value": True,
                        "side": "long",
                    }
                ],
                "exits": {"maxBarsInTrade": 1},
                "riskControls": {
                    **HAND_WRITTEN_SPEC["riskControls"],
                    "stopLossTicks": 100,
                    "takeProfitTicks": 100,
                    "cooldownBarsAfterExit": 2,
                },
            }

            result = run_strategy_backtest(
                dataset_path=dataset_path,
                strategy_spec=cooldown_spec,
                cost_model=CostModel(fixed_fee=0, slippage_ticks=0, tick_size=0.25),
                registry_path=Path(temp_dir) / "run-registry",
            )

            self.assertEqual(
                [order.signal_bar_time.isoformat() for order in result.orders if order.side == "buy"],
                [
                    "2026-06-25T13:30:00+00:00",
                    "2026-06-25T13:55:00+00:00",
                ],
            )

    def test_strategy_replay_prioritizes_stop_loss_over_reverse_signal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dataset_path = _write_walk_forward_dataset(
                Path(temp_dir) / "dataset",
                features=[
                    _typed_feature(
                        "signal-entry",
                        "2026-06-25T13:30:00.000Z",
                        indicator_id="LUX;ICT_SMC",
                        feature_type="signal",
                        name="bullish_bos",
                        value=True,
                    ),
                    _typed_feature(
                        "signal-exit",
                        "2026-06-25T13:35:00.000Z",
                        indicator_id="LUX;ICT_SMC",
                        feature_type="signal",
                        name="bearish_mss",
                        value=True,
                    ),
                ],
                prices=[100, 100, 97, 97],
                session_dates=["2026-06-25"],
                session_end="13:50",
            )
            reverse_exit_spec = {
                **HAND_WRITTEN_SPEC,
                "strategyId": "fixture-stop-priority-long",
                "entryRules": [
                    {
                        "type": "feature_equals",
                        "feature": {
                            "indicatorId": "LUX;ICT_SMC",
                            "type": "signal",
                            "name": "bullish_bos",
                        },
                        "value": True,
                        "side": "long",
                    }
                ],
                "exits": {
                    "maxBarsInTrade": 100,
                    "reverseSignalRules": [
                        {
                            "type": "feature_equals",
                            "feature": {
                                "indicatorId": "LUX;ICT_SMC",
                                "type": "signal",
                                "name": "bearish_mss",
                            },
                            "value": True,
                            "side": "long",
                        }
                    ],
                },
                "riskControls": {
                    **HAND_WRITTEN_SPEC["riskControls"],
                    "stopLossTicks": 4,
                    "takeProfitTicks": 100,
                },
            }

            result = run_strategy_backtest(
                dataset_path=dataset_path,
                strategy_spec=reverse_exit_spec,
                cost_model=CostModel(fixed_fee=0, slippage_ticks=0, tick_size=0.25),
                registry_path=Path(temp_dir) / "run-registry",
            )

            self.assertEqual([order.reason for order in result.orders], ["entry:feature_equals", "stop-loss"])

    def test_strategy_replay_uses_max_bars_when_no_reverse_signal_arrives(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dataset_path = _write_walk_forward_dataset(
                Path(temp_dir) / "dataset",
                features=[
                    _typed_feature(
                        "signal-entry",
                        "2026-06-25T13:30:00.000Z",
                        indicator_id="LUX;ICT_SMC",
                        feature_type="signal",
                        name="bullish_bos",
                        value=True,
                    )
                ],
                prices=[100, 100, 100],
                session_dates=["2026-06-25"],
                session_end="13:50",
            )
            reverse_exit_spec = {
                **HAND_WRITTEN_SPEC,
                "strategyId": "fixture-max-bars-fallback-long",
                "entryRules": [
                    {
                        "type": "feature_equals",
                        "feature": {
                            "indicatorId": "LUX;ICT_SMC",
                            "type": "signal",
                            "name": "bullish_bos",
                        },
                        "value": True,
                        "side": "long",
                    }
                ],
                "exits": {
                    "maxBarsInTrade": 1,
                    "reverseSignalRules": [
                        {
                            "type": "feature_equals",
                            "feature": {
                                "indicatorId": "LUX;ICT_SMC",
                                "type": "signal",
                                "name": "bearish_bos",
                            },
                            "value": True,
                            "side": "long",
                        }
                    ],
                },
                "riskControls": {
                    **HAND_WRITTEN_SPEC["riskControls"],
                    "stopLossTicks": 100,
                    "takeProfitTicks": 100,
                },
            }

            result = run_strategy_backtest(
                dataset_path=dataset_path,
                strategy_spec=reverse_exit_spec,
                cost_model=CostModel(fixed_fee=0, slippage_ticks=0, tick_size=0.25),
                registry_path=Path(temp_dir) / "run-registry",
            )

            self.assertEqual([order.reason for order in result.orders], ["entry:feature_equals", "max-bars-in-trade"])

    def test_strategy_replay_uses_intraday_flat_when_reverse_signal_never_arrives(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dataset_path = _write_walk_forward_dataset(
                Path(temp_dir) / "dataset",
                features=[
                    _typed_feature(
                        "signal-entry",
                        "2026-06-25T13:30:00.000Z",
                        indicator_id="LUX;ICT_SMC",
                        feature_type="signal",
                        name="bullish_bos",
                        value=True,
                    )
                ],
                prices=[100, 100, 100, 100],
                session_dates=["2026-06-25"],
            )
            reverse_exit_spec = {
                **HAND_WRITTEN_SPEC,
                "strategyId": "fixture-intraday-flat-fallback-long",
                "entryRules": [
                    {
                        "type": "feature_equals",
                        "feature": {
                            "indicatorId": "LUX;ICT_SMC",
                            "type": "signal",
                            "name": "bullish_bos",
                        },
                        "value": True,
                        "side": "long",
                    }
                ],
                "exits": {
                    "maxBarsInTrade": 100,
                    "reverseSignalRules": [
                        {
                            "type": "feature_equals",
                            "feature": {
                                "indicatorId": "LUX;ICT_SMC",
                                "type": "signal",
                                "name": "bearish_bos",
                            },
                            "value": True,
                            "side": "long",
                        }
                    ],
                },
                "riskControls": {
                    **HAND_WRITTEN_SPEC["riskControls"],
                    "flatBeforeCloseMinutes": 5,
                    "stopLossTicks": 100,
                    "takeProfitTicks": 100,
                },
            }

            result = run_strategy_backtest(
                dataset_path=dataset_path,
                strategy_spec=reverse_exit_spec,
                cost_model=CostModel(fixed_fee=0, slippage_ticks=0, tick_size=0.25),
                registry_path=Path(temp_dir) / "run-registry",
            )

            self.assertEqual([order.reason for order in result.orders], ["entry:feature_equals", "intraday-flat-before-close"])

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
                walk_forward=WalkForwardConfig(training_sessions=1, scoring_sessions=1),
                fitness_constraints=FitnessConstraints(min_trades=0),
            )

            self.assertEqual(len(result.windows), 1)
            self.assertEqual(result.windows[0].training.start, "2026-06-25T13:30:00+00:00")
            self.assertEqual(result.windows[0].training.end, "2026-06-25T13:40:00+00:00")
            self.assertEqual(result.windows[0].training.start_session_date, "2026-06-25")
            self.assertEqual(result.windows[0].training.end_session_date, "2026-06-25")
            self.assertEqual(result.windows[0].scoring.start, "2026-06-26T13:30:00+00:00")
            self.assertEqual(result.windows[0].scoring.end, "2026-06-26T13:40:00+00:00")
            self.assertEqual(result.windows[0].scoring.start_session_date, "2026-06-26")
            self.assertEqual(result.windows[0].scoring.end_session_date, "2026-06-26")
            self.assertEqual(result.window_results[0].order_count, 0)
            self.assertEqual(result.window_results[0].net_pnl, 0)

    def test_walk_forward_config_can_create_multiple_scoring_windows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dataset_path = _write_walk_forward_dataset(
                Path(temp_dir) / "dataset",
                features=[],
                prices=[100, 100, 100, 100, 100, 100, 100, 100, 100],
                session_dates=["2026-06-25", "2026-06-26", "2026-06-29"],
            )

            result = run_walk_forward_backtest(
                dataset_path=dataset_path,
                strategy_spec=HAND_WRITTEN_SPEC,
                cost_model=CostModel(fixed_fee=0, slippage_ticks=0, tick_size=0.25),
                registry_path=Path(temp_dir) / "run-registry",
                walk_forward=WalkForwardConfig(training_sessions=1, scoring_sessions=1, step_sessions=1),
                fitness_constraints=FitnessConstraints(min_trades=0, min_profitable_windows=0),
            )

            self.assertEqual([window.window_id for window in result.windows], ["wf-1", "wf-2"])
            self.assertEqual(result.windows[0].training.end_index, 2)
            self.assertEqual(result.windows[0].scoring.start_index, 3)
            self.assertEqual(result.windows[0].scoring.end_index, 5)
            self.assertEqual(result.windows[1].training.start_index, 3)
            self.assertEqual(result.windows[1].scoring.start_index, 6)
            self.assertEqual(result.windows[1].scoring.end_index, 8)
            self.assertEqual(result.windows[0].training.bar_count, 3)
            self.assertEqual([window_result.order_count for window_result in result.window_results], [0, 0])

    def test_walk_forward_fitness_rejects_high_sharpe_candidate_that_fails_survival(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registry_path = Path(temp_dir) / "run-registry"
            dataset_path = _write_walk_forward_dataset(
                Path(temp_dir) / "dataset",
                prices=[100, 100, 100, 100, 100, 110, 100, 100, 100, 100, 100, 112],
                features=[
                    _direction_feature("feature-score-1", "2026-06-26T13:30:00.000Z"),
                    _direction_feature("feature-score-2", "2026-06-28T13:30:00.000Z"),
                ],
                session_dates=["2026-06-25", "2026-06-26", "2026-06-27", "2026-06-28"],
            )

            result = run_walk_forward_backtest(
                dataset_path=dataset_path,
                strategy_spec=HAND_WRITTEN_SPEC,
                cost_model=CostModel(fixed_fee=2.50, slippage_ticks=1, tick_size=0.25),
                registry_path=registry_path,
                walk_forward=WalkForwardConfig(training_sessions=1, scoring_sessions=1, step_sessions=2),
                fitness_constraints=FitnessConstraints(min_trades=3, max_drawdown=2000),
            )

            self.assertEqual([window_result.trade_count for window_result in result.window_results], [1, 1])
            self.assertGreater(result.fitness.ranking_inputs["outOfSampleSharpe"], 0)
            self.assertFalse(result.fitness.survived)
            self.assertEqual(result.fitness.rejection_reasons, ["min_trades"])
            self.assertIsNone(result.fitness.score)

            registry_record = json.loads(result.registry_record_path.read_text(encoding="utf-8"))
            self.assertEqual(registry_record["recordType"], "Nautilus Walk-Forward Validation")
            self.assertTrue(registry_record["authoritative"])
            self.assertEqual(registry_record["walkForward"]["config"]["trainingSessions"], 1)
            self.assertEqual(registry_record["walkForward"]["windows"][0]["scoring"]["startSessionDate"], "2026-06-26")
            self.assertEqual(registry_record["walkForward"]["windows"][0]["scoring"]["barCount"], 3)
            self.assertEqual(registry_record["trainingWindowResults"][0]["tradeCount"], 0)
            self.assertEqual(registry_record["perWindowResults"][0]["tradeCount"], 1)
            self.assertEqual(registry_record["perWindowResults"][0]["resultSummary"]["engine"], "nautilus-trader-backtest-engine")
            self.assertEqual(registry_record["perWindowResults"][0]["nautilusTrader"]["engine"], "BacktestEngine")
            scoring_window = registry_record["perWindowResults"][0]
            self.assertEqual(scoring_window["instrument"]["instrumentId"], "ESU6.GLBX")
            self.assertEqual(scoring_window["venue"]["name"], "GLBX")
            self.assertEqual(scoring_window["barType"]["value"], "ESU6.GLBX-5-MINUTE-LAST-EXTERNAL")
            self.assertEqual(
                scoring_window["costConfiguration"]["nautilusExecution"]["feeModel"]["class"],
                "PerContractFeeModel",
            )
            self.assertIn("pythonVersion", scoring_window["environment"])
            for artifact_key in ("ordersByWindow", "nautilusOrderFills", "nautilusPositions", "nautilusAccount"):
                self.assertTrue((result.registry_record_path.parent / scoring_window["artifacts"][artifact_key]).exists())
            self.assertEqual(registry_record["fitness"]["survivalChecks"]["minTrades"]["passed"], False)
            self.assertEqual(registry_record["fitness"]["rejectionReasons"], ["min_trades"])
            self.assertGreater(registry_record["fitness"]["rankingInputs"]["outOfSampleSharpe"], 0)


def _write_walk_forward_dataset(path: Path, features, prices=None, session_dates=None, session_end="13:45"):
    path.mkdir()
    prices = prices or [100, 100, 100, 100, 100, 110]
    session_dates = session_dates or ["2026-06-25", "2026-06-26"]
    if len(prices) % len(session_dates) != 0:
        raise ValueError("test fixture prices must divide evenly across sessions")
    bars_per_session = len(prices) // len(session_dates)
    manifest = {
        "schemaVersion": 1,
        "datasetId": "walk-forward-fixture",
        "collectedAt": "2026-06-28T12:00:00.000Z",
        "source": {"kind": "tradingview"},
        "symbol": {"ticker": "CME_MINI:ES1!"},
        "bar": {"interval": "5m", "priceScale": 100},
        "session": {
            "timezone": "UTC",
            "start": "13:30",
            "end": session_end,
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
    bars = [
        _bar(_session_bar_time(session_date, bar_index).isoformat().replace("+00:00", ".000Z"), price)
        for session_index, session_date in enumerate(session_dates)
        for bar_index, price in enumerate(
            prices[session_index * bars_per_session : (session_index + 1) * bars_per_session]
        )
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


def _direction_feature(feature_id: str, timestamp: str):
    return _typed_feature(
        feature_id,
        timestamp,
        indicator_id="STD;Supertrend",
        feature_type="plot",
        name="direction",
        value=1,
    )


def _typed_feature(
    feature_id: str,
    timestamp: str,
    *,
    indicator_id: str,
    feature_type: str,
    name: str,
    value,
    metadata=None,
):
    return {
        "id": feature_id,
        "source": "tradingview",
        "indicatorId": indicator_id,
        "type": feature_type,
        "name": name,
        "eventTime": timestamp,
        "availabilityTime": timestamp,
        "repaintingRisk": "confirmed",
        "value": value,
        **({"metadata": metadata} if metadata is not None else {}),
    }


if __name__ == "__main__":
    unittest.main()
