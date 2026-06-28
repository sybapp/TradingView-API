import { describe, it, expect } from 'vitest';
import fs from 'fs';
import os from 'os';
import path from 'path';
import TradingView from '../main';

function makeMockClient(periods, infos = {}) {
  const client = {
    ended: false,
    charts: [],
    Session: {
      Chart: class MockChart {
        constructor() {
          client.charts.push(this);
        }

        periods = periods;

        infos = {
          pricescale: 100,
          ...infos,
        };

        deleted = false;

        updateCallbacks = [];

        symbolCallbacks = [];

        errorCallbacks = [];

        setMarket(symbol, options) {
          this.lastMarket = { symbol, options };
          this.infos = {
            ...this.infos,
            full_name: symbol,
          };

          setTimeout(() => {
            this.symbolCallbacks.forEach((cb) => cb());
            this.updateCallbacks.forEach((cb) => cb(['$prices'], options));
          }, 0);
        }

        onSymbolLoaded(cb) {
          this.symbolCallbacks.push(cb);
        }

        onUpdate(cb) {
          this.updateCallbacks.push(cb);
        }

        onError(cb) {
          this.errorCallbacks.push(cb);
        }

        delete() {
          this.deleted = true;
        }
      },
    },
    end() {
      this.ended = true;
      return Promise.resolve();
    },
  };

  return client;
}

describe('TradingView collector', () => {
  it('exports a mocked ES RTH 5m collection as a valid dataset', async () => {
    const outputPath = fs.mkdtempSync(path.join(os.tmpdir(), 'tv-es-rth-'));
    const client = makeMockClient([
      {
        time: Date.parse('2026-06-25T13:25:00.000Z') / 1000,
        open: 5499,
        max: 5500,
        min: 5498,
        close: 5499.5,
        volume: 200,
      },
      {
        time: Date.parse('2026-06-25T13:30:00.000Z') / 1000,
        open: 5500.25,
        max: 5502.5,
        min: 5498.75,
        close: 5501,
        volume: 1200,
      },
      {
        time: Date.parse('2026-06-25T13:35:00.000Z') / 1000,
        open: 5501,
        max: 5503,
        min: 5500.5,
        close: 5502.25,
        volume: 980,
      },
    ]);

    const result = await TradingView.collector.collectEsRth5mDataset({
      createClient: () => client,
      outputPath,
      now: new Date('2026-06-28T12:00:00.000Z'),
      minBars: 2,
      timeoutMs: 1000,
    });

    expect(result.validation).toEqual({ valid: true, errors: [] });
    expect(result.dataset.manifest).toMatchObject({
      schemaVersion: 1,
      datasetId: 'es-rth-5m-2026-06-28T12-00-00-000Z',
      source: 'tradingview',
      symbol: {
        ticker: 'CME_MINI:ES1!',
        root: 'ES',
        assetClass: 'equity_index_futures',
      },
      session: {
        name: 'RTH',
        timezone: 'America/New_York',
        start: '09:30',
        end: '16:00',
      },
      bar: {
        interval: '5m',
        priceScale: 100,
        volumeUnit: 'contracts',
      },
      contract: {
        type: 'continuous_futures',
        continuous: true,
      },
    });
    expect(result.dataset.bars).toEqual([
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
    ]);

    expect(TradingView.datasetContract.readDatasetSync(outputPath)).toEqual(result.dataset);
    expect(client.ended).toBe(true);
    expect(client.charts[0].lastMarket).toEqual({
      symbol: 'CME_MINI:ES1!',
      options: {
        timeframe: '5',
        range: 78,
        to: undefined,
        session: 'regular',
        backadjustment: true,
      },
    });
  });
});
