const fs = require('fs');
const path = require('path');

function readJsonSync(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

function readDatasetSync(datasetPath) {
  const dataset = {
    manifest: readJsonSync(path.join(datasetPath, 'manifest.json')),
    bars: readJsonSync(path.join(datasetPath, 'bars.json')),
    features: readJsonSync(path.join(datasetPath, 'features.json')),
  };
  const derivationDiagnosticsPath = path.join(datasetPath, 'derivation-diagnostics.json');

  if (fs.existsSync(derivationDiagnosticsPath)) {
    dataset.derivationDiagnostics = readJsonSync(derivationDiagnosticsPath);
  }

  return dataset;
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

function requireBoolean(errors, value, pathName) {
  if (typeof value !== 'boolean') {
    addError(errors, pathName, 'must be a boolean');
  }
}

function requireArray(errors, value, pathName) {
  if (!Array.isArray(value)) {
    addError(errors, pathName, 'must be an array');
    return false;
  }
  return true;
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
    if (manifest.session.flatBeforeCloseMinutes !== undefined) {
      requireNumber(
        errors,
        manifest.session.flatBeforeCloseMinutes,
        'manifest.session.flatBeforeCloseMinutes',
      );
    }
    validateSessionInstances(manifest.session, errors);
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

function minutesFromClock(value) {
  const [hours, minutes] = value.split(':').map(Number);
  return (hours * 60) + minutes;
}

function zonedParts(isoTimestamp, timezone) {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: timezone,
    hour12: false,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).formatToParts(new Date(isoTimestamp));

  const part = (type) => parts.find((item) => item.type === type)?.value;
  const hour = Number(part('hour'));
  const normalizedHour = hour === 24 ? 0 : hour;

  return {
    date: `${part('year')}-${part('month')}-${part('day')}`,
    minutes: (normalizedHour * 60) + Number(part('minute')),
  };
}

function barSessionId(bar, session) {
  if (
    !session
    || typeof session.timezone !== 'string'
    || typeof session.start !== 'string'
    || typeof session.end !== 'string'
    || !isIsoTimestamp(bar?.time)
  ) {
    return null;
  }

  const parts = zonedParts(bar.time, session.timezone);
  const start = minutesFromClock(session.start);
  const end = minutesFromClock(session.end);

  if (parts.minutes < start || parts.minutes >= end) return null;
  return parts.date;
}

function validateSessionInstances(session, errors) {
  if (session.sessions === undefined) return;

  if (!Array.isArray(session.sessions)) {
    addError(errors, 'manifest.session.sessions', 'must be an array');
    return;
  }

  session.sessions.forEach((entry, index) => {
    const pathName = `manifest.session.sessions[${index}]`;
    if (!requireObject(errors, entry, pathName)) return;

    requireString(errors, entry.id, `${pathName}.id`);
    requireTimestamp(errors, entry.firstBarTime, `${pathName}.firstBarTime`);
    requireTimestamp(errors, entry.lastBarTime, `${pathName}.lastBarTime`);
    requireTimestamp(errors, entry.flatBeforeCloseTime, `${pathName}.flatBeforeCloseTime`);
    requireNumber(errors, entry.barCount, `${pathName}.barCount`);

    if (
      isIsoTimestamp(entry.firstBarTime)
      && isIsoTimestamp(entry.lastBarTime)
      && Date.parse(entry.lastBarTime) < Date.parse(entry.firstBarTime)
    ) {
      addError(errors, `${pathName}.lastBarTime`, 'must be on or after firstBarTime');
    }
  });
}

function validateBars(bars, errors, interval, session) {
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

    if (
      session?.timezone
      && session?.start
      && session?.end
      && isIsoTimestamp(bar.time)
      && !barSessionId(bar, session)
    ) {
      addError(errors, `${pathName}.time`, 'must be inside the declared RTH session');
    }
  });

  const expectedGap = intervalToMs(interval);
  if (!expectedGap) return;

  for (let index = 1; index < bars.length; index += 1) {
    const previous = bars[index - 1];
    const current = bars[index];
    if (!isIsoTimestamp(previous?.time) || !isIsoTimestamp(current?.time)) continue;

    const actualGap = Date.parse(current.time) - Date.parse(previous.time);
    const previousSessionId = barSessionId(previous, session);
    const currentSessionId = barSessionId(current, session);
    const isSessionBoundary = previousSessionId
      && currentSessionId
      && previousSessionId !== currentSessionId;

    if (isSessionBoundary && actualGap > expectedGap) continue;

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

    if (feature.type === 'signal') {
      requireBoolean(errors, feature.value, `${pathName}.value`);
    }

    if (feature.metadata !== undefined) {
      validateFeatureMetadata(feature.metadata, errors, `${pathName}.metadata`);
    }
  });
}

