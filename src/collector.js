const fs = require('fs');
const path = require('path');
const Client = require('./client');
const datasetContract = require('./datasetContract');

const ES_RTH_5M_DEFAULTS = {
  symbol: 'CME_MINI:ES1!',
  root: 'ES',
  assetClass: 'equity_index_futures',
  interval: '5m',
  tradingViewTimeframe: '5',
  sessionName: 'RTH',
  timezone: 'America/New_York',
  sessionStart: '09:30',
  sessionEnd: '16:00',
  volumeUnit: 'contracts',
  range: 78,
  minBars: 1,
};

function toIsoTimestamp(seconds) {
  return new Date(seconds * 1000).toISOString();
}

function stableDatasetId(prefix, date) {
  return `${prefix}-${date.toISOString().replace(/[:.]/g, '-')}`;
}

function minutesFromClock(value) {
  const [hours, minutes] = value.split(':').map(Number);
  return (hours * 60) + minutes;
}

function zonedClockMinutes(isoTimestamp, timezone) {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: timezone,
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
  }).formatToParts(new Date(isoTimestamp));

  const hour = Number(parts.find((part) => part.type === 'hour')?.value);
  const minute = Number(parts.find((part) => part.type === 'minute')?.value);
  const normalizedHour = hour === 24 ? 0 : hour;

  return (normalizedHour * 60) + minute;
}

function isInsideSession(isoTimestamp, session) {
  const value = zonedClockMinutes(isoTimestamp, session.timezone);
  return value >= minutesFromClock(session.start)
    && value < minutesFromClock(session.end);
}

function toContractBar(period) {
  return {
    time: toIsoTimestamp(period.time),
    open: period.open,
    high: period.max,
    low: period.min,
    close: period.close,
    volume: period.volume,
  };
}

function newestContiguousRun(bars, intervalMs, minBars) {
  const runs = [];
  let current = [];

  bars.forEach((bar) => {
    if (current.length === 0) {
      current.push(bar);
      return;
    }

    const previous = current[current.length - 1];
    const gap = Date.parse(bar.time) - Date.parse(previous.time);
    if (gap === intervalMs) {
      current.push(bar);
      return;
    }

    runs.push(current);
    current = [bar];
  });

  if (current.length > 0) runs.push(current);

  const eligible = runs.filter((run) => run.length >= minBars);
  if (eligible.length === 0) return [];

  return eligible[eligible.length - 1];
}

function periodsToRthBars(periods, options = {}) {
  const session = {
    timezone: options.timezone || ES_RTH_5M_DEFAULTS.timezone,
    start: options.sessionStart || ES_RTH_5M_DEFAULTS.sessionStart,
    end: options.sessionEnd || ES_RTH_5M_DEFAULTS.sessionEnd,
  };

  const bars = periods
    .map(toContractBar)
    .filter((bar) => isInsideSession(bar.time, session))
    .sort((left, right) => Date.parse(left.time) - Date.parse(right.time));

  return newestContiguousRun(
    bars,
    5 * 60 * 1000,
    options.minBars || ES_RTH_5M_DEFAULTS.minBars,
  );
}

function buildEsRth5mDataset({
  bars,
  infos = {},
  now = new Date(),
  datasetId,
} = {}) {
  const collectedAt = now instanceof Date ? now : new Date(now);

  return {
    manifest: {
      schemaVersion: 1,
      datasetId: datasetId || stableDatasetId('es-rth-5m', collectedAt),
      collectedAt: collectedAt.toISOString(),
      source: 'tradingview',
      symbol: {
        ticker: ES_RTH_5M_DEFAULTS.symbol,
        root: ES_RTH_5M_DEFAULTS.root,
        assetClass: ES_RTH_5M_DEFAULTS.assetClass,
      },
      session: {
        name: ES_RTH_5M_DEFAULTS.sessionName,
        timezone: ES_RTH_5M_DEFAULTS.timezone,
        start: ES_RTH_5M_DEFAULTS.sessionStart,
        end: ES_RTH_5M_DEFAULTS.sessionEnd,
      },
      bar: {
        interval: ES_RTH_5M_DEFAULTS.interval,
        priceScale: infos.pricescale || 100,
        volumeUnit: ES_RTH_5M_DEFAULTS.volumeUnit,
      },
      contract: {
        type: 'continuous_futures',
        continuous: true,
        rollPolicy: {
          source: 'tradingview',
          description: 'TradingView continuous futures contract ES1! with provider-managed roll construction.',
        },
      },
      indicators: [],
    },
    bars,
    features: [],
  };
}

function writeJsonSync(filePath, value) {
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`);
}

function writeVersionedDatasetSync(outputPath, dataset) {
  fs.mkdirSync(outputPath, { recursive: true });
  writeJsonSync(path.join(outputPath, 'manifest.json'), dataset.manifest);
  writeJsonSync(path.join(outputPath, 'bars.json'), dataset.bars);
  writeJsonSync(path.join(outputPath, 'features.json'), dataset.features);
}

function waitForChartBars(chart, { minBars, timeoutMs }) {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      reject(new Error(`Timed out waiting for at least ${minBars} RTH bars from TradingView`));
    }, timeoutMs);

    const tryResolve = () => {
      const bars = periodsToRthBars(chart.periods || [], { minBars });
      if (bars.length < minBars) return;

      clearTimeout(timeout);
      resolve(bars);
    };

    chart.onUpdate(tryResolve);
    chart.onSymbolLoaded(tryResolve);
    chart.onError((...error) => {
      clearTimeout(timeout);
      reject(new Error(`TradingView chart error: ${error.join(' ')}`));
    });
  });
}

async function collectEsRth5mDataset({
  createClient,
  outputPath,
  now = new Date(),
  range = ES_RTH_5M_DEFAULTS.range,
  minBars = ES_RTH_5M_DEFAULTS.minBars,
  timeoutMs = 30000,
  to,
} = {}) {
  if (!outputPath) throw new Error('outputPath is required');

  const client = createClient ? createClient() : new Client({
    token: process.env.SESSION,
    signature: process.env.SIGNATURE,
  });
  const chart = new client.Session.Chart();

  try {
    const barsPromise = waitForChartBars(chart, { minBars, timeoutMs });
    chart.setMarket(ES_RTH_5M_DEFAULTS.symbol, {
      timeframe: ES_RTH_5M_DEFAULTS.tradingViewTimeframe,
      range,
      to,
      session: 'regular',
      backadjustment: true,
    });

    const bars = await barsPromise;
    const dataset = buildEsRth5mDataset({
      bars,
      infos: chart.infos,
      now,
    });
    const validation = datasetContract.validateDataset(dataset);

    if (!validation.valid) {
      throw new Error(`Collected dataset failed validation: ${JSON.stringify(validation.errors)}`);
    }

    writeVersionedDatasetSync(outputPath, dataset);

    return {
      outputPath,
      dataset,
      validation,
    };
  } finally {
    if (chart.delete) chart.delete();
    if (client.end) await client.end();
  }
}

module.exports = {
  collectEsRth5mDataset,
  buildEsRth5mDataset,
  periodsToRthBars,
  writeVersionedDatasetSync,
};
