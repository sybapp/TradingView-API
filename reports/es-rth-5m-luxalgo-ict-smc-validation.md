# ES RTH 5m LuxAlgo ICT/SMC Validation Report

## Dataset

- Dataset id: `es-rth-5m-luxalgo-ict-smc-validation-2026-06-28`
- Collection: `{"kind": "pinned-luxalgo-ict-smc-validation", "liveCollected": false, "source": "examples/BuildPinnedLuxAlgoIctSmcValidationDataset.js"}`
- Bars: 390 `5m` RTH bars
- Features: 45
- Bullish Structure Event signals: 5
- Liquidity Zones: order_block=10, fair_value_gap=5
- Zone confirmations: touch=5, reclaim=0
- Rejected or ambiguous derivations: 10

## Walk-Forward Validation

- Search status: `completed`
- Comparison status: `smoke-only`
- Actual trade count: 8
- Trade comparison threshold: 30
- Walk-forward config: `{"scoringSessions": 1, "stepSessions": 1, "trainingSessions": 1}`
- Cost model: `{"fixedFee": 2.5, "slippageTicks": 1, "tickSize": 0.25}`

| Candidate | Strategy | Trades | Comparison |
| --- | --- | ---: | --- |
| candidate-1 | luxalgo-ict-smc-long-1 | 16 | below_threshold |
| candidate-2 | luxalgo-ict-smc-long-2 | 12 | below_threshold |
| candidate-3 | luxalgo-ict-smc-long-3 | 12 | below_threshold |
| candidate-4 | luxalgo-ict-smc-long-4 | 8 | below_threshold |

### Session Windows

| Window | Training | Scoring |
| --- | --- | --- |
| wf-1 | 2026-06-22 to 2026-06-22 (1 session, 78 bars) | 2026-06-23 to 2026-06-23 (1 session, 78 bars) |
| wf-2 | 2026-06-23 to 2026-06-23 (1 session, 78 bars) | 2026-06-24 to 2026-06-24 (1 session, 78 bars) |
| wf-3 | 2026-06-24 to 2026-06-24 (1 session, 78 bars) | 2026-06-25 to 2026-06-25 (1 session, 78 bars) |
| wf-4 | 2026-06-25 to 2026-06-25 (1 session, 78 bars) | 2026-06-26 to 2026-06-26 (1 session, 78 bars) |

### Scoring Results

| Window | Sessions | Session Count | Bars | Trades | Net PnL |
| --- | --- | ---: | ---: | ---: | ---: |
| wf-1 | 2026-06-23 to 2026-06-23 | 1 | 78 | 2 | 5950 |
| wf-2 | 2026-06-24 to 2026-06-24 | 1 | 78 | 2 | 5950 |
| wf-3 | 2026-06-25 to 2026-06-25 | 1 | 78 | 2 | 5950 |
| wf-4 | 2026-06-26 to 2026-06-26 | 1 | 78 | 2 | 5950 |

## Derivation Diagnostics

- `luxalgo-structure-event-direction`: {"derivedFeatures": 10, "sourceFeatures": 15, "unresolvedDirection": 5}
- `luxalgo-liquidity-zone-entry`: {"derivedFeatures": 5, "invalidZoneGeometry": 5, "sourceStructureEvents": 5, "sourceZones": 10}

## Reproduction

- Dataset path: `datasets/es-rth-5m-luxalgo-ict-smc-validation-2026-06-28`
- Search record: `runs/luxalgo-ict-smc-es-rth-5m/searches/strategy-search-fb36aa2a21fa/search.json`
- Run Registry path: `runs/luxalgo-ict-smc-es-rth-5m`
- Re-run: `uv run python -m evaluator.nautilus_evaluator.luxalgo_report datasets/es-rth-5m-luxalgo-ict-smc-validation-2026-06-28 --run-registry runs/luxalgo-ict-smc-es-rth-5m`
