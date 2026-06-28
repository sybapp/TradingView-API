# Trading Strategy Evolution

This context defines the language for evolving TradingView-derived market data and indicator information into strategies validated by NautilusTrader.

## Language

**TradingView Data Source**:
TradingView-derived market bars, indicator plots, graphics, drawings, and strategy reports collected through this repository.
_Avoid_: TradingView oracle, final backtest result

**Candidate Signal**:
A rule, feature, or event inferred from TradingView indicator values or graphics that may become part of a NautilusTrader strategy after validation.
_Avoid_: Final strategy, proven edge

**Nautilus Validation**:
The final evaluation of a candidate strategy inside NautilusTrader using independently replayed market data, execution assumptions, costs, and performance metrics.
_Avoid_: TradingView validation, chart validation

**Bar-Level Loop**:
The first strategy evolution loop, where TradingView OHLCV bars and indicator-derived data are converted into NautilusTrader bar and custom data for validation.
_Avoid_: Tick-accurate loop, live execution simulation

**Structural Feature**:
A typed, time-aligned feature derived from TradingView indicator plots or graphics, such as a label event, trend line, price zone, or volume profile bin.
_Avoid_: Screenshot feature, raw chart image

**Strategy Spec**:
A constrained, structured description of a candidate strategy's entry rules, filters, exits, sizing, risk controls, and tunable parameters.
_Avoid_: Arbitrary generated strategy code, free-form strategy script

**Fitness Score**:
A multi-constraint evaluation of a candidate strategy that ranks out-of-sample Sharpe only after survival checks such as trade count, drawdown, fees, slippage, and regime robustness.
_Avoid_: Sharpe-only score, in-sample score

**Walk-Forward Validation**:
A validation process where strategy parameters are searched on one historical window and scored on later windows that were not used for that search.
_Avoid_: Single split validation, reused in-sample backtest

**Versioned Dataset**:
An immutable snapshot of collected TradingView bars, indicator values, structural features, and metadata used as the input to a specific experiment.
_Avoid_: Live TradingView feed, ad hoc cache

**TradingView Collector**:
The JavaScript-side component that authenticates with TradingView, collects bars and indicator-derived data, normalizes them, and writes versioned datasets.
_Avoid_: Nautilus adapter, execution engine

**Nautilus Evaluator**:
The Python-side component that converts versioned datasets into NautilusTrader data, runs walk-forward backtests, and computes fitness scores.
_Avoid_: TradingView collector, indicator scraper

**Initial Market Scope**:
The first supported market universe, limited to equity index futures such as ES so contract metadata, roll handling, sessions, fees, slippage, and bar assumptions can be made concrete.
_Avoid_: Universal market support, crypto-first scope, all asset classes

**Primary Bar Interval**:
The main validation interval for the first version, using 5-minute bars with 15-minute bars as a secondary comparison interval.
_Avoid_: One-minute-first validation, daily-only validation

**Continuous Futures Dataset**:
A TradingView futures dataset collected from a continuous contract such as ES1!, with explicit metadata describing the roll policy and the fact that the series is not itself a directly tradable contract.
_Avoid_: Single contract dataset, silently adjusted futures data

**RTH Validation Session**:
The regular trading hours session used as the primary validation session for ES strategies in the first version.
_Avoid_: Mixed RTH and ETH validation, unspecified session

**Indicator Allowlist**:
The curated set of TradingView indicators that may contribute plots or structural features to the first strategy search universe.
_Avoid_: Open-ended indicator universe, every available TradingView indicator

**Intraday Strategy**:
An ES strategy that opens positions only during the RTH validation session and must be flat before the session closes.
_Avoid_: Overnight strategy, cross-session hold

**Next-Bar Execution**:
A bar-level execution assumption where features become available only after a bar closes and any resulting order can execute no earlier than the next bar.
_Avoid_: Same-bar fill, lookahead execution

**Availability Time**:
The earliest timestamp at which a TradingView-derived value or structural feature is allowed to be visible to a strategy.
_Avoid_: Drawn-at time, source bar time

**Repainting-Risk Feature**:
A TradingView-derived feature whose historical shape or value may change after later bars arrive, requiring delayed confirmation before it can be used by a strategy.
_Avoid_: Confirmed feature, stable signal

**Dataset Manifest**:
The metadata file that describes a versioned dataset's symbol, session, interval, contract type, roll policy, indicator allowlist, collection time, and schema version.
_Avoid_: Implicit dataset metadata, filename-only metadata

**Feature File**:
The dataset file containing TradingView-derived plots and structural features with event times, availability times, feature types, values, and repainting-risk markers.
_Avoid_: Screenshot export, untyped indicator dump

**Nautilus Evaluator Package**:
The Python-side package, kept in this repository for the first version, that reads versioned datasets and runs NautilusTrader validation.
_Avoid_: Separate evaluator repository, JavaScript backtester

**Template Search**:
The first optimization approach, where bounded strategy templates and parameter spaces are searched before more expressive evolutionary methods are introduced.
_Avoid_: Genetic programming first, unconstrained mutation

**Candidate Proposer**:
The LLM role in the system, limited to proposing strategy specs, feature ideas, parameter ranges, and result interpretations that must pass validation before being trusted.
_Avoid_: Autonomous strategy executor, unrestricted code generator

**Run Registry**:
The local record of each validation or optimization run, including dataset version, strategy spec, optimizer settings, cost model, walk-forward windows, fitness breakdown, and result artifacts.
_Avoid_: Ad hoc experiment notes, unreproducible backtest result
