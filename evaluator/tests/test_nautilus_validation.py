from pathlib import Path
from datetime import datetime, timedelta, timezone
import json
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "evaluator"))

from nautilus_evaluator import (
    CostModel,
    run_nautilus_validation_backtest,
    run_walk_forward_candidate_selection_backtest,
    validate_strategy_spec,
)
from nautilus_evaluator import FitnessConstraints, WalkForwardConfig


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
            self.assertEqual(result.orders[-1].reason, "take-profit")
            self.assertEqual(result.orders[-1].execution_bar_time.isoformat(), "2026-06-25T15:50:00+00:00")
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

    def test_nautilus_validation_enforces_max_bars_in_trade(self):
        result = _run_tiny_validation(
            bars=[
                _bar("2026-06-25T13:30:00.000Z", 100, high=100.5, low=99.5),
                _bar("2026-06-25T13:35:00.000Z", 100, high=100.5, low=99.5),
                _bar("2026-06-25T13:40:00.000Z", 100, high=100.5, low=99.5),
            ],
            spec_overrides={"exits": {"maxBarsInTrade": 1}},
            risk_overrides={"stopLossTicks": 100, "takeProfitTicks": 100},
        )

        self.assertEqual([order.reason for order in result.orders], ["entry:feature_equals", "max-bars-in-trade"])
        self.assertEqual(result.orders[-1].execution_bar_time.isoformat(), "2026-06-25T13:40:00+00:00")
        self.assertEqual(result.position_quantity, 0)

    def test_nautilus_validation_enforces_stop_loss_ticks(self):
        result = _run_tiny_validation(
            bars=[
                _bar("2026-06-25T13:30:00.000Z", 100, high=100.5, low=99.5),
                _bar("2026-06-25T13:35:00.000Z", 100, high=100.5, low=99.5),
                _bar("2026-06-25T13:40:00.000Z", 100, high=100.25, low=98.75),
            ],
            risk_overrides={"stopLossTicks": 4, "takeProfitTicks": 100},
        )

        self.assertEqual([order.reason for order in result.orders], ["entry:feature_equals", "stop-loss"])
        self.assertEqual(result.orders[-1].execution_bar_time.isoformat(), "2026-06-25T13:40:00+00:00")
        self.assertEqual(result.position_quantity, 0)

    def test_nautilus_validation_enforces_take_profit_ticks(self):
        result = _run_tiny_validation(
            bars=[
                _bar("2026-06-25T13:30:00.000Z", 100, high=100.5, low=99.5),
                _bar("2026-06-25T13:35:00.000Z", 100, high=100.5, low=99.5),
                _bar("2026-06-25T13:40:00.000Z", 100, high=101.25, low=99.75),
            ],
            risk_overrides={"stopLossTicks": 100, "takeProfitTicks": 4},
        )

        self.assertEqual([order.reason for order in result.orders], ["entry:feature_equals", "take-profit"])
        self.assertEqual(result.orders[-1].execution_bar_time.isoformat(), "2026-06-25T13:40:00+00:00")
        self.assertEqual(result.position_quantity, 0)

    def test_nautilus_validation_enforces_flat_before_close_per_session(self):
        result = _run_tiny_validation(
            bars=[
                _bar("2026-06-25T13:30:00.000Z", 100, high=100.5, low=99.5),
                _bar("2026-06-25T13:35:00.000Z", 100, high=100.5, low=99.5),
                _bar("2026-06-25T13:40:00.000Z", 100, high=100.5, low=99.5),
            ],
            session_end="13:45",
            risk_overrides={"flatBeforeCloseMinutes": 5, "stopLossTicks": 100, "takeProfitTicks": 100},
        )

        self.assertEqual(
            [order.reason for order in result.orders],
            ["entry:feature_equals", "intraday-flat-before-close"],
        )
        self.assertEqual(result.orders[-1].execution_bar_time.isoformat(), "2026-06-25T13:40:00+00:00")
        self.assertEqual(result.position_quantity, 0)

    def test_strategy_spec_validation_rejects_unsupported_fields(self):
        invalid_spec = {
            **HAND_WRITTEN_SPEC,
            "riskControls": {
                **HAND_WRITTEN_SPEC["riskControls"],
                "trailingStopTicks": 8,
            },
        }

        with self.assertRaisesRegex(ValueError, "riskControls.trailingStopTicks is unsupported"):
            validate_strategy_spec(invalid_spec)

    def test_nautilus_validation_honors_signal_feature_selector_type(self):
        result = _run_tiny_validation(
            bars=[
                _bar("2026-06-25T13:30:00.000Z", 100, high=100, low=100),
                _bar("2026-06-25T13:35:00.000Z", 100, high=100, low=100),
                _bar("2026-06-25T13:40:00.000Z", 104, high=104, low=104),
                _bar("2026-06-25T13:45:00.000Z", 104, high=104, low=104),
            ],
            spec_overrides={
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
                ]
            },
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
            risk_overrides={"stopLossTicks": 100, "takeProfitTicks": 100},
            session_end="13:50",
        )

        self.assertEqual([order.reason for order in result.orders], ["entry:feature_equals", "intraday-flat-before-close"])
        self.assertEqual(result.orders[0].signal_bar_time.isoformat(), "2026-06-25T13:30:00+00:00")

    def test_nautilus_validation_does_not_execute_pending_entry_across_rth_sessions(self):
        result = _run_tiny_validation(
            bars=[
                _bar("2026-06-25T13:30:00.000Z", 100, high=100.5, low=99.5),
                _bar("2026-06-25T13:35:00.000Z", 100, high=100.5, low=99.5),
                _bar("2026-06-26T13:30:00.000Z", 101, high=101.5, low=100.5),
                _bar("2026-06-26T13:35:00.000Z", 101, high=101.5, low=100.5),
            ],
            features=[_direction_feature("feature-last-bar", "2026-06-25T13:35:00.000Z")],
            sessions=[
                _session("2026-06-25", "2026-06-25T13:30:00.000Z", "2026-06-25T13:35:00.000Z"),
                _session("2026-06-26", "2026-06-26T13:30:00.000Z", "2026-06-26T13:35:00.000Z"),
            ],
            risk_overrides={"stopLossTicks": 100, "takeProfitTicks": 100},
        )

        self.assertEqual(result.orders, [])

    def test_nautilus_validation_fails_fast_for_unsupported_slippage_mapping(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "supports only 0 or 1 slippage tick"):
                run_nautilus_validation_backtest(
                    dataset_path=FIXTURE_PATH,
                    strategy_spec=HAND_WRITTEN_SPEC,
                    cost_model=CostModel(fixed_fee=2.50, slippage_ticks=2, tick_size=0.25),
                    registry_path=Path(temp_dir) / "run-registry",
                )

    def test_walk_forward_candidate_selection_uses_training_window_not_scoring_features(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dataset_path = Path(temp_dir) / "dataset"
            _write_tiny_dataset(
                dataset_path,
                bars=[
                    _bar("2026-06-25T13:30:00.000Z", 100, high=100, low=100),
                    _bar("2026-06-25T13:35:00.000Z", 100, high=100, low=100),
                    _bar("2026-06-25T13:40:00.000Z", 104, high=104, low=104),
                    _bar("2026-06-26T13:30:00.000Z", 100, high=100, low=100),
                    _bar("2026-06-26T13:35:00.000Z", 100, high=100, low=100),
                    _bar("2026-06-26T13:40:00.000Z", 120, high=120, low=120),
                ],
                session_end="13:45",
                features=[
                    _direction_feature("training-only-negative", "2026-06-25T13:30:00.000Z", value=-1),
                    _direction_feature("scoring-only-positive", "2026-06-26T13:30:00.000Z", value=1),
                ],
                sessions=[
                    _session("2026-06-25", "2026-06-25T13:30:00.000Z", "2026-06-25T13:40:00.000Z", session_end="13:45"),
                    _session("2026-06-26", "2026-06-26T13:30:00.000Z", "2026-06-26T13:40:00.000Z", session_end="13:45"),
                ],
            )
            positive_scoring_candidate = _strategy_spec(
                spec_overrides={
                    "strategyId": "candidate-positive-scoring-only",
                    "entryRules": [
                        {
                            "type": "feature_equals",
                            "feature": {"indicatorId": "STD;Supertrend", "name": "direction"},
                            "value": 1,
                            "side": "long",
                        }
                    ],
                }
            )
            negative_training_candidate = _strategy_spec(
                spec_overrides={
                    "strategyId": "candidate-negative-training",
                    "entryRules": [
                        {
                            "type": "feature_equals",
                            "feature": {"indicatorId": "STD;Supertrend", "name": "direction"},
                            "value": -1,
                            "side": "long",
                        }
                    ],
                }
            )

            result = run_walk_forward_candidate_selection_backtest(
                dataset_path=dataset_path,
                candidate_specs=[positive_scoring_candidate, negative_training_candidate],
                cost_model=CostModel(fixed_fee=0, slippage_ticks=0, tick_size=0.25),
                registry_path=Path(temp_dir) / "run-registry",
                walk_forward=WalkForwardConfig(training_sessions=1, scoring_sessions=1),
                fitness_constraints=FitnessConstraints(min_trades=0, min_profitable_windows=0),
            )

            self.assertEqual(result.engine, "nautilus-trader-walk-forward-training-window-selection")
            self.assertEqual(result.selection_results[0].selected_strategy_id, "candidate-negative-training")
            self.assertEqual(result.training_window_results[0].net_pnl, 400)
            self.assertEqual(result.window_results[0].trade_count, 0)
            self.assertEqual(result.window_results[0].net_pnl, 0)

            registry_record = json.loads(result.registry_record_path.read_text(encoding="utf-8"))
            self.assertEqual(
                registry_record["searchConfiguration"]["type"],
                "training-window-candidate-selection",
            )
            selection_record = registry_record["trainingWindowSelection"][0]
            self.assertEqual(selection_record["selectedCandidate"]["strategyId"], "candidate-negative-training")
            self.assertEqual(selection_record["selectedCandidate"]["selectionRankingInputs"]["netPnl"], 400)
            self.assertEqual(
                [item["strategyId"] for item in selection_record["selectionInputs"]],
                ["candidate-positive-scoring-only", "candidate-negative-training"],
            )
            self.assertEqual(selection_record["selectionInputs"][0]["trainingResult"]["netPnl"], 0)
            self.assertEqual(registry_record["perWindowResults"][0]["netPnl"], 0)
            self.assertEqual(registry_record["finalRankingInputs"], registry_record["fitness"]["rankingInputs"])


def _run_tiny_validation(
    *,
    bars,
    spec_overrides=None,
    risk_overrides=None,
    session_end="16:00",
    features=None,
    sessions=None,
):
    with tempfile.TemporaryDirectory() as temp_dir:
        dataset_path = Path(temp_dir) / "dataset"
        _write_tiny_dataset(
            dataset_path,
            bars=bars,
            session_end=session_end,
            features=features,
            sessions=sessions,
        )
        return run_nautilus_validation_backtest(
            dataset_path=dataset_path,
            strategy_spec=_strategy_spec(spec_overrides=spec_overrides, risk_overrides=risk_overrides),
            cost_model=CostModel(fixed_fee=0, slippage_ticks=0, tick_size=0.25),
            registry_path=Path(temp_dir) / "run-registry",
        )


def _strategy_spec(spec_overrides=None, risk_overrides=None):
    spec = json.loads(json.dumps(HAND_WRITTEN_SPEC))
    spec["strategyId"] = "tiny-validation"
    spec["exits"] = {"maxBarsInTrade": 100}
    spec["riskControls"] = {
        **spec["riskControls"],
        "flatBeforeCloseMinutes": 5,
        "stopLossTicks": 100,
        "takeProfitTicks": 100,
        **(risk_overrides or {}),
    }
    for key, value in (spec_overrides or {}).items():
        spec[key] = value
    return spec


def _write_tiny_dataset(path: Path, *, bars, session_end, features=None, sessions=None):
    path.mkdir()
    manifest = {
        "schemaVersion": 1,
        "datasetId": "tiny-validation-fixture",
        "collectedAt": "2026-06-28T12:00:00.000Z",
        "source": "tradingview",
        "symbol": {"ticker": "CME_MINI:ES1!", "root": "ES", "assetClass": "equity_index_futures"},
        "session": {
            "name": "RTH",
            "timezone": "UTC",
            "start": "13:30",
            "end": session_end,
            "sessions": sessions
            or [_session("2026-06-25", bars[0]["time"], bars[-1]["time"], session_end=session_end)],
        },
        "bar": {"interval": "5m", "priceScale": 100, "volumeUnit": "contracts"},
        "contract": {"type": "continuous_futures", "continuous": True},
        "indicators": [],
    }
    (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (path / "bars.json").write_text(json.dumps(bars), encoding="utf-8")
    (path / "features.json").write_text(
        json.dumps(features or [_direction_feature("feature-entry", "2026-06-25T13:30:00.000Z")]),
        encoding="utf-8",
    )


def _bar(timestamp: str, open_price: float, *, high: float, low: float):
    return {
        "time": timestamp,
        "open": open_price,
        "high": high,
        "low": low,
        "close": open_price,
        "volume": 100,
    }


def _direction_feature(feature_id: str, timestamp: str, value=1):
    return _typed_feature(
        feature_id,
        timestamp,
        indicator_id="STD;Supertrend",
        feature_type="plot",
        name="direction",
        value=value,
    )


def _typed_feature(feature_id: str, timestamp: str, *, indicator_id: str, feature_type: str, name: str, value):
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
    }


def _session(session_id: str, first_bar_time: str, last_bar_time: str, session_end="16:00"):
    first = datetime.fromisoformat(first_bar_time.replace("Z", "+00:00"))
    session_date = first.date()
    end_hour, end_minute = [int(part) for part in session_end.split(":")]
    flat_before_close = datetime(
        session_date.year,
        session_date.month,
        session_date.day,
        end_hour,
        end_minute,
        tzinfo=timezone.utc,
    ) - timedelta(minutes=5)
    return {
        "id": session_id,
        "firstBarTime": first_bar_time,
        "lastBarTime": last_bar_time,
        "flatBeforeCloseTime": flat_before_close.isoformat().replace("+00:00", ".000Z"),
        "barCount": 1,
    }


if __name__ == "__main__":
    unittest.main()
