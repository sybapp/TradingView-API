import { describe, it, expect } from 'vitest';
import fs from 'fs';
import os from 'os';
import path from 'path';
import TradingView from '../main';

function makeMockClient(periods, infos = {}, studySnapshots = {}) {
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

        Study = class MockStudy {
          constructor(indicator) {
            const snapshot = studySnapshots[indicator.id] || {};
            Object.assign(this, snapshot);
            this.instance = indicator;
            this.readyCallbacks = [];
            this.errorCallbacks = [];

            setTimeout(() => {
              this.readyCallbacks.forEach((cb) => cb());
            }, 0);
          }

          onReady(cb) {
            this.readyCallbacks.push(cb);
          }

          onError(cb) {
            this.errorCallbacks.push(cb);
          }
        };

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
  const bars = [
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
    {
      time: '2026-06-25T13:40:00.000Z',
      open: 5502.25,
      high: 5504,
      low: 5501.5,
      close: 5503.25,
      volume: 850,
    },
  ];

  it('exports curated indicator plots and structural graphics as valid features', () => {
    const dataset = TradingView.collector.buildEsRth5mDataset({
      bars,
      now: new Date('2026-06-28T12:00:00.000Z'),
      datasetId: 'indicator-feature-fixture',
      indicatorStudies: [
        {
          indicatorId: 'STD;Supertrend',
          periods: [
            {
              $time: Date.parse('2026-06-25T13:35:00.000Z') / 1000,
              direction: 1,
            },
          ],
          graphic: {
            lines: [
              {
                id: 11,
                name: 'support_line',
                x1: 0,
                y1: 5498.75,
                x2: 2,
                y2: 5501.5,
                extend: 'right',
                style: 'solid',
                color: 65280,
                width: 2,
              },
            ],
            boxes: [
              {
                id: 12,
                name: 'demand_zone',
                x1: 0,
                y1: 5502.5,
                x2: 1,
                y2: 5498.75,
                bgColor: 32768,
                color: 65280,
                extend: 'none',
                style: 'solid',
                width: 1,
                text: 'demand_zone',
              },
            ],
          },
        },
        {
          indicatorId: 'STD;Zig_Zag',
          periods: [
            {
              $time: Date.parse('2026-06-25T13:35:00.000Z') / 1000,
              pivotHigh: 5503,
            },
          ],
          graphic: {
            labels: [
              {
                id: 21,
                x: 0,
                y: 5502.5,
                text: 'PH',
                style: 'label_down',
                yLoc: 'price',
                color: 16711680,
                textColor: 16777215,
              },
            ],
            horizHists: [
              {
                id: 22,
                name: 'volume_profile',
                firstBarTime: 0,
                lastBarTime: 2,
                priceLow: 5498,
                priceHigh: 5504,
                rate: [0.2, 0.8],
              },
            ],
          },
        },
      ],
    });

    expect(dataset.manifest.indicators).toEqual([
      {
        id: 'STD;Supertrend',
        name: 'Supertrend',
        version: 'tradingview',
        repaintingRisk: 'confirmed',
      },
      {
        id: 'STD;Zig_Zag',
        name: 'Zig Zag',
        version: 'tradingview',
        repaintingRisk: 'repainting-risk',
      },
    ]);
    expect(dataset.features).toHaveLength(6);
    expect(dataset.features).toEqual(expect.arrayContaining([
      expect.objectContaining({
        indicatorId: 'STD;Supertrend',
        type: 'line',
        name: 'support_line',
        eventTime: '2026-06-25T13:30:00.000Z',
        availabilityTime: '2026-06-25T13:30:00.000Z',
        repaintingRisk: 'confirmed',
        value: expect.objectContaining({
          startPrice: 5498.75,
          endPrice: 5501.5,
        }),
      }),
      expect.objectContaining({
        indicatorId: 'STD;Supertrend',
        type: 'box',
        name: 'demand_zone',
        eventTime: '2026-06-25T13:30:00.000Z',
        availabilityTime: '2026-06-25T13:30:00.000Z',
        repaintingRisk: 'confirmed',
        value: expect.objectContaining({
          top: 5502.5,
          bottom: 5498.75,
        }),
      }),
      expect.objectContaining({
        indicatorId: 'STD;Supertrend',
        type: 'plot',
        name: 'direction',
        eventTime: '2026-06-25T13:35:00.000Z',
        availabilityTime: '2026-06-25T13:35:00.000Z',
        repaintingRisk: 'confirmed',
        value: 1,
      }),
      expect.objectContaining({
        indicatorId: 'STD;Zig_Zag',
        type: 'label',
        name: 'PH',
        eventTime: '2026-06-25T13:30:00.000Z',
        availabilityTime: '2026-06-25T13:35:00.000Z',
        repaintingRisk: 'repainting-risk',
        value: expect.objectContaining({
          price: 5502.5,
          text: 'PH',
        }),
      }),
      expect.objectContaining({
        indicatorId: 'STD;Zig_Zag',
        type: 'plot',
        name: 'pivotHigh',
        eventTime: '2026-06-25T13:35:00.000Z',
        availabilityTime: '2026-06-25T13:40:00.000Z',
        repaintingRisk: 'repainting-risk',
        value: 5503,
      }),
      expect.objectContaining({
        indicatorId: 'STD;Zig_Zag',
        type: 'profile',
        name: 'volume_profile',
        eventTime: '2026-06-25T13:30:00.000Z',
        availabilityTime: '2026-06-25T13:40:00.000Z',
        repaintingRisk: 'repainting-risk',
        value: expect.objectContaining({
          priceLow: 5498,
          priceHigh: 5504,
          rate: [0.2, 0.8],
        }),
      }),
    ]));
    expect(TradingView.datasetContract.validateDataset(dataset)).toEqual({
      valid: true,
      errors: [],
    });
  });

  it('ignores studies outside the curated indicator allowlist', () => {
    const features = TradingView.collector.indicatorStudiesToFeatures({
      bars,
      studies: [
        {
          indicatorId: 'UNSAFE;Experimental',
          periods: [
            {
              $time: Date.parse('2026-06-25T13:35:00.000Z') / 1000,
              signal: 1,
            },
          ],
        },
      ],
    });

    expect(features).toEqual([]);
  });

  it('keeps LuxAlgo ICT/SMC out of the default curated indicator allowlist', () => {
    const luxAlgoIds = TradingView.collector.LUXALGO_ICT_SMC_OPT_IN_ALLOWLIST
      .map((indicator) => indicator.id);

    expect(TradingView.collector.CURATED_INDICATOR_ALLOWLIST.map((indicator) => indicator.id))
      .not.toEqual(expect.arrayContaining(luxAlgoIds));
  });

  it('collects LuxAlgo ICT/SMC through the opt-in widgetdata smoke path', async () => {
    const outputPath = fs.mkdtempSync(path.join(os.tmpdir(), 'tv-es-rth-luxalgo-'));
    const [luxAlgoIctSmc] = TradingView.collector.LUXALGO_ICT_SMC_OPT_IN_ALLOWLIST;
    const client = makeMockClient([
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
    ], {}, {
      [luxAlgoIctSmc.id]: {
        graphic: {
          labels: [
            {
              id: 41,
              x: 0,
              y: 5502.5,
              text: 'BOS',
              style: 'label_down',
            },
          ],
          boxes: [
            {
              id: 42,
              name: 'bullish_order_block',
              x1: 0,
              y1: 5502.5,
              x2: 1,
              y2: 5498.75,
              text: 'Bullish OB',
            },
          ],
        },
      },
    });
    let clientOptions;

    const result = await TradingView.collector.collectEsRth5mLuxAlgoIctSmcDataset({
      createClient: (options) => {
        clientOptions = options;
        return client;
      },
      outputPath,
      now: new Date('2026-06-28T12:00:00.000Z'),
      minBars: 2,
      timeoutMs: 1000,
      resolveIndicator: (indicator) => Promise.resolve({ id: indicator.id }),
    });

    expect(clientOptions).toMatchObject({ server: 'widgetdata' });
    expect(result.validation).toEqual({ valid: true, errors: [] });
    expect(result.dataset.manifest.collection).toEqual({
      kind: 'luxalgo-ict-smc-opt-in',
      tradingViewBackend: 'widgetdata',
      optIn: true,
    });
    expect(result.dataset.manifest.indicators).toEqual([
      expect.objectContaining({
        id: luxAlgoIctSmc.id,
        name: 'LuxAlgo ICT/SMC',
        version: '7',
        repaintingRisk: 'repainting-risk',
      }),
    ]);
    expect(result.dataset.features).toEqual(expect.arrayContaining([
      expect.objectContaining({
        indicatorId: luxAlgoIctSmc.id,
        type: 'label',
        name: 'BOS',
        eventTime: '2026-06-25T13:30:00.000Z',
        availabilityTime: '2026-06-25T13:35:00.000Z',
        repaintingRisk: 'repainting-risk',
        value: expect.objectContaining({
          text: 'BOS',
        }),
      }),
      expect.objectContaining({
        indicatorId: luxAlgoIctSmc.id,
        type: 'box',
        name: 'bullish_order_block',
        eventTime: '2026-06-25T13:30:00.000Z',
        availabilityTime: '2026-06-25T13:35:00.000Z',
        repaintingRisk: 'repainting-risk',
        value: expect.objectContaining({
          top: 5502.5,
          bottom: 5498.75,
          text: 'Bullish OB',
        }),
      }),
    ]));
    expect(TradingView.datasetContract.readDatasetSync(outputPath)).toEqual(result.dataset);
  });

  it('collects allowlisted TradingView studies into the exported dataset', async () => {
    const outputPath = fs.mkdtempSync(path.join(os.tmpdir(), 'tv-es-rth-study-'));
    const client = makeMockClient([
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
    ], {}, {
      'STD;Supertrend': {
        periods: [
          {
            $time: Date.parse('2026-06-25T13:35:00.000Z') / 1000,
            direction: -1,
          },
        ],
      },
      'STD;Zig_Zag': {
        graphic: {
          labels: [
            {
              id: 31,
              x: 0,
              y: 5502.5,
              text: 'PH',
            },
          ],
        },
      },
    });

    const result = await TradingView.collector.collectEsRth5mDataset({
      createClient: () => client,
      outputPath,
      now: new Date('2026-06-28T12:00:00.000Z'),
      minBars: 2,
      timeoutMs: 1000,
      resolveIndicator: (indicator) => Promise.resolve({ id: indicator.id }),
    });

    expect(result.validation).toEqual({ valid: true, errors: [] });
    expect(result.dataset.features).toEqual(expect.arrayContaining([
      expect.objectContaining({
        indicatorId: 'STD;Supertrend',
        type: 'plot',
        name: 'direction',
        eventTime: '2026-06-25T13:35:00.000Z',
        availabilityTime: '2026-06-25T13:35:00.000Z',
        repaintingRisk: 'confirmed',
        value: -1,
      }),
      expect.objectContaining({
        indicatorId: 'STD;Zig_Zag',
        type: 'label',
        name: 'PH',
        eventTime: '2026-06-25T13:30:00.000Z',
        availabilityTime: '2026-06-25T13:35:00.000Z',
        repaintingRisk: 'repainting-risk',
      }),
    ]));
    expect(TradingView.datasetContract.readDatasetSync(outputPath)).toEqual(result.dataset);
  });

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
      {
        time: Date.parse('2026-06-26T13:30:00.000Z') / 1000,
        open: 5514.25,
        max: 5516,
        min: 5513.75,
        close: 5515.5,
        volume: 1180,
      },
      {
        time: Date.parse('2026-06-26T13:35:00.000Z') / 1000,
        open: 5515.5,
        max: 5517,
        min: 5514.5,
        close: 5516.25,
        volume: 1020,
      },
    ]);

    const result = await TradingView.collector.collectEsRth5mDataset({
      createClient: () => client,
      outputPath,
      now: new Date('2026-06-28T12:00:00.000Z'),
      minBars: 2,
      timeoutMs: 1000,
      includeIndicatorFeatures: false,
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
        flatBeforeCloseMinutes: 5,
        sessions: [
          {
            id: '2026-06-25',
            firstBarTime: '2026-06-25T13:30:00.000Z',
            lastBarTime: '2026-06-25T13:35:00.000Z',
            flatBeforeCloseTime: '2026-06-25T19:55:00.000Z',
            barCount: 2,
          },
          {
            id: '2026-06-26',
            firstBarTime: '2026-06-26T13:30:00.000Z',
            lastBarTime: '2026-06-26T13:35:00.000Z',
            flatBeforeCloseTime: '2026-06-26T19:55:00.000Z',
            barCount: 2,
          },
        ],
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
      {
        time: '2026-06-26T13:30:00.000Z',
        open: 5514.25,
        high: 5516,
        low: 5513.75,
        close: 5515.5,
        volume: 1180,
      },
      {
        time: '2026-06-26T13:35:00.000Z',
        open: 5515.5,
        high: 5517,
        low: 5514.5,
        close: 5516.25,
        volume: 1020,
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

  it('exports a mocked ES RTH 15m collection as a valid dataset', async () => {
    const outputPath = fs.mkdtempSync(path.join(os.tmpdir(), 'tv-es-rth-15m-'));
    const client = makeMockClient([
      {
        time: Date.parse('2026-06-25T13:15:00.000Z') / 1000,
        open: 5499,
        max: 5500,
        min: 5498,
        close: 5499.5,
        volume: 200,
      },
      {
        time: Date.parse('2026-06-25T13:30:00.000Z') / 1000,
        open: 5500.25,
        max: 5508.5,
        min: 5498.75,
        close: 5506,
        volume: 3200,
      },
      {
        time: Date.parse('2026-06-25T13:45:00.000Z') / 1000,
        open: 5506,
        max: 5510,
        min: 5504.5,
        close: 5508.25,
        volume: 2980,
      },
      {
        time: Date.parse('2026-06-26T13:30:00.000Z') / 1000,
        open: 5514.25,
        max: 5520,
        min: 5513.75,
        close: 5518.5,
        volume: 3180,
      },
      {
        time: Date.parse('2026-06-26T13:45:00.000Z') / 1000,
        open: 5518.5,
        max: 5521,
        min: 5516.5,
        close: 5519.25,
        volume: 3020,
      },
    ]);

    const result = await TradingView.collector.collectEsRth15mDataset({
      createClient: () => client,
      outputPath,
      now: new Date('2026-06-28T12:00:00.000Z'),
      minBars: 2,
      timeoutMs: 1000,
      includeIndicatorFeatures: false,
    });

    expect(result.validation).toEqual({ valid: true, errors: [] });
    expect(result.dataset.manifest).toMatchObject({
      schemaVersion: 1,
      datasetId: 'es-rth-15m-2026-06-28T12-00-00-000Z',
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
        flatBeforeCloseMinutes: 5,
        sessions: [
          {
            id: '2026-06-25',
            firstBarTime: '2026-06-25T13:30:00.000Z',
            lastBarTime: '2026-06-25T13:45:00.000Z',
            flatBeforeCloseTime: '2026-06-25T19:55:00.000Z',
            barCount: 2,
          },
          {
            id: '2026-06-26',
            firstBarTime: '2026-06-26T13:30:00.000Z',
            lastBarTime: '2026-06-26T13:45:00.000Z',
            flatBeforeCloseTime: '2026-06-26T19:55:00.000Z',
            barCount: 2,
          },
        ],
      },
      bar: {
        interval: '15m',
        priceScale: 100,
        volumeUnit: 'contracts',
      },
      contract: {
        type: 'continuous_futures',
        continuous: true,
      },
    });
    expect(result.dataset.bars.map((bar) => bar.time)).toEqual([
      '2026-06-25T13:30:00.000Z',
      '2026-06-25T13:45:00.000Z',
      '2026-06-26T13:30:00.000Z',
      '2026-06-26T13:45:00.000Z',
    ]);

    expect(TradingView.datasetContract.readDatasetSync(outputPath)).toEqual(result.dataset);
    expect(client.ended).toBe(true);
    expect(client.charts[0].lastMarket).toEqual({
      symbol: 'CME_MINI:ES1!',
      options: {
        timeframe: '15',
        range: 26,
        to: undefined,
        session: 'regular',
        backadjustment: true,
      },
    });
  });
});
