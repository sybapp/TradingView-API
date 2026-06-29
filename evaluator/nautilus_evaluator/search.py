"""Bounded Strategy Spec search on top of walk-forward evaluator results."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union
import copy
import hashlib
import itertools
import json
import random
import shutil

import optuna

from .strategy import (
    CostModel,
    EVALUATOR_VERSION,
    FitnessConstraints,
    WalkForwardBacktestResult,
    WalkForwardConfig,
    run_walk_forward_candidate_selection_backtest,
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
    trial_number: Optional[int] = None
    trial_parameters: JsonObject = field(default_factory=dict)
    objective_value: Optional[float] = None

    @property
    def fitness(self):
        return self.result.fitness


@dataclass(frozen=True)
class StrategySearchResult:
    optimizer_config: JsonObject
    generated_candidates: List[JsonObject]
    evaluated_candidates: List[EvaluatedSearchCandidate]
    surviving_candidates: List[EvaluatedSearchCandidate]
    rejected_candidates: List[JsonObject]
    trial_results: List[JsonObject]
    winning_candidate: Optional[EvaluatedSearchCandidate]
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
            template_candidates = _trial_ordered_template_candidates(template_candidates, search_config)
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
        generated_candidates, evaluated, rejected, trial_results, selection_candidate = _run_trial_based_search(
            dataset_path=dataset_path,
            templates=templates,
            proposed_candidates=proposed_candidates or [],
            cost_model=cost_model,
            registry_path=registry_path,
            walk_forward=walk_forward,
            fitness_constraints=fitness_constraints,
            search_config=search_config,
        )
        if not evaluated:
            raise ValueError("bounded strategy search did not produce any schema-valid candidates")
        selection_survivors, selection_rejections = _surviving_candidates(
            [selection_candidate] if selection_candidate is not None else []
        )
        surviving_candidates = selection_survivors
        rejected = _dedupe_rejected_candidates([*rejected, *selection_rejections])
        winning_candidate = selection_survivors[0] if selection_survivors else None
    else:
        generated_candidates = generate_bounded_template_specs(templates, search_config)
        remaining_budget = search_config.max_candidates - len(generated_candidates)
        if remaining_budget > 0:
            generated_candidates.extend(
                dict(candidate)
                for candidate in (proposed_candidates or [])[:remaining_budget]
            )
        evaluated, rejected = _evaluate_candidates(
            dataset_path=dataset_path,
            candidates=generated_candidates,
            cost_model=cost_model,
            registry_path=registry_path,
            walk_forward=walk_forward,
            fitness_constraints=fitness_constraints,
        )
        trial_results = []
        if not evaluated:
            raise ValueError("bounded strategy search did not produce any schema-valid candidates")
        selection_candidate = _training_selection_candidate(
            dataset_path=dataset_path,
            evaluated=evaluated,
            cost_model=cost_model,
            registry_path=registry_path,
            walk_forward=walk_forward,
            fitness_constraints=fitness_constraints,
        )
        surviving_candidates, selection_rejections = _surviving_candidates(
            [selection_candidate] if selection_candidate is not None else []
        )
        _, evaluated_rejections = _surviving_candidates(evaluated)
        rejected = _dedupe_rejected_candidates([*rejected, *evaluated_rejections])
        rejected = _dedupe_rejected_candidates([*rejected, *selection_rejections])
        winning_candidate = _rank_survivors(surviving_candidates)[0] if surviving_candidates else None
    record_path = _write_search_registry_record(
        dataset_path=Path(dataset_path),
        registry_path=Path(registry_path),
        search_config=search_config,
        cost_model=cost_model,
        walk_forward=walk_forward,
        fitness_constraints=fitness_constraints,
        generated_candidates=generated_candidates,
        evaluated_candidates=evaluated,
        surviving_candidates=surviving_candidates,
        rejected_candidates=rejected,
        trial_results=trial_results,
        winning_candidate=winning_candidate,
    )
    return StrategySearchResult(
        optimizer_config=_search_config_to_json(search_config),
        generated_candidates=generated_candidates,
        evaluated_candidates=evaluated,
        surviving_candidates=surviving_candidates,
        rejected_candidates=rejected,
        trial_results=trial_results,
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
    candidate_id_offset: int = 0,
    trial_number_offset: Optional[int] = None,
) -> tuple[List[EvaluatedSearchCandidate], List[JsonObject]]:
    evaluated: List[EvaluatedSearchCandidate] = []
    rejected: List[JsonObject] = []
    for index, candidate in enumerate(candidates, start=1):
        try:
            spec = validate_strategy_spec(candidate)
        except ValueError as exc:
            rejected.append(
                {
                    "candidateId": f"candidate-{candidate_id_offset + index}",
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
                candidate_id=f"candidate-{candidate_id_offset + index}",
                strategy_id=spec.strategy_id,
                strategy_spec=spec.raw,
                result=result,
                optimizer_phase=optimizer_phase,
                trial_number=(
                    trial_number_offset + index
                    if trial_number_offset is not None
                    else None
                ),
                trial_parameters=_trial_parameters_from_spec(spec.raw),
                objective_value=_objective_value(result),
            )
        )
    return evaluated, rejected


def _run_trial_based_search(
    *,
    dataset_path: Union[str, Path],
    templates: Sequence[StrategyTemplate],
    proposed_candidates: Sequence[Mapping[str, Any]],
    cost_model: CostModel,
    registry_path: Union[str, Path],
    walk_forward: WalkForwardConfig,
    fitness_constraints: FitnessConstraints,
    search_config: BoundedSearchConfig,
) -> tuple[List[JsonObject], List[EvaluatedSearchCandidate], List[JsonObject], List[JsonObject], Optional[EvaluatedSearchCandidate]]:
    _validate_search_config(search_config)
    search_space = _trial_search_space(templates, proposed_candidates)
    trial_plan = _optuna_trial_plan(search_space, search_config)
    generated_candidates: List[JsonObject] = []
    evaluated: List[EvaluatedSearchCandidate] = []
    rejected: List[JsonObject] = []
    trial_results: List[JsonObject] = []
    by_trial_number: Dict[int, EvaluatedSearchCandidate] = {}
    rejected_by_trial_number: Dict[int, JsonObject] = {}
    trial_plan_by_candidate_index = {
        int(plan["candidateIndex"]): plan
        for plan in trial_plan
    }

    def objective(trial) -> float:
        candidate_index = trial.suggest_int("candidate_index", 0, max(len(search_space) - 1, 0))
        plan = trial_plan_by_candidate_index[candidate_index]
        generated_candidates.append(plan["strategySpec"])
        candidate_id = f"candidate-{len(generated_candidates)}"
        trial_number = int(trial.number + 1)
        trial_parameters = dict(plan["parameters"])
        trial_parameters["candidateIndex"] = candidate_index
        try:
            spec = validate_strategy_spec(plan["strategySpec"])
        except ValueError as exc:
            rejection = {
                "candidateId": candidate_id,
                "trialNumber": trial_number,
                "source": plan["source"],
                "strategySpec": dict(plan["strategySpec"]),
                "trialParameters": trial_parameters,
                "error": str(exc),
            }
            rejected.append(rejection)
            rejected_by_trial_number[trial_number] = rejection
            raise optuna.TrialPruned(str(exc)) from exc

        for name, value in trial_parameters.items():
            trial.set_user_attr(f"parameter:{name}", value)
        trial.set_user_attr("candidateId", candidate_id)
        trial.set_user_attr("source", plan["source"])
        trial.set_user_attr("strategySpec", spec.raw)
        result = run_walk_forward_backtest(
            dataset_path=dataset_path,
            strategy_spec=spec.raw,
            cost_model=cost_model,
            registry_path=registry_path,
            walk_forward=walk_forward,
            fitness_constraints=fitness_constraints,
        )
        candidate = EvaluatedSearchCandidate(
            candidate_id=candidate_id,
            strategy_id=spec.strategy_id,
            strategy_spec=spec.raw,
            result=result,
            optimizer_phase="trial",
            trial_number=trial_number,
            trial_parameters=trial_parameters,
            objective_value=_objective_value(result),
        )
        evaluated.append(candidate)
        by_trial_number[trial_number] = candidate
        return candidate.objective_value if candidate.objective_value is not None else float("-inf")

    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=search_config.seed),
    )
    for plan in trial_plan:
        study.enqueue_trial({"candidate_index": plan["candidateIndex"]})
    study.optimize(objective, n_trials=len(trial_plan), catch=(ValueError,))

    for trial in study.trials:
        trial_number = trial.number + 1
        candidate = by_trial_number.get(trial_number)
        if candidate is not None:
            source = str(trial.user_attrs.get("source", "template"))
            trial_results.append(_evaluated_trial_to_json(candidate, source, trial))
            continue
        rejection = rejected_by_trial_number.get(trial_number)
        if rejection is not None:
            trial_results.append(_rejected_trial_to_json(rejection, trial))

    selection_candidate = _training_selection_candidate(
        dataset_path=dataset_path,
        evaluated=evaluated,
        cost_model=cost_model,
        registry_path=registry_path,
        walk_forward=walk_forward,
        fitness_constraints=fitness_constraints,
    )

    rejected = _dedupe_rejected_candidates(rejected)
    return generated_candidates, evaluated, rejected, trial_results, selection_candidate


def _all_template_candidates(templates: Sequence[StrategyTemplate]) -> List[JsonObject]:
    candidates: List[JsonObject] = []
    for template in templates:
        candidates.extend(_template_candidate_specs(template))
    return candidates


def _optuna_trial_plan(search_space: Sequence[JsonObject], search_config: BoundedSearchConfig) -> List[JsonObject]:
    rng = random.Random(search_config.seed)
    decorated = [
        (
            _trial_candidate_priority(candidate),
            rng.random(),
            index,
            candidate,
        )
        for index, candidate in enumerate(search_space)
    ]
    decorated.sort(key=lambda item: (item[0], item[1]))
    return [
        {
            **copy.deepcopy(candidate),
            "candidateIndex": index,
            "trialNumber": trial_number,
        }
        for trial_number, (_, _, index, candidate) in enumerate(
            decorated[: search_config.max_candidates],
            start=1,
        )
    ]


def _trial_search_space(
    templates: Sequence[StrategyTemplate],
    proposed_candidates: Sequence[Mapping[str, Any]],
) -> List[JsonObject]:
    search_space: List[JsonObject] = []
    for candidate in _all_template_candidates(templates):
        search_space.append(
            {
                "source": "template",
                "parameters": _trial_parameters_from_spec(candidate),
                "strategySpec": candidate,
            }
        )
    for index, candidate in enumerate(proposed_candidates, start=1):
        candidate_copy = dict(candidate)
        parameters = _trial_parameters_from_spec(candidate_copy)
        parameters.setdefault("proposalIndex", index)
        search_space.append(
            {
                "source": "proposed",
                "parameters": parameters,
                "strategySpec": candidate_copy,
            }
        )
    return search_space


def _trial_candidate_priority(candidate: Mapping[str, Any]) -> tuple[int, str]:
    parameters = candidate.get("parameters")
    if not isinstance(parameters, dict):
        parameters = {}
    source = 0 if candidate.get("source") == "template" else 1
    return (source, json.dumps(parameters, sort_keys=True))


def reproduce_search_winner(registry_record_path: Union[str, Path]) -> WalkForwardBacktestResult:
    """Re-run the winning Strategy Spec using only the search registry artifact."""
    record_path = Path(registry_record_path)
    record = json.loads(record_path.read_text(encoding="utf-8"))
    if record.get("status") == "completed_no_survivors" or record.get("winningRun") is None:
        raise ValueError("search completed with no surviving Nautilus Validation winner to reproduce")
    cost_model = record["costModel"]
    walk_forward = record["walkForward"]["config"]
    constraints = record["fitnessConstraints"]
    dataset_path = record_path.parent / record["dataset"]["artifacts"]["snapshot"]
    winning_spec = record["winningRun"]["strategySpec"]
    if winning_spec.get("selectionStrategy") == "training-window-candidate-selection":
        return run_walk_forward_candidate_selection_backtest(
            dataset_path=dataset_path,
            candidate_specs=winning_spec["candidateStrategySpecs"],
            cost_model=CostModel(
                fixed_fee=cost_model["fixedFee"],
                slippage_ticks=cost_model["slippageTicks"],
                tick_size=cost_model["tickSize"],
            ),
            registry_path=record_path.parent / "reproduced-runs",
            walk_forward=WalkForwardConfig(
                training_sessions=walk_forward["trainingSessions"],
                scoring_sessions=walk_forward["scoringSessions"],
                step_sessions=walk_forward["stepSessions"],
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
    return run_walk_forward_backtest(
        dataset_path=dataset_path,
        strategy_spec=winning_spec,
        cost_model=CostModel(
            fixed_fee=cost_model["fixedFee"],
            slippage_ticks=cost_model["slippageTicks"],
            tick_size=cost_model["tickSize"],
        ),
        registry_path=record_path.parent / "reproduced-runs",
        walk_forward=WalkForwardConfig(
            training_sessions=walk_forward["trainingSessions"],
            scoring_sessions=walk_forward["scoringSessions"],
            step_sessions=walk_forward["stepSessions"],
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


def _trial_ordered_template_candidates(candidates: List[JsonObject], search_config: BoundedSearchConfig) -> List[JsonObject]:
    search_space = [
        {
            "source": "template",
            "parameters": _trial_parameters_from_spec(candidate),
            "strategySpec": candidate,
        }
        for candidate in candidates
    ]
    return [
        trial["strategySpec"]
        for trial in _optuna_trial_plan(search_space, search_config)
    ]


def _training_selection_candidate(
    *,
    dataset_path: Union[str, Path],
    evaluated: Sequence[EvaluatedSearchCandidate],
    cost_model: CostModel,
    registry_path: Union[str, Path],
    walk_forward: WalkForwardConfig,
    fitness_constraints: FitnessConstraints,
) -> Optional[EvaluatedSearchCandidate]:
    if not evaluated:
        return None

    result = run_walk_forward_candidate_selection_backtest(
        dataset_path=dataset_path,
        candidate_specs=[candidate.strategy_spec for candidate in evaluated],
        cost_model=cost_model,
        registry_path=registry_path,
        walk_forward=walk_forward,
        fitness_constraints=fitness_constraints,
    )
    return EvaluatedSearchCandidate(
        candidate_id="training-window-selection",
        strategy_id=result.strategy_id,
        strategy_spec={
            "selectionStrategy": "training-window-candidate-selection",
            "candidateStrategySpecs": [candidate.strategy_spec for candidate in evaluated],
        },
        result=result,
        optimizer_phase="training-window-selection",
        trial_parameters={
            "selectedCandidates": [
                {
                    "windowId": selection.window_id,
                    "candidateId": selection.selected_candidate_id,
                    "strategyId": selection.selected_strategy_id,
                }
                for selection in result.selection_results
            ],
        },
        objective_value=result.fitness.score,
    )


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


def _rank_survivors(candidates: List[EvaluatedSearchCandidate]) -> List[EvaluatedSearchCandidate]:
    return sorted(candidates, key=_candidate_rank_key, reverse=True)


def _candidate_rank_key(candidate: EvaluatedSearchCandidate):
    fitness = candidate.fitness
    return (
        fitness.score if fitness.score is not None else float("-inf"),
        fitness.ranking_inputs.get("netPnl", 0),
        fitness.ranking_inputs.get("tradeCount", 0),
    )


def _diagnostic_rank_key(candidate: EvaluatedSearchCandidate):
    fitness = candidate.fitness
    return (
        fitness.ranking_inputs.get("outOfSampleSharpe", float("-inf")),
        fitness.ranking_inputs.get("netPnl", 0),
        fitness.ranking_inputs.get("tradeCount", 0),
    )


def _surviving_candidates(
    candidates: List[EvaluatedSearchCandidate],
) -> tuple[List[EvaluatedSearchCandidate], List[JsonObject]]:
    survivors: List[EvaluatedSearchCandidate] = []
    rejected: List[JsonObject] = []
    for candidate in candidates:
        validation_reasons = _nautilus_validation_rejection_reasons(candidate)
        if validation_reasons:
            rejected.append(_rejected_evaluated_candidate_to_json(candidate, validation_reasons))
            continue
        if not candidate.fitness.survived:
            rejected.append(_rejected_evaluated_candidate_to_json(candidate, candidate.fitness.rejection_reasons))
            continue
        survivors.append(candidate)
    return survivors, rejected


def _nautilus_validation_rejection_reasons(candidate: EvaluatedSearchCandidate) -> List[str]:
    record_path = candidate.result.registry_record_path
    if not record_path.exists():
        return ["missing_run_registry_record"]
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ["invalid_run_registry_record"]

    reasons: List[str] = []
    if record.get("recordType") != "Nautilus Walk-Forward Validation":
        reasons.append("not_nautilus_walk_forward_validation")
    if record.get("authoritative") is not True:
        reasons.append("not_authoritative_nautilus_validation")

    window_results = record.get("trainingWindowResults", []) + record.get("perWindowResults", [])
    if not window_results:
        reasons.append("missing_nautilus_window_results")
    for index, window in enumerate(window_results, start=1):
        provenance = window.get("nautilusTrader")
        if not _has_required_nautilus_provenance(provenance):
            reasons.append(f"missing_required_nautilus_provenance_window_{index}")
        if not _has_required_nautilus_window_metadata(window):
            reasons.append(f"missing_required_nautilus_metadata_window_{index}")
        missing_artifacts = _missing_nautilus_window_artifacts(record_path.parent, window)
        if missing_artifacts:
            reasons.append(f"missing_required_nautilus_artifacts_window_{index}")

    if any(
        not _has_required_nautilus_provenance(result.nautilus_provenance)
        for result in candidate.result.training_window_results
    ):
        reasons.append("missing_required_training_result_nautilus_provenance")
    if any(
        not _has_required_nautilus_result_metadata(result)
        for result in candidate.result.training_window_results
    ):
        reasons.append("missing_required_training_result_nautilus_metadata")
    if any(
        not _has_required_nautilus_provenance(result.nautilus_provenance)
        for result in candidate.result.window_results
    ):
        reasons.append("missing_required_scoring_result_nautilus_provenance")
    if any(
        not _has_required_nautilus_result_metadata(result)
        for result in candidate.result.window_results
    ):
        reasons.append("missing_required_scoring_result_nautilus_metadata")
    return reasons


def _has_required_nautilus_provenance(provenance: Any) -> bool:
    if not isinstance(provenance, dict):
        return False
    return (
        provenance.get("package") == "nautilus_trader"
        and isinstance(provenance.get("version"), str)
        and bool(provenance.get("version"))
        and isinstance(provenance.get("moduleFile"), str)
        and bool(provenance.get("moduleFile"))
        and provenance.get("engine") == "BacktestEngine"
        and provenance.get("runtimeImportFromThirdPartyReference") is False
    )


def _has_required_nautilus_window_metadata(window: Any) -> bool:
    if not isinstance(window, dict):
        return False
    return all(
        isinstance(window.get(key), dict) and bool(window.get(key))
        for key in ("environment", "instrument", "venue", "barType", "costConfiguration", "artifacts")
    )


def _has_required_nautilus_result_metadata(result: Any) -> bool:
    return all(
        bool(getattr(result, field, None))
        for field in ("environment", "instrument", "venue", "bar_type", "cost_configuration")
    )


def _missing_nautilus_window_artifacts(run_path: Path, window: Any) -> List[str]:
    artifacts = window.get("artifacts") if isinstance(window, dict) else None
    if not isinstance(artifacts, dict):
        return ["artifacts"]

    missing: List[str] = []
    for key in ("ordersByWindow", "nautilusOrderFills", "nautilusPositions", "nautilusAccount"):
        artifact = artifacts.get(key)
        if not isinstance(artifact, str) or not artifact:
            missing.append(key)
            continue
        if not (run_path / artifact).exists():
            missing.append(key)
    if not artifacts.get("ordersByWindowKey"):
        missing.append("ordersByWindowKey")
    return missing


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
    surviving_candidates: List[EvaluatedSearchCandidate],
    rejected_candidates: List[JsonObject],
    trial_results: List[JsonObject],
    winning_candidate: Optional[EvaluatedSearchCandidate],
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
    winning_run_artifacts = (
        _copy_winning_run_artifacts(search_path, winning_candidate)
        if winning_candidate
        else None
    )
    ranked_survivors = _rank_survivors(surviving_candidates)
    best_rejected_candidate = _best_rejected_candidate(evaluated_candidates, surviving_candidates)
    record = {
        "runId": run_id,
        "recordType": "Nautilus Validation Search",
        "authoritative": True,
        "status": "completed" if winning_candidate else "completed_no_survivors",
        "evaluatorVersion": EVALUATOR_VERSION,
        "dataset": dataset_record,
        "optimizerConfig": _search_config_to_json(search_config),
        "sampler": _sampler_config_to_json(search_config),
        "costModel": _cost_model_to_json(cost_model),
        "walkForward": {"config": _walk_forward_config_to_json(walk_forward)},
        "fitnessConstraints": _fitness_constraints_to_json(fitness_constraints),
        "trials": trial_results,
        "generatedCandidates": generated_candidates,
        "evaluatedSpecs": [_evaluated_candidate_to_json(candidate) for candidate in evaluated_candidates],
        "survivingCandidates": [_evaluated_candidate_to_json(candidate) for candidate in ranked_survivors],
        "rejectedCandidates": rejected_candidates,
        "ranking": [candidate.strategy_id for candidate in ranked_survivors],
        "bestRejectedCandidate": _best_rejected_candidate_to_json(best_rejected_candidate),
        "winningRun": _winning_candidate_to_json(winning_candidate, winning_run_artifacts),
        "reproducibilityInputs": {
            "datasetSnapshot": dataset_record["artifacts"]["snapshot"],
            "optimizerConfig": _search_config_to_json(search_config),
            "sampler": _sampler_config_to_json(search_config),
            "costModel": _cost_model_to_json(cost_model),
            "walkForward": _walk_forward_config_to_json(walk_forward),
            "fitnessConstraints": _fitness_constraints_to_json(fitness_constraints),
            "generatedCandidatesHash": _json_hash(generated_candidates),
            "trialsHash": _json_hash(trial_results),
        },
    }
    record_path = search_path / "search.json"
    record_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    return record_path


def _copy_winning_run_artifacts(search_path: Path, candidate: EvaluatedSearchCandidate) -> JsonObject:
    source = candidate.result.registry_record_path
    artifact_dir = search_path / "winning-run"
    artifact_dir.mkdir(exist_ok=True)
    destination = artifact_dir / "run.json"
    shutil.copyfile(source, destination)
    artifacts = {"runRecord": "winning-run/run.json"}
    orders_source = source.parent / "orders-by-window.json"
    if orders_source.exists():
        shutil.copyfile(orders_source, artifact_dir / "orders-by-window.json")
        artifacts["ordersByWindow"] = "winning-run/orders-by-window.json"
    return artifacts


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
    payload = {
        "candidateId": candidate.candidate_id,
        "strategyId": candidate.strategy_id,
        "strategySpec": candidate.strategy_spec,
        "optimizerPhase": candidate.optimizer_phase,
        "registryRecord": str(candidate.result.registry_record_path),
        "fitness": _fitness_to_json(candidate.fitness),
    }
    if candidate.trial_number is not None:
        payload["trialNumber"] = candidate.trial_number
        payload["trialParameters"] = candidate.trial_parameters
        payload["objectiveValue"] = candidate.objective_value
    return payload


def _rejected_evaluated_candidate_to_json(candidate: EvaluatedSearchCandidate, reasons: Sequence[str]) -> JsonObject:
    payload = {
        "candidateId": candidate.candidate_id,
        "strategyId": candidate.strategy_id,
        "source": "evaluated",
        "strategySpec": candidate.strategy_spec,
        "optimizerPhase": candidate.optimizer_phase,
        "registryRecord": str(candidate.result.registry_record_path),
        "rejectionReasons": list(reasons),
        "fitness": _fitness_to_json(candidate.fitness),
    }
    if candidate.trial_number is not None:
        payload["trialNumber"] = candidate.trial_number
        payload["trialParameters"] = candidate.trial_parameters
        payload["objectiveValue"] = candidate.objective_value
    return payload


def _winning_candidate_to_json(
    candidate: Optional[EvaluatedSearchCandidate],
    winning_run_artifacts: Optional[JsonObject],
) -> Optional[JsonObject]:
    if candidate is None:
        return None
    return {
        "candidateId": candidate.candidate_id,
        "strategyId": candidate.strategy_id,
        "strategySpec": candidate.strategy_spec,
        "fitness": _fitness_to_json(candidate.fitness),
        "trialNumber": candidate.trial_number,
        "trialParameters": candidate.trial_parameters,
        "objectiveValue": candidate.objective_value,
        "sourceRunRecord": str(candidate.result.registry_record_path),
        "provenance": {
            "recordType": "Nautilus Walk-Forward Validation",
            "requiredNautilusProvenance": True,
        },
        "artifacts": winning_run_artifacts or {},
    }


def _best_rejected_candidate(
    evaluated_candidates: List[EvaluatedSearchCandidate],
    surviving_candidates: List[EvaluatedSearchCandidate],
) -> Optional[EvaluatedSearchCandidate]:
    survivor_ids = {(candidate.candidate_id, candidate.strategy_id) for candidate in surviving_candidates}
    rejected = [
        candidate
        for candidate in evaluated_candidates
        if (candidate.candidate_id, candidate.strategy_id) not in survivor_ids
    ]
    if not rejected:
        return None
    return sorted(rejected, key=_diagnostic_rank_key, reverse=True)[0]


def _best_rejected_candidate_to_json(candidate: Optional[EvaluatedSearchCandidate]) -> Optional[JsonObject]:
    if candidate is None:
        return None
    return {
        "candidateId": candidate.candidate_id,
        "strategyId": candidate.strategy_id,
        "strategySpec": candidate.strategy_spec,
        "fitness": _fitness_to_json(candidate.fitness),
        "trialNumber": candidate.trial_number,
        "trialParameters": candidate.trial_parameters,
        "objectiveValue": candidate.objective_value,
        "diagnosticOnly": True,
    }


def _dedupe_rejected_candidates(rejected_candidates: List[JsonObject]) -> List[JsonObject]:
    deduped: List[JsonObject] = []
    seen = set()
    for candidate in rejected_candidates:
        key = (
            candidate.get("candidateId"),
            candidate.get("strategyId"),
            tuple(candidate.get("rejectionReasons", [])),
            candidate.get("error"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


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


def _sampler_config_to_json(search_config: BoundedSearchConfig) -> JsonObject:
    if search_config.method != "optuna_style":
        return {"name": "none", "seed": search_config.seed}
    return {
        "name": "optuna-tpe-sampler",
        "package": "optuna",
        "samplerClass": "TPESampler",
        "objective": "maximize_fitness_score",
        "seed": search_config.seed,
        "maxTrials": search_config.max_candidates,
        "searchSpace": "bounded_strategy_template_choices_and_validated_proposals",
    }


def _cost_model_to_json(cost_model: CostModel) -> JsonObject:
    return {
        "fixedFee": cost_model.fixed_fee,
        "slippageTicks": cost_model.slippage_ticks,
        "tickSize": cost_model.tick_size,
    }


def _walk_forward_config_to_json(config: WalkForwardConfig) -> JsonObject:
    if config.training_sessions is None or config.scoring_sessions is None:
        raise ValueError("walk_forward requires training_sessions and scoring_sessions")
    step_sessions = config.step_sessions if config.step_sessions is not None else config.scoring_sessions
    return {
        "trainingSessions": config.training_sessions,
        "scoringSessions": config.scoring_sessions,
        "stepSessions": step_sessions,
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


def _trial_parameters_from_spec(strategy_spec: Mapping[str, Any]) -> JsonObject:
    parameters = strategy_spec.get("parameters")
    if not isinstance(parameters, dict):
        return {}
    return {
        key: value
        for key, value in parameters.items()
        if key not in ("direction_feature",)
    }


def _objective_value(result: WalkForwardBacktestResult) -> Optional[float]:
    return result.fitness.score if result.fitness.survived else None


def _evaluated_trial_to_json(candidate: EvaluatedSearchCandidate, source: str, trial: Any = None) -> JsonObject:
    return {
        "trialNumber": candidate.trial_number,
        "candidateId": candidate.candidate_id,
        "strategyId": candidate.strategy_id,
        "source": source,
        "state": "complete",
        "samplerState": str(trial.state.name) if trial is not None else "COMPLETE",
        "optunaParams": dict(trial.params) if trial is not None else {},
        "parameters": candidate.trial_parameters,
        "objectiveValue": candidate.objective_value,
        "strategySpec": candidate.strategy_spec,
        "registryRecord": str(candidate.result.registry_record_path),
        "fitness": _fitness_to_json(candidate.fitness),
    }


def _rejected_trial_to_json(rejection: Mapping[str, Any], trial: Any = None) -> JsonObject:
    return {
        "trialNumber": rejection.get("trialNumber"),
        "candidateId": rejection.get("candidateId"),
        "source": rejection.get("source"),
        "state": "rejected",
        "samplerState": str(trial.state.name) if trial is not None else "PRUNED",
        "optunaParams": dict(trial.params) if trial is not None else {},
        "parameters": rejection.get("trialParameters", {}),
        "objectiveValue": None,
        "strategySpec": rejection.get("strategySpec"),
        "error": rejection.get("error"),
    }


def _json_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()
