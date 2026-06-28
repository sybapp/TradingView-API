# Strategy Evolution Roadmap

This roadmap turns TradingView-derived market data and indicator information into strategies that are finally validated by NautilusTrader.

## Goal

Build a reproducible strategy evolution system that can discover ES intraday strategies with strong out-of-sample, cost-adjusted Sharpe in NautilusTrader.

TradingView is a data and feature source. NautilusTrader is the final validation authority.

## First-version scope

- Market: ES equity index futures, starting with a TradingView continuous contract such as `ES1!`.
- Session: RTH only.
- Interval: 5-minute bars as primary, 15-minute bars as secondary comparison.
- Strategy style: intraday only, flat before RTH close.
- Data flow: collect TradingView data into immutable dataset directories before Nautilus consumes it.
- Strategy search: constrained strategy specs and template search, followed by Optuna-style optimization.
- Validation: NautilusTrader walk-forward backtests with next-bar execution, fees, slippage, and multi-constraint fitness.

## Architecture

### TradingView Collector

The JavaScript-side collector owns TradingView authentication, chart sessions, indicator sessions, and normalization.

It exports versioned dataset directories containing:

- `manifest.json`: symbol, session, interval, contract type, roll policy, indicator allowlist, collection time, schema version.
- `bars.*`: OHLCV bars aligned to the validation interval.
- `features.*`: indicator plots and structural features with `event_time`, `availability_time`, feature type, value, and repainting-risk metadata.

### Nautilus Evaluator

The Python-side evaluator lives in this repository in an independent directory.

It owns:

- Reading versioned datasets.
- Converting bars and features into NautilusTrader data inputs.
- Compiling validated strategy specs into NautilusTrader strategies.
- Running walk-forward backtests.
- Applying cost, slippage, and next-bar execution assumptions.
- Writing run registry records.

### Strategy Spec

A strategy spec is a structured description of:

- Entry rules.
- Filters.
- Exit rules.
- Sizing.
- Risk controls.
- Tunable parameter ranges.

The system searches strategy specs, not arbitrary generated Python code.

## Validation rules

- TradingView strategy reports may inform exploration but cannot be the final performance authority.
- Every feature must carry an availability time.
- Repainting-risk features must be delayed until confirmed.
- Signals are evaluated after bar close.
- Orders execute no earlier than the next bar.
- Fitness is multi-constraint: out-of-sample Sharpe ranks survivors only after checks for trade count, drawdown, costs, slippage, and regime robustness.
- Every optimization or backtest writes a local run registry record.

## Milestones

### 1. Dataset contract

Define and test the dataset directory schema:

- `manifest.json`
- bars file
- feature file
- schema validation
- a tiny fixture dataset

Done when Python can load a fixture dataset and validate metadata, bars, feature availability, and repainting-risk fields.

### 2. ES RTH 5m collector

Add a collector flow for ES RTH 5-minute bars:

- TradingView chart collection.
- RTH filtering.
- continuous futures metadata.
- roll policy metadata.
- deterministic dataset IDs.

Done when the collector can export a reproducible ES RTH 5m dataset.

### 3. Indicator feature extraction

Add the first curated indicator allowlist and feature normalizers:

- plot features.
- label/line/box/profile structural features.
- availability-time rules.
- repainting-risk markers.

Done when features are exported with stable typed schemas and can be inspected independently from Nautilus.

### 4. Nautilus evaluator skeleton

Create the Python evaluator package:

- dataset reader.
- Nautilus bar conversion.
- minimal custom feature representation.
- one smoke-test backtest over fixture data.

Done when Nautilus can replay the fixture bars and run a trivial strategy.

### 5. Strategy spec compiler

Define the first strategy spec schema and compile it into a Nautilus strategy:

- entry condition over features.
- fixed exit or stop/take-profit.
- intraday flat-by-close.
- fixed sizing.

Done when one hand-written spec can run through Nautilus and produce a run record.

### 6. Walk-forward runner and fitness

Add:

- rolling windows.
- cost and slippage assumptions.
- multi-constraint fitness.
- run registry output.

Done when a set of candidate specs can be ranked by out-of-sample fitness.

### 7. Template search

Add deterministic/random template search, then Optuna-style optimization.

Done when the system can search bounded strategy specs and reproduce the winning run from registry artifacts.

## Open details

- Exact ES TradingView symbol and exchange namespace.
- RTH calendar source and timezone handling.
- Concrete roll policy metadata fields.
- First indicator allowlist.
- Feature schema field names and file format.
- Cost and slippage defaults for ES.
- Walk-forward window sizes.
- Run registry directory layout.
