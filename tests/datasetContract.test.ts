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
});
