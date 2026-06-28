const fs = require('fs');
const path = require('path');

function readJsonSync(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

function readDatasetSync(datasetPath) {
  return {
    manifest: readJsonSync(path.join(datasetPath, 'manifest.json')),
    bars: readJsonSync(path.join(datasetPath, 'bars.json')),
    features: readJsonSync(path.join(datasetPath, 'features.json')),
  };
}

function isIsoTimestamp(value) {
  if (typeof value !== 'string') return false;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) && new Date(parsed).toISOString() === value;
}

function addError(errors, pathName, message) {
  errors.push({ path: pathName, message });
}

function requireObject(errors, value, pathName) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    addError(errors, pathName, 'must be an object');
    return false;
  }
  return true;
}

function requireString(errors, value, pathName) {
  if (typeof value !== 'string' || value.length === 0) {
    addError(errors, pathName, 'must be a non-empty string');
  }
}

function requireNumber(errors, value, pathName) {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    addError(errors, pathName, 'must be a finite number');
  }
}

function requireTimestamp(errors, value, pathName) {
  if (!isIsoTimestamp(value)) {
    addError(errors, pathName, 'must be an ISO timestamp');
  }
}

function isValidTimestampPair(eventTime, availabilityTime) {
  return isIsoTimestamp(eventTime)
    && isIsoTimestamp(availabilityTime)
    && Date.parse(availabilityTime) >= Date.parse(eventTime);
}

function validateManifest(manifest, errors) {
  if (!requireObject(errors, manifest, 'manifest')) return;

  if (manifest.schemaVersion !== 1) {
    addError(errors, 'manifest.schemaVersion', 'must be 1');
  }

  requireString(errors, manifest.datasetId, 'manifest.datasetId');
  requireTimestamp(errors, manifest.collectedAt, 'manifest.collectedAt');

  if (manifest.source !== 'tradingview') {
    addError(errors, 'manifest.source', 'must be tradingview');
  }

  if (requireObject(errors, manifest.symbol, 'manifest.symbol')) {
    requireString(errors, manifest.symbol.ticker, 'manifest.symbol.ticker');
    requireString(errors, manifest.symbol.root, 'manifest.symbol.root');
    requireString(errors, manifest.symbol.assetClass, 'manifest.symbol.assetClass');
  }

  if (requireObject(errors, manifest.session, 'manifest.session')) {
    requireString(errors, manifest.session.name, 'manifest.session.name');
    requireString(errors, manifest.session.timezone, 'manifest.session.timezone');
    requireString(errors, manifest.session.start, 'manifest.session.start');
    requireString(errors, manifest.session.end, 'manifest.session.end');
  }

  if (requireObject(errors, manifest.bar, 'manifest.bar')) {
    if (!['5m', '15m'].includes(manifest.bar.interval)) {
      addError(errors, 'manifest.bar.interval', 'must be 5m or 15m');
    }
    requireNumber(errors, manifest.bar.priceScale, 'manifest.bar.priceScale');
    requireString(errors, manifest.bar.volumeUnit, 'manifest.bar.volumeUnit');
  }

  if (requireObject(errors, manifest.contract, 'manifest.contract')) {
    if (manifest.contract.type !== 'continuous_futures') {
      addError(errors, 'manifest.contract.type', 'must be continuous_futures');
    }
    if (manifest.contract.continuous !== true) {
      addError(errors, 'manifest.contract.continuous', 'must be true');
    }
    requireObject(errors, manifest.contract.rollPolicy, 'manifest.contract.rollPolicy');
  }

  if (!Array.isArray(manifest.indicators)) {
    addError(errors, 'manifest.indicators', 'must be an array');
  } else {
    manifest.indicators.forEach((indicator, index) => {
      const pathName = `manifest.indicators[${index}]`;
      if (!requireObject(errors, indicator, pathName)) return;

      requireString(errors, indicator.id, `${pathName}.id`);
      requireString(errors, indicator.name, `${pathName}.name`);
      requireString(errors, indicator.version, `${pathName}.version`);
      if (!['confirmed', 'repainting-risk'].includes(indicator.repaintingRisk)) {
        addError(
          errors,
          `${pathName}.repaintingRisk`,
          'must be confirmed or repainting-risk',
        );
      }
    });
  }
}

function intervalToMs(interval) {
  if (interval === '5m') return 5 * 60 * 1000;
  if (interval === '15m') return 15 * 60 * 1000;
  return null;
}

function validateBars(bars, errors, interval) {
  if (!Array.isArray(bars)) {
    addError(errors, 'bars', 'must be an array');
    return;
  }

  bars.forEach((bar, index) => {
    const pathName = `bars[${index}]`;
    if (!requireObject(errors, bar, pathName)) return;

    requireTimestamp(errors, bar.time, `${pathName}.time`);
    ['open', 'high', 'low', 'close', 'volume'].forEach((field) => {
      requireNumber(errors, bar[field], `${pathName}.${field}`);
    });
  });

  const expectedGap = intervalToMs(interval);
  if (!expectedGap) return;

  for (let index = 1; index < bars.length; index += 1) {
    const previous = bars[index - 1];
    const current = bars[index];
    if (!isIsoTimestamp(previous?.time) || !isIsoTimestamp(current?.time)) continue;

    const actualGap = Date.parse(current.time) - Date.parse(previous.time);
    if (actualGap !== expectedGap) {
      addError(
        errors,
        `bars[${index}].time`,
        `must be ${expectedGap / 60000} minutes after bars[${index - 1}].time`,
      );
    }
  }
}

function validateFeatures(features, errors) {
  if (!Array.isArray(features)) {
    addError(errors, 'features', 'must be an array');
    return;
  }

  features.forEach((feature, index) => {
    const pathName = `features[${index}]`;
    if (!requireObject(errors, feature, pathName)) return;

    requireString(errors, feature.id, `${pathName}.id`);
    requireString(errors, feature.source, `${pathName}.source`);
    requireString(errors, feature.indicatorId, `${pathName}.indicatorId`);
    requireString(errors, feature.type, `${pathName}.type`);
    requireString(errors, feature.name, `${pathName}.name`);
    requireTimestamp(errors, feature.eventTime, `${pathName}.eventTime`);
    requireTimestamp(errors, feature.availabilityTime, `${pathName}.availabilityTime`);
    if (!Object.prototype.hasOwnProperty.call(feature, 'value')) {
      addError(errors, `${pathName}.value`, 'must be present');
    }

    if (
      isIsoTimestamp(feature.eventTime)
      && isIsoTimestamp(feature.availabilityTime)
      && !isValidTimestampPair(feature.eventTime, feature.availabilityTime)
    ) {
      addError(
        errors,
        `${pathName}.availabilityTime`,
        'must be on or after eventTime',
      );
    }

    if (!['confirmed', 'repainting-risk'].includes(feature.repaintingRisk)) {
      addError(
        errors,
        `${pathName}.repaintingRisk`,
        'must be confirmed or repainting-risk',
      );
    }
  });
}

function validateDataset(dataset) {
  const errors = [];

  if (!requireObject(errors, dataset, 'dataset')) {
    return { valid: false, errors };
  }

  validateManifest(dataset.manifest, errors);
  validateBars(dataset.bars, errors, dataset.manifest?.bar?.interval);
  validateFeatures(dataset.features, errors);

  return {
    valid: errors.length === 0,
    errors,
  };
}

module.exports = {
  readDatasetSync,
  validateDataset,
};
