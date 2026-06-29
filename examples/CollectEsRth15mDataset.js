const path = require('path');
const TradingView = require('../main');

function readArg(name, fallback) {
  const prefix = `--${name}=`;
  const value = process.argv.find((arg) => arg.startsWith(prefix));
  return value ? value.slice(prefix.length) : fallback;
}

async function main() {
  const outputPath = path.resolve(
    process.cwd(),
    readArg('output', 'datasets/es-rth-15m-latest'),
  );
  const to = readArg('to', undefined);
  const collectedAt = readArg('collected-at', undefined);

  const result = await TradingView.collector.collectEsRth15mDataset({
    outputPath,
    now: collectedAt ? new Date(collectedAt) : undefined,
    to: to ? Number(to) : undefined,
    minBars: Number(readArg('min-bars', '1')),
    range: Number(readArg('range', '26')),
  });

  console.log(`Wrote ${result.dataset.bars.length} ES RTH 15m bars to ${result.outputPath}`);
  console.log(JSON.stringify(result.validation, null, 2));
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
