"""Minimal Strategy Spec schema for the first evaluator slice."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional


JsonObject = Dict[str, Any]


@dataclass(frozen=True)
class FeatureEqualsEntryRule:
    indicator_id: str
    feature_type: Optional[str]
    name: str
    metadata: JsonObject
    max_bars_after_structure_event: Optional[int]
    zone_preference: Optional[str]
    value: Any
    side: str


@dataclass(frozen=True)
class FixedSizing:
    quantity: int


@dataclass(frozen=True)
class RiskControls:
    intraday_flat: bool
    flat_before_close_minutes: int
    stop_loss_ticks: int
    take_profit_ticks: int
    cooldown_bars_after_exit: int


@dataclass(frozen=True)
class Exits:
    max_bars_in_trade: Optional[int]
    reverse_signal_rules: List[FeatureEqualsEntryRule]


@dataclass(frozen=True)
class StrategySpec:
    raw: JsonObject
    strategy_id: str
    entry_rules: List[FeatureEqualsEntryRule]
    exits: Exits
    sizing: FixedSizing
    risk_controls: RiskControls


def validate_strategy_spec(value: Mapping[str, Any]) -> StrategySpec:
    errors: List[str] = []
    raw = dict(value)
    _reject_unknown_keys(
        raw,
        {
            "schemaVersion",
            "strategyId",
            "description",
            "parameters",
            "entryRules",
            "exits",
            "sizing",
            "riskControls",
            "tunableParameters",
        },
        "Strategy Spec",
        errors,
    )

    if raw.get("schemaVersion") != 1:
        errors.append("schemaVersion must be 1")

    strategy_id = raw.get("strategyId")
    if not isinstance(strategy_id, str) or not strategy_id:
        errors.append("strategyId must be a non-empty string")

    entry_rules = _parse_entry_rules(raw.get("entryRules"), errors)
    sizing = _parse_sizing(raw.get("sizing"), errors)
    risk_controls = _parse_risk_controls(raw.get("riskControls"), errors)

    exits = _parse_exits(raw.get("exits"), errors)

    tunable_parameters = raw.get("tunableParameters")
    if not isinstance(tunable_parameters, dict):
        errors.append("tunableParameters must be an object")

    parameters = raw.get("parameters")
    if not isinstance(parameters, dict):
        errors.append("parameters must be an object")

    if errors:
        raise ValueError("invalid Strategy Spec: " + "; ".join(errors))

    return StrategySpec(
        raw=raw,
        strategy_id=strategy_id,
        entry_rules=entry_rules,
        exits=exits,
        sizing=sizing,
        risk_controls=risk_controls,
    )


def _parse_entry_rules(value: Any, errors: List[str]) -> List[FeatureEqualsEntryRule]:
    if not isinstance(value, list) or not value:
        errors.append("entryRules must be a non-empty array")
        return []

    rules: List[FeatureEqualsEntryRule] = []
    for index, rule in enumerate(value):
        parsed = _parse_feature_equals_rule(rule, path=f"entryRules[{index}]", errors=errors)
        if parsed is not None:
            rules.append(parsed)
    return rules


def _parse_sizing(value: Any, errors: List[str]) -> FixedSizing:
    if not isinstance(value, dict):
        errors.append("sizing must be an object")
        return FixedSizing(quantity=0)
    _reject_unknown_keys(value, {"type", "quantity"}, "sizing", errors)
    if value.get("type") != "fixed":
        errors.append("sizing.type must be fixed")
    quantity = value.get("quantity")
    if not _is_positive_int(quantity):
        errors.append("sizing.quantity must be a positive integer")
        return FixedSizing(quantity=0)
    return FixedSizing(quantity=quantity)


def _parse_exits(value: Any, errors: List[str]) -> Exits:
    if not isinstance(value, dict):
        errors.append("exits must be an object")
        return Exits(max_bars_in_trade=None, reverse_signal_rules=[])

    _reject_unknown_keys(value, {"maxBarsInTrade", "reverseSignalRules"}, "exits", errors)
    max_bars = value.get("maxBarsInTrade")
    reverse_signal_rules = _parse_exit_rules(value.get("reverseSignalRules"), errors)
    if max_bars is None:
        return Exits(max_bars_in_trade=None, reverse_signal_rules=reverse_signal_rules)
    if not _is_positive_int(max_bars):
        errors.append("exits.maxBarsInTrade must be a positive integer")
        return Exits(max_bars_in_trade=None, reverse_signal_rules=reverse_signal_rules)
    return Exits(max_bars_in_trade=max_bars, reverse_signal_rules=reverse_signal_rules)


def _parse_exit_rules(value: Any, errors: List[str]) -> List[FeatureEqualsEntryRule]:
    if value is None:
        return []
    if not isinstance(value, list) or not value:
        errors.append("exits.reverseSignalRules must be a non-empty array when provided")
        return []

    rules: List[FeatureEqualsEntryRule] = []
    for index, rule in enumerate(value):
        parsed = _parse_feature_equals_rule(rule, path=f"exits.reverseSignalRules[{index}]", errors=errors)
        if parsed is not None:
            rules.append(parsed)
    return rules


def _parse_feature_equals_rule(
    rule: Any,
    *,
    path: str,
    errors: List[str],
) -> Optional[FeatureEqualsEntryRule]:
    if not isinstance(rule, dict):
        errors.append(f"{path} must be an object")
        return None
    _reject_unknown_keys(rule, {"type", "feature", "value", "side"}, path, errors)
    if rule.get("type") != "feature_equals":
        errors.append(f"{path}.type must be feature_equals")
    feature = rule.get("feature")
    if not isinstance(feature, dict):
        errors.append(f"{path}.feature must be an object")
        return None
    _reject_unknown_keys(
        feature,
        {
            "indicatorId",
            "type",
            "name",
            "metadata",
            "maxBarsAfterStructureEvent",
            "zonePreference",
        },
        f"{path}.feature",
        errors,
    )
    indicator_id = feature.get("indicatorId")
    feature_type = feature.get("type")
    name = feature.get("name")
    metadata = feature.get("metadata", {})
    max_bars_after_structure_event = feature.get("maxBarsAfterStructureEvent")
    zone_preference = feature.get("zonePreference")
    side = rule.get("side")
    if not isinstance(indicator_id, str) or not indicator_id:
        errors.append(f"{path}.feature.indicatorId must be a non-empty string")
    if feature_type is not None and (not isinstance(feature_type, str) or not feature_type):
        errors.append(f"{path}.feature.type must be a non-empty string when provided")
    if not isinstance(name, str) or not name:
        errors.append(f"{path}.feature.name must be a non-empty string")
    if not isinstance(metadata, dict):
        errors.append(f"{path}.feature.metadata must be an object when provided")
        metadata = {}
    if max_bars_after_structure_event is not None and not _is_non_negative_int(max_bars_after_structure_event):
        errors.append(f"{path}.feature.maxBarsAfterStructureEvent must be a non-negative integer when provided")
    if zone_preference is not None and zone_preference not in {"nearest-any", "prefer-OB", "prefer-FVG"}:
        errors.append(f"{path}.feature.zonePreference must be nearest-any, prefer-OB, or prefer-FVG when provided")
    if side != "long":
        errors.append(f"{path}.side must be long")
    if "value" not in rule:
        errors.append(f"{path}.value is required")
    if (
        isinstance(indicator_id, str)
        and isinstance(name, str)
        and (feature_type is None or isinstance(feature_type, str))
        and side == "long"
        and "value" in rule
    ):
        return FeatureEqualsEntryRule(
            indicator_id=indicator_id,
            feature_type=feature_type,
            name=name,
            metadata=dict(metadata),
            max_bars_after_structure_event=(
                max_bars_after_structure_event
                if isinstance(max_bars_after_structure_event, int)
                else None
            ),
            zone_preference=zone_preference if isinstance(zone_preference, str) else None,
            value=rule["value"],
            side=side,
        )
    return None


def _parse_risk_controls(value: Any, errors: List[str]) -> RiskControls:
    if not isinstance(value, dict):
        errors.append("riskControls must be an object")
        return RiskControls(False, 0, 0, 0, 0)
    _reject_unknown_keys(
        value,
        {
            "intradayFlat",
            "flatBeforeCloseMinutes",
            "stopLossTicks",
            "takeProfitTicks",
            "cooldownBarsAfterExit",
        },
        "riskControls",
        errors,
    )

    intraday_flat = value.get("intradayFlat")
    flat_before_close_minutes = value.get("flatBeforeCloseMinutes")
    stop_loss_ticks = value.get("stopLossTicks")
    take_profit_ticks = value.get("takeProfitTicks")
    cooldown_bars_after_exit = value.get("cooldownBarsAfterExit", 0)

    if intraday_flat is not True:
        errors.append("riskControls.intradayFlat must be true")
    if not _is_non_negative_int(flat_before_close_minutes):
        errors.append("riskControls.flatBeforeCloseMinutes must be a non-negative integer")
    if not _is_positive_int(stop_loss_ticks):
        errors.append("riskControls.stopLossTicks must be a positive integer")
    if not _is_positive_int(take_profit_ticks):
        errors.append("riskControls.takeProfitTicks must be a positive integer")
    if not _is_non_negative_int(cooldown_bars_after_exit):
        errors.append("riskControls.cooldownBarsAfterExit must be a non-negative integer")

    return RiskControls(
        intraday_flat=intraday_flat is True,
        flat_before_close_minutes=flat_before_close_minutes if isinstance(flat_before_close_minutes, int) else 0,
        stop_loss_ticks=stop_loss_ticks if isinstance(stop_loss_ticks, int) else 0,
        take_profit_ticks=take_profit_ticks if isinstance(take_profit_ticks, int) else 0,
        cooldown_bars_after_exit=(
            cooldown_bars_after_exit
            if isinstance(cooldown_bars_after_exit, int)
            else 0
        ),
    )


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and value > 0


def _is_non_negative_int(value: Any) -> bool:
    return isinstance(value, int) and value >= 0


def _reject_unknown_keys(
    value: Mapping[str, Any],
    allowed: set[str],
    path: str,
    errors: List[str],
) -> None:
    for key in sorted(set(value) - allowed):
        errors.append(f"{path}.{key} is unsupported")
