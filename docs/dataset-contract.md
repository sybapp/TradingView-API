# Versioned Dataset Contract

The Versioned Dataset Contract is the boundary between the TradingView Collector and the Nautilus Evaluator.

A dataset is a directory containing:

- `manifest.json`
- `bars.json`
- `features.json`

The collector writes this directory. The evaluator reads it. Backtests must not call TradingView live.

The first Python evaluator boundary lives in `evaluator/nautilus_evaluator`.
It exposes:

- `load_versioned_dataset(dataset_path)`
- `to_nautilus_bar_inputs(dataset)`
- `to_nautilus_bars(dataset)` when `nautilus_trader` is installed
- `run_nautilus_validation_backtest(dataset_path, strategy_spec, cost_model, registry_path)`
- `run_smoke_backtest(dataset_path)`
- `validate_strategy_spec(strategy_spec)`
- `run_strategy_backtest(dataset_path, strategy_spec, cost_model, registry_path)`
- `generate_bounded_template_specs(templates, search_config)`
- `run_bounded_strategy_search(dataset_path, templates, cost_model, registry_path, walk_forward, fitness_constraints, search_config, proposed_candidates=None)`
- `reproduce_search_winner(search_registry_record_path)`

The non-authoritative smoke replay can be run against the fixture dataset
without calling TradingView:

```sh
uv run python -m evaluator tests/fixtures/es-rth-5m-dataset
```

The first hand-written Strategy Spec fixture runs inside the real
NautilusTrader backtest engine and records a Nautilus Validation run:

```sh
uv run python -m evaluator tests/fixtures/es-rth-5m-dataset \
  --strategy-spec tests/fixtures/strategy-specs/supertrend-long.json \
  --run-registry runs
```

This validation path loads the fixture Versioned Dataset, converts bars into
NautilusTrader `Bar` objects for the ESU6.GLBX fixture instrument, executes the
Strategy Spec through a Nautilus `Strategy` adapter, maps fixed per-contract
fees and supported slippage settings into Nautilus execution configuration,
forces the strategy flat before RTH close, and writes a `run.json` marked
`Nautilus Validation` plus Nautilus report artifacts. Unsupported fee or
slippage mappings fail before the engine is started.

The evaluator is a root-level `uv` project. Use `uv run ...` commands from the
repository root so imports resolve from the locked Python 3.12 environment;
system `python3` is not a supported evaluator runtime.

The first bounded Strategy Spec search loop lives in the same Python package.
It generates schema-valid specs from constrained `StrategyTemplate` choices,
supports deterministic, seeded random, and Optuna-style bounded trial ordering,
evaluates every valid candidate through walk-forward validation, ranks survivors
by multi-constraint Fitness Score, and writes a search registry record with the
optimizer configuration, generated candidates, evaluated specs, rejected
proposals, surviving candidates, evaluator version, dataset version metadata, a
dataset snapshot, reproducibility inputs, and winning run artifacts when a real
Nautilus Validation survivor exists. If no candidate survives, the search
completes with no winner and may report only a diagnostic best rejected
candidate. LLM-proposed candidates are accepted only as candidate specs and are
validated before they can be evaluated or ranked.

## Manifest

`manifest.json` describes the immutable dataset:

- `schemaVersion`: currently `1`
- `datasetId`: stable dataset identifier
- `collectedAt`: ISO timestamp
- `source`: `tradingview`
- `symbol`: ticker, root, and asset class
- `session`: session name, timezone, start, end, optional flat-before-close offset, and optional per-session instances
- `bar`: interval, price scale, and volume unit
- `contract`: continuous futures metadata and roll policy metadata
- `indicators`: curated indicator allowlist entries

## Bars

`bars.json` is an array of OHLCV records:

- `time`: ISO timestamp
- `open`
- `high`
- `low`
- `close`
- `volume`

Bars must align to the interval declared in the manifest. The first supported intervals are `5m` and `15m`. RTH datasets may contain overnight or weekend gaps between distinct sessions, but bars must remain continuous at the declared interval inside each session.

When present, `manifest.session.sessions` records the derived RTH session structure:

- `id`: local session date in the declared timezone
- `firstBarTime`
- `lastBarTime`
- `flatBeforeCloseTime`
- `barCount`

## Features

`features.json` is an array of TradingView-derived feature records:

- `id`
- `source`
- `indicatorId`
- `type`
- `name`
- `eventTime`
- `availabilityTime`
- `repaintingRisk`
- `value`

`availabilityTime` is the earliest time a Strategy Spec may see the feature. It must be on or after `eventTime`.

`repaintingRisk` is either:

- `confirmed`
- `repainting-risk`

Graphics must be represented as typed Structural Features rather than screenshots.

## Public API

The JavaScript contract module is exported as `TradingView.datasetContract`:

- `readDatasetSync(datasetPath)`
- `validateDataset(dataset)`

`validateDataset` returns:

```json
{
  "valid": true,
  "errors": []
}
```

When invalid, `errors` contains `{ "path": "...", "message": "..." }` records suitable for tests and CLI output.

The first TradingView Collector path is exported as `TradingView.collector`:

- `collectEsRth5mDataset(options)`
- `buildEsRth5mDataset(options)`
- `collectIndicatorStudies(options)`
- `indicatorStudiesToFeatures(options)`
- `periodsToRthBars(periods, options)`
- `writeVersionedDatasetSync(datasetPath, dataset)`
- `CURATED_INDICATOR_ALLOWLIST`

`collectEsRth5mDataset(options)` collects the default curated indicator allowlist unless `includeIndicatorFeatures` is set to `false` or `indicatorStudies` are supplied directly. `buildEsRth5mDataset(options)` accepts optional `indicatorStudies` and `indicatorAllowlist` values. Allowlisted study periods are exported as plot features, and allowlisted study graphics are exported as typed Structural Features. Repainting-risk indicators use delayed availability times before the resulting records may be consumed as confirmed candidate signals.

To write an ES RTH 5-minute continuous futures dataset:

```sh
npm run collect:es-rth-5m -- --output=datasets/es-rth-5m-latest
```

The command collects `CME_MINI:ES1!` 5-minute bars with TradingView's regular session, records the dataset as continuous futures with TradingView roll metadata, filters bars to the explicit `America/New_York` RTH window, preserves multiple RTH sessions when present, writes the three contract files, and validates the dataset before exiting.

For a reproducible demo, pin the TradingView reference timestamp and manifest collection timestamp:

```sh
npm run collect:es-rth-5m -- --output=datasets/es-rth-5m-demo --to=1782676800 --collected-at=2026-06-28T12:00:00.000Z
```
