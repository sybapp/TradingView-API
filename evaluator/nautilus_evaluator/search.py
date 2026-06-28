"""Bounded Strategy Spec search on top of walk-forward evaluator results."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union
import copy
import hashlib
import itertools
import json
import random
import shutil

from .strategy import (
    CostModel,
    EVALUATOR_VERSION,
    FitnessConstraints,
    WalkForwardBacktestResult,
    WalkForwardConfig,
    run_walk_forward_backtest,
)
from .strategy_spec import validate_strategy_spec


JsonObject = Dict[str, Any]


@dataclass(frozen=True)
class StrategyTemplate:
    template_id: str
    base_spec: Mapping[str, Any]
    choices: Mapping[str, Sequence[Any]]


@dataclass(frozen=True)
class BoundedSearchConfig:
    method: str = "deterministic"
    max_candidates: int = 20
    seed: int = 0


@dataclass(frozen=True)
class EvaluatedSearchCandidate:
    candidate_id: str
    strategy_id: str
    strategy_spec: JsonObject
    result: WalkForwardBacktestResult
    optimizer_phase: str = "evaluation"

    @property
    def fitness(self):
        return self.result.fitness


@dataclass(frozen=True)
class StrategySearchResult:
    optimizer_config: JsonObject
    generated_candidates: List[JsonObject]
    evaluated_candidates: List[EvaluatedSearchCandidate]
    rejected_candidates: List[JsonObject]
    winning_candidate: EvaluatedSearchCandidate
    registry_record_path: Path


def generate_bounded_template_specs(
    templates: Sequence[StrategyTemplate],
    search_config: BoundedSearchConfig,
) -> List[JsonObject]:
    """Generate schema-valid Strategy Specs from bounded template choices."""
    _validate_search_config(search_config)
    candidates: List[JsonObject] = []
    for template in templates:
        template_candidates = _template_candidate_specs(template)
        if search_config.method == "random":
            template_candidates = _sample_candidates(template_candidates, search_config)
        elif search_config.method == "optuna_style":
            template_candidates = _optuna_style_candidates(template_candidates, search_config)
        elif search_config.method != "deterministic":
            raise ValueError("search_config.method must be deterministic, random, or optuna_style")

        for candidate in template_candidates:
            if len(candidates) >= search_config.max_candidates:
                break
            validate_strategy_spec(candidate)
            candidates.append(candidate)
        if len(candidates) >= search_config.max_candidates:
            break
    return candidates


def run_bounded_strategy_search(
    *,
    dataset_path: Union[str, Path],
    templates: Sequence[StrategyTemplate],
    cost_model: CostModel,
    registry_path: Union[str, Path],
    walk_forward: WalkForwardConfig,
    fitness_constraints: FitnessConstraints,
    search_config: BoundedSearchConfig,
    proposed_candidates: Optional[Sequence[Mapping[str, Any]]] = None,
) -> StrategySearchResult:
    """Evaluate bounded candidates through walk-forward validation and rank by Fitness Score."""
    if search_config.method == "optuna_style":
        generated_candidates, evaluated, rejected = _run_optuna_style_search(
            dataset_path=dataset_path,
            templates=templates,
            proposed_candidates=proposed_candidates or [],
            cost_model=cost_model,
            registry_path=registry_path,
            walk_forward=walk_forward,
            fitness_constraints=fitness_constraints,
            search_config=search_config,
        )
    else:
        generated_candidates = generate_bounded_template_specs(templates, search_config)
        generated_candidates.extend(dict(candidate) for candidate in (proposed_candidates or []))
        evaluated, rejected = _evaluate_candidates(
            dataset_path=dataset_path,
            candidates=generated_candidates,
            cost_model=cost_model,
            registry_path=registry_path,
            walk_forward=walk_forward,
            fitness_constraints=fitness_constraints,
        )

    if not evaluated:
        raise ValueError("bounded strategy search did not produce any schema-valid candidates")

    winning_candidate = _rank_candidates(evaluated)[0]
    record_path = _write_search_registry_record(
        dataset_path=Path(dataset_path),
        registry_path=Path(registry_path),
        search_config=search_config,
        cost_model=cost_model,
        walk_forward=walk_forward,
        fitness_constraints=fitness_constraints,
        generated_candidates=generated_candidates,
        evaluated_candidates=evaluated,
        rejected_candidates=rejected,
        winning_candidate=winning_candidate,
    )
    return StrategySearchResult(
        optimizer_config=_search_config_to_json(search_config),
        generated_candidates=generated_candidates,
        evaluated_candidates=evaluated,
        rejected_candidates=rejected,
        winning_candidate=winning_candidate,
        registry_record_path=record_path,
    )


def _evaluate_candidates(
    *,
    dataset_path: Union[str, Path],
    candidates: Sequence[Mapping[str, Any]],
    cost_model: CostModel,
    registry_path: Union[str, Path],
    walk_forward: WalkForwardConfig,
    fitness_constraints: FitnessConstraints,
    optimizer_phase: str = "evaluation",
) -> tuple[List[EvaluatedSearchCandidate], List[JsonObject]]:
    evaluated: List[EvaluatedSearchCandidate] = []
    rejected: List[JsonObject] = []
    for index, candidate in enumerate(candidates, start=1):
        try:
            spec = validate_strategy_spec(candidate)
        except ValueError as exc:
            rejected.append(
                {
                    "candidateId": candidate.get("strategyId", f"candidate-{index}"),
                    "source": _candidate_source(candidate),
                    "strategySpec": dict(candidate),
                    "error": str(exc),
                }
            )
            continue

        result = run_walk_forward_backtest(
            dataset_path=dataset_path,
            strategy_spec=spec.raw,
            cost_model=cost_model,
            registry_path=registry_path,
            walk_forward=walk_forward,
            fitness_constraints=fitness_constraints,
        )
        evaluated.append(
            EvaluatedSearchCandidate(
                candidate_id=f"candidate-{index}",
                strategy_id=spec.strategy_id,
                strategy_spec=spec.raw,
                result=result,
                optimizer_phase=optimizer_phase,
            )
        )
    return evaluated, rejected


def _run_optuna_style_search(
    *,
    dataset_path: Union[str, Path],
    templates: Sequence[StrategyTemplate],
    proposed_candidates: Sequence[Mapping[str, Any]],
    cost_model: CostModel,
    registry_path: Union[str, Path],
    walk_forward: WalkForwardConfig,
    fitness_constraints: FitnessConstraints,
    search_config: BoundedSearchConfig,
) -> tuple[List[JsonObject], List[EvaluatedSearchCandidate], List[JsonObject]]:
    _validate_search_config(search_config)
    template_candidates = _all_template_candidates(templates)
    trial_budget = min(search_config.max_candidates, len(template_candidates))
    exploration_count = max(1, trial_budget // 2) if trial_budget else 0
    exploratory_candidates = template_candidates[:exploration_count]
    evaluated, rejected = _evaluate_candidates(
        dataset_path=dataset_path,
        candidates=exploratory_candidates,
        cost_model=cost_model,
        registry_path=registry_path,
        walk_forward=walk_forward,
        fitness_constraints=fitness_constraints,
        optimizer_phase="exploration",
    )

    generated_candidates = list(exploratory_candidates)
    remaining_budget = search_config.max_candidates - len(generated_candidates)
    if remaining_budget > 0 and evaluated:
        best_spec = _rank_candidates(evaluated)[0].strategy_spec
        exploitation_candidates = _rank_unevaluated_candidates_for_exploitation(
            candidates=template_candidates[exploration_count:],
            best_spec=best_spec,
            seed=search_config.seed,
        )[:remaining_budget]
        exploitation_evaluated, exploitation_rejected = _evaluate_candidates(
            dataset_path=dataset_path,
            candidates=exploitation_candidates,
            cost_model=cost_model,
            registry_path=registry_path,
            walk_forward=walk_forward,
            fitness_constraints=fitness_constraints,
            optimizer_phase="exploitation",
        )
        generated_candidates.extend(exploitation_candidates)
        evaluated.extend(exploitation_evaluated)
        rejected.extend(exploitation_rejected)

    remaining_budget = search_config.max_candidates - len(generated_candidates)
    if remaining_budget > 0 and proposed_candidates:
        proposed = [dict(candidate) for candidate in proposed_candidates[:remaining_budget]]
        proposed_evaluated, proposed_rejected = _evaluate_candidates(
            dataset_path=dataset_path,
            candidates=proposed,
            cost_model=cost_model,
            registry_path=registry_path,
            walk_forward=walk_forward,
            fitness_constraints=fitness_constraints,
            optimizer_phase="proposed",
        )
        generated_candidates.extend(proposed)
        evaluated.extend(proposed_evaluated)
        rejected.extend(proposed_rejected)

    return generated_candidates, evaluated, rejected


def _all_template_candidates(templates: Sequence[StrategyTemplate]) -> List[JsonObject]:
    candidates: List[JsonObject] = []
    for template in templates:
        candidates.extend(_template_candidate_specs(template))
    return candidates


def _rank_unevaluated_candidates_for_exploitation(
    *,
    candidates: Sequence[JsonObject],
    best_spec: Mapping[str, Any],
    seed: int,
) -> List[JsonObject]:
    rng = random.Random(seed)
    decorated = [
        (
            _choice_similarity(candidate, best_spec),
            rng.random(),
            candidate,
        )
        for candidate in candidates
    ]
    decorated.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [candidate for _, _, candidate in decorated]


def _choice_similarity(candidate: Mapping[str, Any], best_spec: Mapping[str, Any]) -> int:
    parameters = candidate.get("parameters")
    best_parameters = best_spec.get("parameters")
    if not isinstance(parameters, dict) or not isinstance(best_parameters, dict):
        return 0
    return sum(
        1
        for key, value in parameters.items()
        if key not in ("templateChoiceIndex", "templateId") and best_parameters.get(key) == value
    )


def reproduce_search_winner(registry_record_path: Union[str, Path]) -> WalkForwardBacktestResult:
    """Re-run the winning Strategy Spec using only the search registry artifact."""
    record_path = Path(registry_record_path)
    record = json.loads(record_path.read_text(encoding="utf-8"))
    cost_model = record["costModel"]
    walk_forward = record["walkForward"]["config"]
    constraints = record["fitnessConstraints"]
    dataset_path = record_path.parent / record["dataset"]["artifacts"]["snapshot"]
    return run_walk_forward_backtest(
        dataset_path=dataset_path,
        strategy_spec=record["winningRun"]["strategySpec"],
        cost_model=CostModel(
            fixed_fee=cost_model["fixedFee"],
            slippage_ticks=cost_model["slippageTicks"],
            tick_size=cost_model["tickSize"],
        ),
        registry_path=record_path.parent / "reproduced-runs",
        walk_forward=WalkForwardConfig(
            training_bars=walk_forward["trainingBars"],
            scoring_bars=walk_forward["scoringBars"],
            step_bars=walk_forward["stepBars"],
        ),
        fitness_constraints=FitnessConstraints(
            min_trades=constraints["minTrades"],
            max_drawdown=constraints["maxDrawdown"],
            max_cost_to_gross_ratio=constraints["maxCostToGrossRatio"],
            max_slippage_costs=constraints["maxSlippageCosts"],
            min_profitable_windows=constraints["minProfitableWindows"],
            min_profitable_window_ratio=constraints["minProfitableWindowRatio"],
        ),
    )


def _template_candidate_specs(template: StrategyTemplate) -> List[JsonObject]:
    if not template.template_id:
        raise ValueError("template.template_id must be a non-empty string")
    if not template.choices:
        candidate = copy.deepcopy(dict(template.base_spec))
        candidate["strategyId"] = f"{template.template_id}-1"
        candidate.setdefault("parameters", {})
        candidate["parameters"]["templateId"] = template.template_id
        return [candidate]

    paths = list(template.choices.keys())
    value_sets = [list(template.choices[path]) for path in paths]
    if any(not values for values in value_sets):
        raise ValueError("template choices must contain at least one value per path")

    candidates: List[JsonObject] = []
    for index, values in enumerate(itertools.product(*value_sets), start=1):
        candidate = copy.deepcopy(dict(template.base_spec))
        candidate["strategyId"] = f"{template.template_id}-{index}"
        candidate.setdefault("parameters", {})
        candidate["parameters"]["templateId"] = template.template_id
        candidate["parameters"]["templateChoiceIndex"] = index
        for path, value in zip(paths, values):
            _set_path(candidate, path, value)
            candidate["parameters"][path.replace(".", "_")] = value
        candidates.append(candidate)
    return candidates


def _sample_candidates(candidates: List[JsonObject], search_config: BoundedSearchConfig) -> List[JsonObject]:
    rng = random.Random(search_config.seed)
    sampled = candidates[:]
    rng.shuffle(sampled)
    return sampled[: search_config.max_candidates]


def _optuna_style_candidates(candidates: List[JsonObject], search_config: BoundedSearchConfig) -> List[JsonObject]:
    if len(candidates) <= search_config.max_candidates:
        return candidates
    rng = random.Random(search_config.seed)
    exploratory_count = max(1, search_config.max_candidates // 2)
    exploratory = candidates[:exploratory_count]
    remaining = candidates[exploratory_count:]
    rng.shuffle(remaining)
    return (exploratory + remaining)[: search_config.max_candidates]


def _set_path(value: JsonObject, path: str, replacement: Any) -> None:
    target: Any = value
    parts = path.split(".")
    for part in parts[:-1]:
        target = target[int(part)] if part.isdigit() else target[part]
    final = parts[-1]
    if final.isdigit():
        target[int(final)] = replacement
    else:
        target[final] = replacement


def _rank_candidates(candidates: List[EvaluatedSearchCandidate]) -> List[EvaluatedSearchCandidate]:
    return sorted(candidates, key=_candidate_rank_key, reverse=True)


def _candidate_rank_key(candidate: EvaluatedSearchCandidate):
    fitness = candidate.fitness
    return (
        1 if fitness.survived else 0,
        fitness.score if fitness.score is not None else float("-inf"),
        fitness.ranking_inputs.get("netPnl", 0),
        fitness.ranking_inputs.get("tradeCount", 0),
    )


def _write_search_registry_record(
    *,
    dataset_path: Path,
    registry_path: Path,
    search_config: BoundedSearchConfig,
    cost_model: CostModel,
    walk_forward: WalkForwardConfig,
    fitness_constraints: FitnessConstraints,
    generated_candidates: List[JsonObject],
    evaluated_candidates: List[EvaluatedSearchCandidate],
    rejected_candidates: List[JsonObject],
    winning_candidate: EvaluatedSearchCandidate,
) -> Path:
    run_id = _search_run_id(
        dataset_path,
        search_config,
        cost_model,
        walk_forward,
        fitness_constraints,
        generated_candidates,
    )
    search_path = registry_path / "searches" / run_id
    search_path.mkdir(parents=True, exist_ok=True)
    dataset_record = _snapshot_dataset(search_path, dataset_path)
    winning_run_artifact = _copy_winning_run_artifacts(search_path, winning_candidate)
    record = {
        "runId": run_id,
        "recordType": "Evaluator Replay Search Helper",
        "authoritative": False,
        "evaluatorVersion": EVALUATOR_VERSION,
        "dataset": dataset_record,
        "optimizerConfig": _search_config_to_json(search_config),
        "costModel": _cost_model_to_json(cost_model),
        "walkForward": {"config": _walk_forward_config_to_json(walk_forward)},
        "fitnessConstraints": _fitness_constraints_to_json(fitness_constraints),
        "generatedCandidates": generated_candidates,
        "evaluatedSpecs": [_evaluated_candidate_to_json(candidate) for candidate in evaluated_candidates],
        "rejectedCandidates": rejected_candidates,
        "ranking": [candidate.strategy_id for candidate in _rank_candidates(evaluated_candidates)],
        "winningRun": {
            "candidateId": winning_candidate.candidate_id,
            "strategyId": winning_candidate.strategy_id,
            "strategySpec": winning_candidate.strategy_spec,
            "fitness": _fitness_to_json(winning_candidate.fitness),
            "sourceRunRecord": str(winning_candidate.result.registry_record_path),
            "artifacts": {"runRecord": winning_run_artifact},
        },
    }
    record_path = search_path / "search.json"
    record_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    return record_path


def _copy_winning_run_artifacts(search_path: Path, candidate: EvaluatedSearchCandidate) -> str:
    source = candidate.result.registry_record_path
    artifact_dir = search_path / "winning-run"
    artifact_dir.mkdir(exist_ok=True)
    destination = artifact_dir / "run.json"
    shutil.copyfile(source, destination)
    orders_source = source.parent / "orders-by-window.json"
    if orders_source.exists():
        shutil.copyfile(orders_source, artifact_dir / "orders-by-window.json")
    return "winning-run/run.json"


def _snapshot_dataset(search_path: Path, dataset_path: Path) -> JsonObject:
    manifest = json.loads((dataset_path / "manifest.json").read_text(encoding="utf-8"))
    artifact_dir = search_path / "dataset"
    shutil.copytree(dataset_path, artifact_dir, dirs_exist_ok=True)
    return {
        "datasetId": manifest["datasetId"],
        "path": str(dataset_path),
        "schemaVersion": manifest["schemaVersion"],
        "collectedAt": manifest["collectedAt"],
        "source": manifest["source"],
        "symbol": manifest["symbol"],
        "bar": manifest["bar"],
        "session": manifest["session"],
        "artifacts": {"snapshot": "dataset"},
    }


def _evaluated_candidate_to_json(candidate: EvaluatedSearchCandidate) -> JsonObject:
    return {
        "candidateId": candidate.candidate_id,
        "strategyId": candidate.strategy_id,
        "strategySpec": candidate.strategy_spec,
        "optimizerPhase": candidate.optimizer_phase,
        "registryRecord": str(candidate.result.registry_record_path),
        "fitness": _fitness_to_json(candidate.fitness),
    }


def _search_run_id(
    dataset_path: Path,
    search_config: BoundedSearchConfig,
    cost_model: CostModel,
    walk_forward: WalkForwardConfig,
    fitness_constraints: FitnessConstraints,
    candidates: List[JsonObject],
) -> str:
    payload = {
        "datasetPath": str(dataset_path),
        "searchConfig": asdict(search_config),
        "costModel": asdict(cost_model),
        "walkForward": asdict(walk_forward),
        "fitnessConstraints": asdict(fitness_constraints),
        "candidates": candidates,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return f"strategy-search-{digest[:12]}"


def _validate_search_config(search_config: BoundedSearchConfig) -> None:
    if search_config.max_candidates <= 0:
        raise ValueError("search_config.max_candidates must be positive")


def _candidate_source(candidate: Mapping[str, Any]) -> str:
    parameters = candidate.get("parameters")
    if isinstance(parameters, dict) and "templateId" in parameters:
        return "template"
    return "proposed"


def _search_config_to_json(search_config: BoundedSearchConfig) -> JsonObject:
    return {
        "method": search_config.method,
        "maxCandidates": search_config.max_candidates,
        "seed": search_config.seed,
    }


def _cost_model_to_json(cost_model: CostModel) -> JsonObject:
    return {
        "fixedFee": cost_model.fixed_fee,
        "slippageTicks": cost_model.slippage_ticks,
        "tickSize": cost_model.tick_size,
    }


def _walk_forward_config_to_json(config: WalkForwardConfig) -> JsonObject:
    return {
        "trainingBars": config.training_bars,
        "scoringBars": config.scoring_bars,
        "stepBars": config.step_bars if config.step_bars is not None else config.scoring_bars,
    }


def _fitness_constraints_to_json(constraints: FitnessConstraints) -> JsonObject:
    return {
        "minTrades": constraints.min_trades,
        "maxDrawdown": constraints.max_drawdown,
        "maxCostToGrossRatio": constraints.max_cost_to_gross_ratio,
        "maxSlippageCosts": constraints.max_slippage_costs,
        "minProfitableWindows": constraints.min_profitable_windows,
        "minProfitableWindowRatio": constraints.min_profitable_window_ratio,
    }


def _fitness_to_json(fitness) -> JsonObject:
    return {
        "survived": fitness.survived,
        "score": fitness.score,
        "rejectionReasons": fitness.rejection_reasons,
        "survivalChecks": fitness.survival_checks,
        "rankingInputs": fitness.ranking_inputs,
    }
