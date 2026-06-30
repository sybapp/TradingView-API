import { describe, it, expect } from 'vitest';
import path from 'path';
import TradingView from '../main';

const fixtureDir = path.join(
  __dirname,
  'fixtures',
  'es-rth-5m-dataset',
);

describe('Versioned Dataset Contract', () => {
  it('validates an ES RTH 5 minute fixture dataset', () => {
    const dataset = TradingView.datasetContract.readDatasetSync(fixtureDir);
    const result = TradingView.datasetContract.validateDataset(dataset);

    expect(result).toEqual({ valid: true, errors: [] });
    expect(dataset.manifest.session.sessions).toEqual([
      {
        id: '2026-06-25',
        firstBarTime: '2026-06-25T13:30:00.000Z',
        lastBarTime: '2026-06-25T19:55:00.000Z',
        flatBeforeCloseTime: '2026-06-25T19:55:00.000Z',
        barCount: 78,
      },
      {
        id: '2026-06-26',
        firstBarTime: '2026-06-26T13:30:00.000Z',
        lastBarTime: '2026-06-26T13:40:00.000Z',
        flatBeforeCloseTime: '2026-06-26T19:55:00.000Z',
        barCount: 3,
      },
    ]);
  });

  it('rejects missing required manifest metadata', () => {
    const dataset = TradingView.datasetContract.readDatasetSync(fixtureDir);
    delete dataset.manifest.contract.rollPolicy;
    delete dataset.manifest.symbol.ticker;
    dataset.manifest.indicators[0].repaintingRisk = 'maybe';

    const result = TradingView.datasetContract.validateDataset(dataset);

    expect(result.valid).toBe(false);
    expect(result.errors).toEqual(
      expect.arrayContaining([
        {
          path: 'manifest.symbol.ticker',
          message: 'must be a non-empty string',
        },
        {
          path: 'manifest.contract.rollPolicy',
          message: 'must be an object',
        },
        {
          path: 'manifest.indicators[0].repaintingRisk',
          message: 'must be confirmed or repainting-risk',
        },
      ]),
    );
  });

  it('rejects unsafe feature availability and repainting metadata', () => {
    const dataset = TradingView.datasetContract.readDatasetSync(fixtureDir);
    delete dataset.features[0].availabilityTime;
    delete dataset.features[0].value;
    dataset.features[1].availabilityTime = '2026-06-25T13:25:00.000Z';
    dataset.features[1].repaintingRisk = 'unknown';

    const result = TradingView.datasetContract.validateDataset(dataset);

    expect(result.valid).toBe(false);
    expect(result.errors).toEqual(
      expect.arrayContaining([
        {
          path: 'features[0].availabilityTime',
          message: 'must be an ISO timestamp',
        },
        {
          path: 'features[0].value',
          message: 'must be present',
        },
        {
          path: 'features[1].availabilityTime',
          message: 'must be on or after eventTime',
        },
        {
          path: 'features[1].repaintingRisk',
          message: 'must be confirmed or repainting-risk',
        },
      ]),
    );
  });

  it('rejects bars that do not align to the manifest interval', () => {
    const dataset = TradingView.datasetContract.readDatasetSync(fixtureDir);
    dataset.bars[1].time = '2026-06-25T13:36:00.000Z';

    const result = TradingView.datasetContract.validateDataset(dataset);

    expect(result.valid).toBe(false);
    expect(result.errors).toEqual(
      expect.arrayContaining([
        {
          path: 'bars[1].time',
          message: 'must be 5 minutes after bars[0].time',
        },
      ]),
    );
  });

  it('allows overnight gaps only between distinct RTH sessions', () => {
    const dataset = TradingView.datasetContract.readDatasetSync(fixtureDir);
    const overnightBoundaryIndex = dataset.bars.findIndex(
      (bar) => bar.time === '2026-06-26T13:30:00.000Z',
    );

    expect(overnightBoundaryIndex).toBeGreaterThan(0);
    expect(TradingView.datasetContract.validateDataset(dataset)).toEqual({
      valid: true,
      errors: [],
    });
  });

  it('rejects missing bars inside an RTH session', () => {
    const dataset = TradingView.datasetContract.readDatasetSync(fixtureDir);
    dataset.bars = dataset.bars.filter(
      (bar) => bar.time !== '2026-06-26T13:35:00.000Z',
    );

    const result = TradingView.datasetContract.validateDataset(dataset);

    expect(result.valid).toBe(false);
    expect(result.errors).toEqual(
      expect.arrayContaining([
        {
          path: 'bars[79].time',
          message: 'must be 5 minutes after bars[78].time',
        },
      ]),
    );
  });

  it('accepts 15 minute ES RTH bars and rejects malformed 15 minute gaps', () => {
    const dataset = TradingView.collector.buildEsRth15mDataset({
      now: new Date('2026-06-28T12:00:00.000Z'),
      bars: [
        {
          time: '2026-06-25T13:30:00.000Z',
          open: 5500.25,
          high: 5508.5,
          low: 5498.75,
          close: 5506,
          volume: 3200,
        },
        {
          time: '2026-06-25T13:45:00.000Z',
          open: 5506,
          high: 5510,
          low: 5504.5,
          close: 5508.25,
          volume: 2980,
        },
        {
          time: '2026-06-26T13:30:00.000Z',
          open: 5514.25,
          high: 5520,
          low: 5513.75,
          close: 5518.5,
          volume: 3180,
        },
        {
          time: '2026-06-26T13:45:00.000Z',
          open: 5518.5,
          high: 5521,
          low: 5516.5,
          close: 5519.25,
          volume: 3020,
        },
      ],
      indicatorAllowlist: [],
    });

    expect(TradingView.datasetContract.validateDataset(dataset)).toEqual({
      valid: true,
      errors: [],
    });
    expect(dataset.manifest.bar.interval).toBe('15m');
    expect(dataset.manifest.symbol).toEqual({
      ticker: 'CME_MINI:ES1!',
      root: 'ES',
      assetClass: 'equity_index_futures',
    });
    expect(dataset.manifest.contract).toMatchObject({
      type: 'continuous_futures',
      continuous: true,
    });

    dataset.bars[1].time = '2026-06-25T13:50:00.000Z';
    const malformed = TradingView.datasetContract.validateDataset(dataset);

    expect(malformed.valid).toBe(false);
    expect(malformed.errors).toEqual(
      expect.arrayContaining([
        {
          path: 'bars[1].time',
          message: 'must be 15 minutes after bars[0].time',
        },
      ]),
    );
  });

  it('accepts LuxAlgo structural feature metadata in the dataset contract', () => {
    const dataset = TradingView.collector.buildEsRth5mDataset({
      now: new Date('2026-06-28T12:00:00.000Z'),
      bars: [
        {
          time: '2026-06-25T13:30:00.000Z',
          open: 5500.25,
          high: 5502.5,
          low: 5498.75,
          close: 5501,
          volume: 1200,
        },
        {
          time: '2026-06-25T13:35:00.000Z',
          open: 5501,
          high: 5503,
          low: 5500.5,
          close: 5502.25,
          volume: 980,
        },
      ],
      indicatorAllowlist: TradingView.collector.LUXALGO_ICT_SMC_OPT_IN_ALLOWLIST,
      indicatorStudies: [
        {
          indicatorId: TradingView.collector.LUXALGO_ICT_SMC_OPT_IN_ALLOWLIST[0].id,
          graphic: {
            labels: [
              {
                id: 1,
                x: 0,
                y: 5502.5,
                text: 'BOS',
              },
            ],
          },
        },
      ],
    });

    expect(TradingView.datasetContract.validateDataset(dataset)).toEqual({
      valid: true,
      errors: [],
    });
    expect(dataset.features[0].value).toEqual(expect.objectContaining({
      graphicKind: 'label',
      graphicId: 1,
      sourceFields: expect.objectContaining({
        text: 'BOS',
      }),
    }));
  });

  it('accepts derived signal feature provenance metadata in the dataset contract', () => {
    const dataset = TradingView.collector.buildEsRth5mDataset({
      now: new Date('2026-06-28T12:00:00.000Z'),
      bars: [
        {
          time: '2026-06-25T13:30:00.000Z',
          open: 5500.25,
          high: 5502.5,
          low: 5498.75,
          close: 5501,
          volume: 1200,
        },
        {
          time: '2026-06-25T13:35:00.000Z',
          open: 5501,
          high: 5503,
          low: 5500.5,
          close: 5502.25,
          volume: 980,
        },
      ],
      indicatorAllowlist: TradingView.collector.LUXALGO_ICT_SMC_OPT_IN_ALLOWLIST,
      indicatorStudies: [
        {
          indicatorId: TradingView.collector.LUXALGO_ICT_SMC_OPT_IN_ALLOWLIST[0].id,
          graphic: {
            labels: [
              {
                id: 1,
                x: 0,
                y: 5502.5,
                text: 'BOS',
                style: 'label_up',
              },
            ],
          },
        },
      ],
    });

    const signalFeature = dataset.features.find((feature) => feature.type === 'signal');

    expect(signalFeature).toEqual(expect.objectContaining({
      value: true,
      metadata: expect.objectContaining({
        provenance: expect.objectContaining({
          sourceFeatureIds: [expect.any(String)],
          derivation: expect.objectContaining({
            rule: 'luxalgo-structure-event-direction',
            version: '1',
          }),
          directionEvidence: expect.objectContaining({
            direction: 'bullish',
          }),
        }),
      }),
    }));
    expect(TradingView.datasetContract.validateDataset(dataset)).toEqual({
      valid: true,
      errors: [],
    });
  });

  it('accepts optional derivation diagnostics companion data', () => {
    const dataset = TradingView.collector.buildEsRth5mDataset({
      now: new Date('2026-06-28T12:00:00.000Z'),
      bars: [
        {
          time: '2026-06-25T13:30:00.000Z',
          open: 5500.25,
          high: 5502.5,
          low: 5498.75,
          close: 5501,
          volume: 1200,
        },
        {
          time: '2026-06-25T13:35:00.000Z',
          open: 5501,
          high: 5503,
          low: 5500.5,
          close: 5502.25,
          volume: 980,
        },
      ],
      indicatorAllowlist: TradingView.collector.LUXALGO_ICT_SMC_OPT_IN_ALLOWLIST,
      indicatorStudies: [
        {
          indicatorId: TradingView.collector.LUXALGO_ICT_SMC_OPT_IN_ALLOWLIST[0].id,
          graphic: {
            labels: [
              {
                id: 1,
                x: 0,
                y: 5502.5,
                text: 'MSS',
                style: 'label_left',
              },
            ],
          },
        },
      ],
    });

    expect(dataset.derivationDiagnostics).toEqual(expect.objectContaining({
      schemaVersion: 1,
      rules: expect.arrayContaining([
        expect.objectContaining({
          rule: 'luxalgo-structure-event-direction',
          version: '1',
          counts: expect.objectContaining({
            unresolvedDirection: 1,
          }),
        }),
      ]),
    }));
    expect(TradingView.datasetContract.validateDataset(dataset)).toEqual({
      valid: true,
      errors: [],
    });
  });
});
