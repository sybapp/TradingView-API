"""Run and report the pinned ES RTH 5m LuxAlgo ICT/SMC validation path."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any, Dict, Iterable, Mapping, Sequence

from .search import (
    TRADE_COMPARISON_THRESHOLD,
    BoundedSearchConfig,
    LuxAlgoIctSmcLongTemplateConfig,
    create_luxalgo_ict_smc_long_strategy_template,
    run_bounded_strategy_search,
)
from .strategy import CostModel, FitnessConstraints, WalkForwardConfig


JsonObject = Dict[str, Any]
STRUCTURE_EVENTS = {"BOS", "CHOCH", "MSS"}


def run_luxalgo_validation_report(
    *,
    dataset_path: Path,
    registry_path: Path,
    report_path: Path,
    summary_path: Path,
    max_candidates: int = 4,
) -> JsonObject:
    cost_model = CostModel(fixed_fee=2.50, slippage_ticks=1, tick_size=0.25)
    walk_forward = WalkForwardConfig(training_sessions=1, scoring_sessions=1, step_sessions=1)
    fitness_constraints = FitnessConstraints(
        min_trades=1,
        max_drawdown=None,
        max_cost_to_gross_ratio=None,
        max_slippage_costs=None,
        min_profitable_windows=0,
        min_profitable_window_ratio=None,
    )
    template = create_luxalgo_ict_smc_long_strategy_template(
        LuxAlgoIctSmcLongTemplateConfig(
            event_types=("bos",),
            zone_types=("order_block",),
            zone_preferences=("nearest-any",),
            confirmation_modes=("touch",),
            max_bars_after_structure_event=(6,),
            cooldown_bars_after_exit=(0,),
            stop_loss_ticks=(8, 12),
            max_bars_in_trade=(24, 48),
        )
    )
    result = run_bounded_strategy_search(
        dataset_path=dataset_path,
        templates=[template],
        cost_model=cost_model,
        registry_path=registry_path,
        walk_forward=walk_forward,
        fitness_constraints=fitness_constraints,
        search_config=BoundedSearchConfig(method="deterministic", max_candidates=max_candidates, seed=0),
    )
    search_record = _read_json(result.registry_record_path)
    summary = build_report_summary(
        dataset_path=dataset_path,
        search_record_path=result.registry_record_path,
        search_record=search_record,
    )
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_markdown_report(summary), encoding="utf-8")
    return summary


def build_report_summary(
    *,
    dataset_path: Path,
    search_record_path: Path,
    search_record: Mapping[str, Any],
) -> JsonObject:
    dataset_summary = summarize_dataset(dataset_path)
    search_summary = summarize_search_record(search_record_path, search_record)
    return {
        "dataset": dataset_summary,
        "search": search_summary,
        "reproduction": {
            "datasetPath": str(dataset_path),
            "searchRecord": str(search_record_path),
            "runRegistryPath": str(search_record_path.parents[2]),
            "reportCommand": (
                "uv run python -m evaluator.nautilus_evaluator.luxalgo_report "
                f"{dataset_path} --run-registry {search_record_path.parents[2]}"
            ),
        },
    }


def summarize_dataset(dataset_path: Path) -> JsonObject:
    manifest = _read_json(dataset_path / "manifest.json")
    bars = _read_json(dataset_path / "bars.json")
    features = _read_json(dataset_path / "features.json")
    diagnostics_path = dataset_path / "derivation-diagnostics.json"
    diagnostics = _read_json(diagnostics_path) if diagnostics_path.exists() else {"rules": []}

    raw_structure_features = [
        feature for feature in features
        if feature.get("type") == "label" and str(feature.get("name", "")).upper() in STRUCTURE_EVENTS
    ]
    raw_liquidity_zones = [
        feature for feature in features
        if feature.get("type") == "box" and _liquidity_zone_kind(feature) is not None
    ]
    signal_counts = _count_by(
        feature.get("name", "")
        for feature in features
        if feature.get("type") == "signal"
    )
    diagnostic_counts = {
        str(rule.get("rule")): dict(rule.get("counts", {}))
        for rule in diagnostics.get("rules", [])
    }
    rejected_or_ambiguous = sum(
        int(counts.get(key, 0))
        for counts in diagnostic_counts.values()
        for key in ("unresolvedDirection", "invalidZoneGeometry", "missingProvenance")
    )

    return {
        "datasetId": manifest["datasetId"],
        "collectedAt": manifest["collectedAt"],
        "symbol": manifest["symbol"],
        "collection": manifest.get("collection", {}),
        "bar": manifest["bar"],
        "session": manifest["session"],
        "barCount": len(bars),
        "featureCount": len(features),
        "rawStructureFeatureCount": len(raw_structure_features),
        "bullishStructureEventSignalCount": sum(
            signal_counts.get(name, 0)
            for name in ("bullish_bos", "bullish_choch", "bullish_mss")
        ),
        "liquidityZoneCounts": _count_by(_liquidity_zone_kind(feature) for feature in raw_liquidity_zones),
        "zoneConfirmationCounts": {
            "touch": signal_counts.get("bullish_liquidity_zone_touch_entry", 0),
            "reclaim": signal_counts.get("bullish_liquidity_zone_reclaim_entry", 0),
        },
        "signalCounts": signal_counts,
        "diagnosticCounts": diagnostic_counts,
        "rejectedOrAmbiguousDerivationCount": rejected_or_ambiguous,
        "artifacts": {
            "manifest": str(dataset_path / "manifest.json"),
            "bars": str(dataset_path / "bars.json"),
            "features": str(dataset_path / "features.json"),
            "derivationDiagnostics": str(diagnostics_path) if diagnostics_path.exists() else None,
        },
    }


def summarize_search_record(search_record_path: Path, search_record: Mapping[str, Any]) -> JsonObject:
    evaluated = list(search_record.get("evaluatedSpecs", []))
    rejected = list(search_record.get("rejectedCandidates", []))
    winning = search_record.get("winningRun")
    best = winning or search_record.get("bestRejectedCandidate")
    best_trade_count = int((best or {}).get("totalTradeCount", 0))
    comparison_status = (
        "comparable"
        if winning and best_trade_count >= TRADE_COMPARISON_THRESHOLD
        else "smoke-only"
    )
    run_record = None
    run_record_payload = None
    if winning:
        artifact = winning.get("artifacts", {}).get("runRecord")
        if artifact:
            run_record_path = search_record_path.parent / artifact
            run_record = str(run_record_path)
            if run_record_path.exists():
                run_record_payload = _read_json(run_record_path)

    return {
        "status": search_record.get("status"),
        "recordType": search_record.get("recordType"),
        "searchRecord": str(search_record_path),
        "strategySpecOrSearchRecord": str(search_record_path),
        "walkForward": search_record.get("walkForward", {}),
        "costModel": search_record.get("costModel", {}),
        "fitnessConstraints": search_record.get("fitnessConstraints", {}),
        "generatedCandidateCount": len(search_record.get("generatedCandidates", [])),
        "evaluatedCandidateCount": len(evaluated),
        "survivingCandidateCount": len(search_record.get("survivingCandidates", [])),
        "rejectedCandidateCount": len(rejected),
        "candidateTradeCounts": [
            {
                "candidateId": candidate.get("candidateId"),
                "strategyId": candidate.get("strategyId"),
                "totalTradeCount": candidate.get("totalTradeCount", 0),
                "tradeComparison": candidate.get("tradeComparison", {}),
            }
            for candidate in evaluated
        ],
        "actualTradeCount": best_trade_count,
        "tradeComparisonThreshold": TRADE_COMPARISON_THRESHOLD,
        "comparisonStatus": comparison_status,
        "winningRun": winning,
        "runRegistryRecord": run_record,
        "sessionWindows": (run_record_payload or {}).get("walkForward", {}).get("windows", []),
        "perWindowResults": [
            {
                "windowId": result.get("windowId"),
                "startSessionDate": result.get("startSessionDate"),
                "endSessionDate": result.get("endSessionDate"),
                "sessionCount": result.get("sessionCount"),
                "barCount": result.get("barCount"),
                "tradeCount": result.get("tradeCount"),
                "netPnl": result.get("netPnl"),
            }
            for result in (run_record_payload or {}).get("perWindowResults", [])
        ],
        "reproducibilityInputs": search_record.get("reproducibilityInputs", {}),
    }


def render_markdown_report(summary: Mapping[str, Any]) -> str:
    dataset = summary["dataset"]
    search = summary["search"]
    liquidity_counts = dataset["liquidityZoneCounts"]
    diagnostics = dataset["diagnosticCounts"]
    candidate_lines = [
        (
            f"| {candidate['candidateId']} | {candidate['strategyId']} | "
            f"{candidate['totalTradeCount']} | "
            f"{candidate['tradeComparison'].get('status', 'unknown')} |"
        )
        for candidate in search["candidateTradeCounts"]
    ]
    diagnostics_lines = [
        f"- `{rule}`: {json.dumps(counts, sort_keys=True)}"
        for rule, counts in diagnostics.items()
    ]
    window_lines = [
        (
            f"| {window['windowId']} | "
            f"{window.get('training', {}).get('startSessionDate')} to {window.get('training', {}).get('endSessionDate')} "
            f"({window.get('training', {}).get('sessionCount')} session, {window.get('training', {}).get('barCount')} bars) | "
            f"{window.get('scoring', {}).get('startSessionDate')} to {window.get('scoring', {}).get('endSessionDate')} "
            f"({window.get('scoring', {}).get('sessionCount')} session, {window.get('scoring', {}).get('barCount')} bars) |"
        )
        for window in search["sessionWindows"]
    ]
    result_lines = [
        (
            f"| {result['windowId']} | {result['startSessionDate']} to {result['endSessionDate']} | "
            f"{result['sessionCount']} | {result['barCount']} | {result['tradeCount']} | {result['netPnl']} |"
        )
        for result in search["perWindowResults"]
    ]

    return "\n".join([
        "# ES RTH 5m LuxAlgo ICT/SMC Validation Report",
        "",
        "## Dataset",
        "",
        f"- Dataset id: `{dataset['datasetId']}`",
        f"- Collection: `{json.dumps(dataset['collection'], sort_keys=True)}`",
        f"- Bars: {dataset['barCount']} `{dataset['bar']['interval']}` RTH bars",
        f"- Features: {dataset['featureCount']}",
        f"- Bullish Structure Event signals: {dataset['bullishStructureEventSignalCount']}",
        f"- Liquidity Zones: order_block={liquidity_counts.get('order_block', 0)}, fair_value_gap={liquidity_counts.get('fair_value_gap', 0)}",
        f"- Zone confirmations: touch={dataset['zoneConfirmationCounts']['touch']}, reclaim={dataset['zoneConfirmationCounts']['reclaim']}",
        f"- Rejected or ambiguous derivations: {dataset['rejectedOrAmbiguousDerivationCount']}",
        "",
        "## Walk-Forward Validation",
        "",
        f"- Search status: `{search['status']}`",
        f"- Comparison status: `{search['comparisonStatus']}`",
        f"- Actual trade count: {search['actualTradeCount']}",
        f"- Trade comparison threshold: {search['tradeComparisonThreshold']}",
        f"- Walk-forward config: `{json.dumps(search['walkForward'].get('config', {}), sort_keys=True)}`",
        f"- Cost model: `{json.dumps(search['costModel'], sort_keys=True)}`",
        "",
        "| Candidate | Strategy | Trades | Comparison |",
        "| --- | --- | ---: | --- |",
        *(candidate_lines or ["| n/a | n/a | 0 | n/a |"]),
        "",
        "### Session Windows",
        "",
        "| Window | Training | Scoring |",
        "| --- | --- | --- |",
        *(window_lines or ["| n/a | n/a | n/a |"]),
        "",
        "### Scoring Results",
        "",
        "| Window | Sessions | Session Count | Bars | Trades | Net PnL |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
        *(result_lines or ["| n/a | n/a | 0 | 0 | 0 | 0 |"]),
        "",
        "## Derivation Diagnostics",
        "",
        *(diagnostics_lines or ["- No derivation diagnostics recorded."]),
        "",
        "## Reproduction",
        "",
        f"- Dataset path: `{summary['reproduction']['datasetPath']}`",
        f"- Search record: `{summary['reproduction']['searchRecord']}`",
        f"- Run Registry path: `{summary['reproduction']['runRegistryPath']}`",
        f"- Re-run: `{summary['reproduction']['reportCommand']}`",
        "",
    ])


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _count_by(values: Iterable[Any]) -> JsonObject:
    counts: JsonObject = {}
    for value in values:
        if value is None or value == "":
            continue
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _liquidity_zone_kind(feature: Mapping[str, Any]) -> str | None:
    text = f"{feature.get('name', '')} {feature.get('value', {}).get('text', '')}".lower()
    tokens = set(re.findall(r"[a-z0-9]+", text))
    if "fvg" in tokens or "fair_value_gap" in text or "fair value gap" in text:
        return "fair_value_gap"
    if "ob" in tokens or "order_block" in text or "order block" in text:
        return "order_block"
    return None


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_path", type=Path)
    parser.add_argument("--run-registry", type=Path, default=Path("runs/luxalgo-ict-smc-es-rth-5m"))
    parser.add_argument("--report", type=Path, default=Path("reports/es-rth-5m-luxalgo-ict-smc-validation.md"))
    parser.add_argument("--summary", type=Path, default=Path("reports/es-rth-5m-luxalgo-ict-smc-validation.json"))
    parser.add_argument("--max-candidates", type=int, default=4)
    args = parser.parse_args(argv)

    summary = run_luxalgo_validation_report(
        dataset_path=args.dataset_path,
        registry_path=args.run_registry,
        report_path=args.report,
        summary_path=args.summary,
        max_candidates=args.max_candidates,
    )
    print(json.dumps({
        "datasetId": summary["dataset"]["datasetId"],
        "comparisonStatus": summary["search"]["comparisonStatus"],
        "actualTradeCount": summary["search"]["actualTradeCount"],
        "report": str(args.report),
        "summary": str(args.summary),
        "searchRecord": summary["search"]["searchRecord"],
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
