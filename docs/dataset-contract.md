# Versioned Dataset Contract

The Versioned Dataset Contract is the boundary between the TradingView Collector and the Nautilus Evaluator.

A dataset is a directory containing:

- `manifest.json`
- `bars.json`
- `features.json`

The collector writes this directory. The evaluator reads it. Backtests must not call TradingView live.

## Manifest

`manifest.json` describes the immutable dataset:

- `schemaVersion`: currently `1`
- `datasetId`: stable dataset identifier
- `collectedAt`: ISO timestamp
- `source`: `tradingview`
- `symbol`: ticker, root, and asset class
- `session`: session name, timezone, start, and end
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

Bars must align to the interval declared in the manifest. The first supported intervals are `5m` and `15m`.

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
- `periodsToRthBars(periods, options)`
- `writeVersionedDatasetSync(datasetPath, dataset)`

To write an ES RTH 5-minute continuous futures dataset:

```sh
npm run collect:es-rth-5m -- --output=datasets/es-rth-5m-latest
```

The command collects `CME_MINI:ES1!` 5-minute bars with TradingView's regular session, records the dataset as continuous futures with TradingView roll metadata, filters bars to the explicit `America/New_York` RTH window, writes the three contract files, and validates the dataset before exiting.

For a reproducible demo, pin the TradingView reference timestamp and manifest collection timestamp:

```sh
npm run collect:es-rth-5m -- --output=datasets/es-rth-5m-demo --to=1782676800 --collected-at=2026-06-28T12:00:00.000Z
```