function validateFeatureMetadata(metadata, errors, pathName) {
  if (!requireObject(errors, metadata, pathName)) return;

  if (metadata.provenance === undefined) return;
  if (!requireObject(errors, metadata.provenance, `${pathName}.provenance`)) return;

  const { provenance } = metadata;

  if (provenance.sourceFeatureIds !== undefined) {
    if (!Array.isArray(provenance.sourceFeatureIds) || provenance.sourceFeatureIds.length === 0) {
      addError(errors, `${pathName}.provenance.sourceFeatureIds`, 'must be a non-empty array');
    } else {
      provenance.sourceFeatureIds.forEach((featureId, index) => {
        requireString(
          errors,
          featureId,
          `${pathName}.provenance.sourceFeatureIds[${index}]`,
        );
      });
    }
  }

  if (provenance.derivation !== undefined) {
    if (requireObject(errors, provenance.derivation, `${pathName}.provenance.derivation`)) {
      requireString(errors, provenance.derivation.rule, `${pathName}.provenance.derivation.rule`);
      requireString(
        errors,
        provenance.derivation.version,
        `${pathName}.provenance.derivation.version`,
      );
    }
  }

  if (provenance.directionEvidence !== undefined) {
    if (
      requireObject(
        errors,
        provenance.directionEvidence,
        `${pathName}.provenance.directionEvidence`,
      )
    ) {
      requireString(
        errors,
        provenance.directionEvidence.direction,
        `${pathName}.provenance.directionEvidence.direction`,
      );
      requireString(
        errors,
        provenance.directionEvidence.evidenceType,
        `${pathName}.provenance.directionEvidence.evidenceType`,
      );
      requireString(
        errors,
        provenance.directionEvidence.evidenceValue,
        `${pathName}.provenance.directionEvidence.evidenceValue`,
      );
    }
  }
}

function validateDataset(dataset) {
  const errors = [];

  if (!requireObject(errors, dataset, 'dataset')) {
    return { valid: false, errors };
  }

  validateManifest(dataset.manifest, errors);
  validateBars(dataset.bars, errors, dataset.manifest?.bar?.interval, dataset.manifest?.session);
  validateFeatures(dataset.features, errors);
  validateDerivationDiagnostics(dataset.derivationDiagnostics, errors);

  return {
    valid: errors.length === 0,
    errors,
  };
}

function validateDerivationDiagnostics(derivationDiagnostics, errors) {
  if (derivationDiagnostics === undefined) return;
  if (!requireObject(errors, derivationDiagnostics, 'derivationDiagnostics')) return;

  if (derivationDiagnostics.schemaVersion !== 1) {
    addError(errors, 'derivationDiagnostics.schemaVersion', 'must be 1');
  }

  if (!requireArray(errors, derivationDiagnostics.rules, 'derivationDiagnostics.rules')) return;

  derivationDiagnostics.rules.forEach((ruleDiagnostics, ruleIndex) => {
    const pathName = `derivationDiagnostics.rules[${ruleIndex}]`;
    if (!requireObject(errors, ruleDiagnostics, pathName)) return;

    requireString(errors, ruleDiagnostics.rule, `${pathName}.rule`);
    requireString(errors, ruleDiagnostics.version, `${pathName}.version`);

    if (requireObject(errors, ruleDiagnostics.counts, `${pathName}.counts`)) {
      Object.entries(ruleDiagnostics.counts).forEach(([countName, countValue]) => {
        requireNumber(errors, countValue, `${pathName}.counts.${countName}`);
      });
    }

    if (requireArray(errors, ruleDiagnostics.warnings, `${pathName}.warnings`)) {
      ruleDiagnostics.warnings.forEach((warning, warningIndex) => {
        requireString(errors, warning, `${pathName}.warnings[${warningIndex}]`);
      });
    }

    if (requireArray(errors, ruleDiagnostics.examples, `${pathName}.examples`)) {
      ruleDiagnostics.examples.forEach((example, exampleIndex) => {
        const examplePath = `${pathName}.examples[${exampleIndex}]`;
        if (!requireObject(errors, example, examplePath)) return;

        requireString(errors, example.kind, `${examplePath}.kind`);
        requireString(errors, example.indicatorId, `${examplePath}.indicatorId`);
        requireString(errors, example.sourceFeatureId, `${examplePath}.sourceFeatureId`);
        requireString(errors, example.featureType, `${examplePath}.featureType`);
        requireString(errors, example.featureName, `${examplePath}.featureName`);
        requireString(errors, example.reason, `${examplePath}.reason`);
        if (example.eventTime !== undefined) {
          requireTimestamp(errors, example.eventTime, `${examplePath}.eventTime`);
        }
        if (example.evidence !== undefined) {
          requireObject(errors, example.evidence, `${examplePath}.evidence`);
        }
      });
    }
  });
}

module.exports = {
  readDatasetSync,
  validateDataset,
};
