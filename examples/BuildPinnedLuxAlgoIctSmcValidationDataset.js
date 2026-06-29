const path = require('path');
const TradingView = require('../main');

function readArg(name, fallback) {
  const prefix = `--${name}=`;
  const value = process.argv.find((arg) => arg.startsWith(prefix));
  return value ? value.slice(prefix.length) : fallback;
}

function rthTime(sessionDate, barIndex) {
  const start = new Date(`${sessionDate}T13:30:00.000Z`);
  return new Date(start.getTime() + (barIndex * 5 * 60 * 1000)).toISOString();
}

function sessionBars(sessionDate, sessionIndex) {
  const base = 5500 + (sessionIndex * 12);
  return Array.from({ length: 78 }, (_, barIndex) => {
    const wave = barIndex % 6;
    const open = base + barIndex + (wave >= 3 ? 1.25 : 0);
    const close = open + (wave < 4 ? 0.75 : -0.5);
    return {
      time: rthTime(sessionDate, barIndex),
      open,
      high: Math.max(open, close) + 1,
      low: Math.min(open, close) - 1.5,
      close,
      volume: 900 + (sessionIndex * 50) + (barIndex * 7),
    };
  });
}

function luxAlgoGraphic(sessionDates) {
  const labels = [];
  const boxes = [];

  sessionDates.forEach((sessionDate, sessionIndex) => {
    const base = 5500 + (sessionIndex * 12);
    const offset = sessionIndex * 100;
    const barOffset = sessionIndex * 78;

    labels.push(
      {
        id: offset + 1,
        x: barOffset,
        y: base + 2,
        text: 'BOS',
        style: 'label_up',
        yLoc: 'price',
      },
      {
        id: offset + 2,
        x: barOffset + 76,
        y: base + 7,
        text: 'BOS',
        style: 'label_down',
        yLoc: 'price',
      },
      {
        id: offset + 3,
        x: barOffset + 60,
        y: base + 8,
        text: 'MSS',
        style: 'label_left',
        yLoc: 'price',
      },
    );

    boxes.push(
      {
        id: offset + 10,
        name: 'bullish_order_block',
        x1: barOffset,
        y1: base + 0.5,
        x2: barOffset + 2,
        y2: 5400,
        text: 'Bullish OB',
      },
      {
        id: offset + 11,
        name: 'bullish_fair_value_gap',
        x1: barOffset + 30,
        y1: base + 4.75,
        x2: barOffset + 31,
        y2: 5400,
        text: 'Bullish FVG',
      },
      {
        id: offset + 12,
        name: 'bullish_order_block',
        x1: barOffset + 62,
        text: 'Bullish OB missing geometry',
      },
    );
  });

  return { labels, boxes };
}

async function main() {
  const outputPath = path.resolve(
    process.cwd(),
    readArg('output', 'datasets/es-rth-5m-luxalgo-ict-smc-validation-2026-06-28'),
  );
  const sessionDates = ['2026-06-22', '2026-06-23', '2026-06-24', '2026-06-25', '2026-06-26'];
  const [luxAlgoIctSmc] = TradingView.collector.LUXALGO_ICT_SMC_OPT_IN_ALLOWLIST;
  const bars = sessionDates.flatMap((sessionDate, index) => sessionBars(sessionDate, index));
  const dataset = TradingView.collector.buildEsRth5mDataset({
    bars,
    now: new Date(readArg('collected-at', '2026-06-28T12:00:00.000Z')),
    datasetId: readArg('dataset-id', 'es-rth-5m-luxalgo-ict-smc-validation-2026-06-28'),
    indicatorAllowlist: TradingView.collector.LUXALGO_ICT_SMC_OPT_IN_ALLOWLIST,
    candidateSignalDerivation: {
      luxAlgoLiquidityZoneEntries: {
        confirmationMode: readArg('confirmation-mode', 'touch'),
        zonePreference: readArg('zone-preference', 'nearest-any'),
        maxBarsAfterStructureEvent: Number(readArg('max-bars-after-structure-event', '6')),
      },
    },
    collection: {
      kind: 'pinned-luxalgo-ict-smc-validation',
      source: 'examples/BuildPinnedLuxAlgoIctSmcValidationDataset.js',
      liveCollected: false,
    },
    indicatorStudies: [
      {
        indicatorId: luxAlgoIctSmc.id,
        graphic: luxAlgoGraphic(sessionDates),
      },
    ],
  });
  const validation = TradingView.datasetContract.validateDataset(dataset);
  if (!validation.valid) {
    throw new Error(`Pinned dataset failed validation: ${JSON.stringify(validation.errors)}`);
  }

  TradingView.collector.writeVersionedDatasetSync(outputPath, dataset);
  console.log(JSON.stringify({
    outputPath,
    datasetId: dataset.manifest.datasetId,
    bars: dataset.bars.length,
    features: dataset.features.length,
    diagnostics: dataset.derivationDiagnostics?.rules?.length || 0,
  }, null, 2));
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
