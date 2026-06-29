const fs = require('fs');
const path = require('path');
const Client = require('./client');
const datasetContract = require('./datasetContract');
const miscRequests = require('./miscRequests');

const ES_RTH_BASE_DEFAULTS = {
  symbol: 'CME_MINI:ES1!',
  root: 'ES',
  assetClass: 'equity_index_futures',
  sessionName: 'RTH',
  timezone: 'America/New_York',
  sessionStart: '09:30',
  sessionEnd: '16:00',
  flatBeforeCloseMinutes: 5,
  volumeUnit: 'contracts',
  minBars: 1,
};

const ES_RTH_5M_DEFAULTS = {
  ...ES_RTH_BASE_DEFAULTS,
  interval: '5m',
  datasetPrefix: 'es-rth-5m',
  tradingViewTimeframe: '5',
  range: 78,
};

const ES_RTH_15M_DEFAULTS = {
  ...ES_RTH_BASE_DEFAULTS,
  interval: '15m',
  datasetPrefix: 'es-rth-15m',
  tradingViewTimeframe: '15',
  range: 26,
};

const CURATED_INDICATOR_ALLOWLIST = [
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
];

const LUXALGO_ICT_SMC_COLLECTION_KIND = 'luxalgo-ict-smc-opt-in';
const LUXALGO_ICT_SMC_TRADINGVIEW_BACKEND = 'widgetdata';
const LUXALGO_STRUCTURE_SIGNAL_DERIVATION_RULE = 'luxalgo-structure-event-direction';
const LUXALGO_STRUCTURE_SIGNAL_DERIVATION_VERSION = '1';
const LUXALGO_LIQUIDITY_ZONE_ENTRY_DERIVATION_RULE = 'luxalgo-liquidity-zone-entry';
const LUXALGO_LIQUIDITY_ZONE_ENTRY_DERIVATION_VERSION = '1';
const DERIVATION_DIAGNOSTICS_FILE = 'derivation-diagnostics.json';
const LUXALGO_ICT_SMC_OPT_IN_ALLOWLIST = [
  {
    id: 'PUB;6daafb2cabe6419d98ae25229d2327f8',
    name: 'LuxAlgo ICT/SMC',
    version: '7',
    repaintingRisk: 'repainting-risk',
  },
];

function toIsoTimestamp(seconds) {
  return new Date(seconds * 1000).toISOString();
}

function toIsoFeatureTimestamp(value, bars = []) {
  if (value instanceof Date) return value.toISOString();
  if (typeof value === 'string') return new Date(value).toISOString();
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  if (Number.isInteger(value) && value >= 0 && value < bars.length) return bars[value].time;

  return new Date(value > 1000000000000 ? value : value * 1000).toISOString();
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

function zonedDate(isoTimestamp, timezone) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: timezone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date(isoTimestamp));

  const part = (type) => parts.find((item) => item.type === type)?.value;
  return `${part('year')}-${part('month')}-${part('day')}`;
}

function isInsideSession(isoTimestamp, session) {
  const value = zonedClockMinutes(isoTimestamp, session.timezone);
  return value >= minutesFromClock(session.start)
    && value < minutesFromClock(session.end);
}

function zonedOffsetMs(date, timezone) {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: timezone,
    hour12: false,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).formatToParts(date);

  const part = (type) => parts.find((item) => item.type === type)?.value;
  const hour = Number(part('hour'));
  const normalizedHour = hour === 24 ? 0 : hour;
  const zonedAsUtc = Date.UTC(
    Number(part('year')),
    Number(part('month')) - 1,
    Number(part('day')),
    normalizedHour,
    Number(part('minute')),
    Number(part('second')),
  );

  return zonedAsUtc - date.getTime();
}

function zonedDateTimeToUtc(dateValue, clockValue, timezone) {
  const [year, month, day] = dateValue.split('-').map(Number);
  const [hour, minute] = clockValue.split(':').map(Number);
  const guess = new Date(Date.UTC(year, month - 1, day, hour, minute, 0));
  const offset = zonedOffsetMs(guess, timezone);

  return new Date(guess.getTime() - offset);
}

function sessionFlatBeforeCloseTime(sessionId, session) {
  const end = zonedDateTimeToUtc(sessionId, session.end, session.timezone);
  const flatBeforeCloseMinutes = session.flatBeforeCloseMinutes
    ?? ES_RTH_BASE_DEFAULTS.flatBeforeCloseMinutes;

  return new Date(end.getTime() - (flatBeforeCloseMinutes * 60 * 1000)).toISOString();
}

function deriveRthSessions(bars, session) {
  const bySession = new Map();

  bars.forEach((bar) => {
    const sessionId = zonedDate(bar.time, session.timezone);
    if (!bySession.has(sessionId)) bySession.set(sessionId, []);
    bySession.get(sessionId).push(bar);
  });

  return Array.from(bySession.entries()).map(([id, sessionBars]) => ({
    id,
    firstBarTime: sessionBars[0].time,
    lastBarTime: sessionBars[sessionBars.length - 1].time,
    flatBeforeCloseTime: sessionFlatBeforeCloseTime(id, session),
    barCount: sessionBars.length,
  }));
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

function allowlistById(allowlist) {
  return new Map(allowlist.map((indicator) => [indicator.id, indicator]));
}

function normalizeStudyId(study) {
  return study.indicatorId
    || study.id
    || study.instance?.id
    || study.instance?.pineId
    || study.instance?.name;
}

function nextBarTimeAfter(bars, eventTime) {
  const eventMs = Date.parse(eventTime);
  const next = bars.find((bar) => Date.parse(bar.time) > eventMs);
  return next?.time || eventTime;
}

function latestGraphicTime(graphic, fields, bars) {
  const times = fields
    .map((field) => toIsoFeatureTimestamp(graphic[field], bars))
    .filter(Boolean)
    .sort((left, right) => Date.parse(left) - Date.parse(right));

  return times[times.length - 1] || null;
}

function firstGraphicTime(graphic, fields, bars) {
  const times = fields
    .map((field) => toIsoFeatureTimestamp(graphic[field], bars))
    .filter(Boolean)
    .sort((left, right) => Date.parse(left) - Date.parse(right));

  return times[0] || null;
}

function featureAvailabilityTime({
  repaintingRisk,
  eventTime,
  explicitAvailabilityTime,
  structuralEndTime,
  bars,
}) {
  const explicit = toIsoFeatureTimestamp(explicitAvailabilityTime, bars);
  if (explicit) return explicit;

  if (repaintingRisk === 'repainting-risk') {
    if (structuralEndTime && Date.parse(structuralEndTime) >= Date.parse(eventTime)) {
      return structuralEndTime;
    }
    return nextBarTimeAfter(bars, eventTime);
  }

  return eventTime;
}

function featureId({ indicatorId, type, name, eventTime, availabilityTime, index }) {
  const stableTime = availabilityTime.replace(/[:.]/g, '-');
  return `${indicatorId}:${type}:${name}:${eventTime.replace(/[:.]/g, '-')}:${stableTime}:${index}`;
}

function normalizePlotFeatures({ study, indicator, bars, startIndex }) {
  let index = startIndex;
  const features = [];

  (study.periods || []).forEach((period) => {
    const eventTime = toIsoFeatureTimestamp(period.$time ?? period.time);
    if (!eventTime) return;

    Object.keys(period)
      .filter((key) => !key.startsWith('$') && key !== 'time')
      .sort()
      .forEach((name) => {
        const value = period[name];
        if (value === undefined || value === null) return;

        const availabilityTime = featureAvailabilityTime({
          repaintingRisk: indicator.repaintingRisk,
          eventTime,
          explicitAvailabilityTime: period.$availabilityTime,
          bars,
        });

        features.push({
          id: featureId({
            indicatorId: indicator.id,
            type: 'plot',
            name,
            eventTime,
            availabilityTime,
            index,
          }),
          source: 'tradingview',
          indicatorId: indicator.id,
          type: 'plot',
          name,
          eventTime,
          availabilityTime,
          repaintingRisk: indicator.repaintingRisk,
          value,
        });
        index += 1;
      });
  });

  return { features, nextIndex: index };
}

function normalizeGraphicFeature({
  indicator,
  type,
  name,
  eventTime,
  availabilityTime,
  value,
  index,
  graphic,
}) {
  return {
    id: featureId({
      indicatorId: indicator.id,
      type,
      name,
      eventTime,
      availabilityTime,
      index,
    }),
    source: 'tradingview',
    indicatorId: indicator.id,
    type,
    name,
    eventTime,
    availabilityTime,
    repaintingRisk: indicator.repaintingRisk,
    value: {
      ...value,
      graphicKind: graphic?.kind,
      graphicId: graphic?.id,
      sourceFields: graphic?.sourceFields,
    },
  };
}

function isLuxAlgoStructureEventName(name) {
  return ['BOS', 'CHoCH', 'MSS'].includes(name);
}

function deriveDirectionEvidenceFromLabelStyle(style) {
  if (style === 'label_up') {
    return {
      direction: 'bullish',
      evidenceType: 'label_style',
      evidenceValue: style,
    };
  }

  if (style === 'label_down') {
    return {
      direction: 'bearish',
      evidenceType: 'label_style',
      evidenceValue: style,
    };
  }

  return null;
}

function signalNameForStructureEvent(structureEventName, direction) {
  return `${direction}_${structureEventName.toLowerCase()}`;
}

function signalNameForLiquidityZoneEntry(confirmationMode) {
  return `bullish_liquidity_zone_${confirmationMode}_entry`;
}

function emptyDerivationRuleDiagnostics({ rule, version }) {
  return {
    rule,
    version,
    counts: {},
    warnings: [],
    examples: [],
  };
}

function incrementDiagnosticCount(diagnostics, countName) {
  diagnostics.counts[countName] = (diagnostics.counts[countName] || 0) + 1;
}

function addDiagnosticExample(diagnostics, example) {
  if (diagnostics.examples.length >= 5) return;
  diagnostics.examples.push(example);
}

function warnIfCount(diagnostics, countName, message) {
  if ((diagnostics.counts[countName] || 0) > 0) {
    diagnostics.warnings.push(message(diagnostics.counts[countName]));
  }
}

function compactRuleDiagnostics(diagnostics) {
  return {
    ...diagnostics,
    counts: Object.fromEntries(
      Object.entries(diagnostics.counts).filter(([, value]) => value > 0),
    ),
  };
}

function createDerivationDiagnostics(rules) {
  const includedRules = rules.filter((diagnostics) => (
    Object.values(diagnostics.counts).some((value) => value > 0)
    || diagnostics.warnings.length > 0
    || diagnostics.examples.length > 0
  ));

  if (includedRules.length === 0) return undefined;

  return {
    schemaVersion: 1,
    rules: includedRules.map(compactRuleDiagnostics),
  };
}

function normalizeDerivedSignalFeature({
  indicator,
  eventTime,
  availabilityTime,
  signalName,
  index,
  sourceFeatureId,
  directionEvidence,
}) {
  return {
    id: featureId({
      indicatorId: indicator.id,
      type: 'signal',
      name: signalName,
      eventTime,
      availabilityTime,
      index,
    }),
    source: 'tradingview',
    indicatorId: indicator.id,
    type: 'signal',
    name: signalName,
    eventTime,
    availabilityTime,
    repaintingRisk: indicator.repaintingRisk,
    value: true,
    metadata: {
      provenance: {
        sourceFeatureIds: [sourceFeatureId],
        derivation: {
          rule: LUXALGO_STRUCTURE_SIGNAL_DERIVATION_RULE,
          version: LUXALGO_STRUCTURE_SIGNAL_DERIVATION_VERSION,
        },
        directionEvidence,
      },
    },
  };
}

function deriveLuxAlgoStructureSignals({ indicator, features, startIndex }) {
  const diagnostics = emptyDerivationRuleDiagnostics({
    rule: LUXALGO_STRUCTURE_SIGNAL_DERIVATION_RULE,
    version: LUXALGO_STRUCTURE_SIGNAL_DERIVATION_VERSION,
  });

  if (indicator.id !== LUXALGO_ICT_SMC_OPT_IN_ALLOWLIST[0].id) {
    return { features: [], nextIndex: startIndex, diagnostics };
  }

  let index = startIndex;
  const derivedFeatures = [];

  features.forEach((feature) => {
    if (feature.type !== 'label' || !isLuxAlgoStructureEventName(feature.name)) return;
    incrementDiagnosticCount(diagnostics, 'sourceFeatures');

    const directionEvidence = deriveDirectionEvidenceFromLabelStyle(feature.value?.style);
    if (!directionEvidence) {
      incrementDiagnosticCount(diagnostics, 'unresolvedDirection');
      addDiagnosticExample(diagnostics, {
        kind: 'unresolved_direction',
        indicatorId: feature.indicatorId,
        sourceFeatureId: feature.id,
        featureType: feature.type,
        featureName: feature.name,
        eventTime: feature.eventTime,
        reason: 'unsupported label style',
        evidence: {
          text: feature.value?.text,
          style: feature.value?.style,
          graphicId: feature.value?.graphicId,
        },
      });
      return;
    }

    derivedFeatures.push(normalizeDerivedSignalFeature({
      indicator,
      eventTime: feature.eventTime,
      availabilityTime: feature.availabilityTime,
      signalName: signalNameForStructureEvent(feature.name, directionEvidence.direction),
      index,
      sourceFeatureId: feature.id,
      directionEvidence,
    }));
    incrementDiagnosticCount(diagnostics, 'derivedFeatures');
    index += 1;
  });

  warnIfCount(
    diagnostics,
    'unresolvedDirection',
    (count) => `${count} structure event label could not be converted to a directional signal.`,
  );

  return {
    features: derivedFeatures,
    nextIndex: index,
    diagnostics,
  };
}

function barIndexByTime(bars) {
  return new Map(bars.map((bar, index) => [bar.time, index]));
}

function luxAlgoLiquidityZoneEntryOptions(options) {
  const source = options?.luxAlgoLiquidityZoneEntries;
  if (!source) return null;

  return {
    confirmationMode: source.confirmationMode || 'touch',
    zonePreference: source.zonePreference || 'nearest-any',
    maxBarsAfterStructureEvent: source.maxBarsAfterStructureEvent ?? 12,
  };
}

function liquidityZoneKind(feature) {
  const name = String(feature.name || '').toLowerCase();
  const text = String(feature.value?.text || '').toLowerCase();
  const combined = `${name} ${text}`;

  if (combined.includes('fvg') || combined.includes('fair_value_gap') || combined.includes('fair value gap')) {
    return 'fair_value_gap';
  }

  if (combined.includes('ob') || combined.includes('order_block') || combined.includes('order block')) {
    return 'order_block';
  }

  return null;
}

function bullishLiquidityZone(feature) {
  if (feature.type !== 'box') return null;

  const kind = liquidityZoneKind(feature);
  if (!kind) return null;

  const name = String(feature.name || '').toLowerCase();
  const text = String(feature.value?.text || '').toLowerCase();
  const combined = `${name} ${text}`;
  if (!combined.includes('bull')) return null;

  const top = feature.value?.top;
  const bottom = feature.value?.bottom;
  if (!Number.isFinite(top) || !Number.isFinite(bottom)) return null;

  return {
    feature,
    kind,
    direction: 'bullish',
    top: Math.max(top, bottom),
    bottom: Math.min(top, bottom),
  };
}

function diagnoseBullishLiquidityZone(feature) {
  if (feature.type !== 'box') return null;

  const kind = liquidityZoneKind(feature);
  if (!kind) return null;

  const name = String(feature.name || '').toLowerCase();
  const text = String(feature.value?.text || '').toLowerCase();
  const combined = `${name} ${text}`;
  if (!combined.includes('bull')) return null;

  const top = feature.value?.top;
  const bottom = feature.value?.bottom;
  if (Number.isFinite(top) && Number.isFinite(bottom)) return null;

  return {
    kind: 'invalid_zone_geometry',
    indicatorId: feature.indicatorId,
    sourceFeatureId: feature.id,
    featureType: feature.type,
    featureName: feature.name,
    eventTime: feature.eventTime,
    reason: 'missing finite top or bottom',
    evidence: {
      top: feature.value?.top,
      bottom: feature.value?.bottom,
      graphicId: feature.value?.graphicId,
    },
  };
}

function bullishStructureEvent(feature) {
  if (feature.type !== 'label' || !isLuxAlgoStructureEventName(feature.name)) return null;

  const directionEvidence = deriveDirectionEvidenceFromLabelStyle(feature.value?.style);
  if (directionEvidence?.direction !== 'bullish') return null;

  return {
    feature,
    directionEvidence,
  };
}

function isZoneInvalidatedByBar(zone, bar) {
  return bar.close < zone.bottom;
}

function isZoneTouchedByBar(zone, bar) {
  return bar.low <= zone.top && bar.high >= zone.bottom;
}

function isZoneReclaimedByBar(zone, bar) {
  return isZoneTouchedByBar(zone, bar) && bar.close > zone.top;
}

function zoneConfirms({ zone, bar, confirmationMode }) {
  if (confirmationMode === 'reclaim') return isZoneReclaimedByBar(zone, bar);
  return isZoneTouchedByBar(zone, bar);
}

function selectLiquidityZone({ zones, structureFeature, confirmationBar, zonePreference }) {
  const candidates = zones.filter((zone) => (
    Date.parse(zone.feature.availabilityTime) <= Date.parse(confirmationBar.time)
    && Date.parse(zone.feature.eventTime) <= Date.parse(confirmationBar.time)
  ));

  const preferred = candidates.filter((zone) => {
    if (zonePreference === 'prefer-OB') return zone.kind === 'order_block';
    if (zonePreference === 'prefer-FVG') return zone.kind === 'fair_value_gap';
    return true;
  });
  const selectionPool = preferred.length > 0 ? preferred : candidates;

  return selectionPool
    .slice()
    .sort((left, right) => (
      Math.abs(confirmationBar.close - left.top) - Math.abs(confirmationBar.close - right.top)
      || Date.parse(left.feature.availabilityTime) - Date.parse(right.feature.availabilityTime)
      || left.feature.id.localeCompare(right.feature.id)
    ))[0] || null;
}

function normalizeLiquidityZoneEntrySignalFeature({
  indicator,
  structureEvent,
  zone,
  confirmationBar,
  confirmationMode,
  index,
}) {
  return {
    id: featureId({
      indicatorId: indicator.id,
      type: 'signal',
      name: signalNameForLiquidityZoneEntry(confirmationMode),
      eventTime: confirmationBar.time,
      availabilityTime: confirmationBar.time,
      index,
    }),
    source: 'tradingview',
    indicatorId: indicator.id,
    type: 'signal',
    name: signalNameForLiquidityZoneEntry(confirmationMode),
    eventTime: confirmationBar.time,
    availabilityTime: confirmationBar.time,
    repaintingRisk: indicator.repaintingRisk,
    value: true,
    metadata: {
      provenance: {
        sourceFeatureIds: [structureEvent.feature.id, zone.feature.id],
        derivation: {
          rule: LUXALGO_LIQUIDITY_ZONE_ENTRY_DERIVATION_RULE,
          version: LUXALGO_LIQUIDITY_ZONE_ENTRY_DERIVATION_VERSION,
        },
        structureEvent: {
          featureId: structureEvent.feature.id,
          name: structureEvent.feature.name,
          direction: 'bullish',
          eventTime: structureEvent.feature.eventTime,
          availabilityTime: structureEvent.feature.availabilityTime,
          directionEvidence: structureEvent.directionEvidence,
        },
        selectedZone: {
          featureId: zone.feature.id,
          kind: zone.kind,
          direction: zone.direction,
          eventTime: zone.feature.eventTime,
          availabilityTime: zone.feature.availabilityTime,
          top: zone.top,
          bottom: zone.bottom,
        },
        confirmation: {
          mode: confirmationMode,
          barTime: confirmationBar.time,
          bar: {
            open: confirmationBar.open,
            high: confirmationBar.high,
            low: confirmationBar.low,
            close: confirmationBar.close,
          },
        },
      },
    },
  };
}

function deriveLuxAlgoLiquidityZoneEntrySignals({
  indicator,
  features,
  bars,
  options,
  startIndex,
}) {
  const entryOptions = luxAlgoLiquidityZoneEntryOptions(options);
  const diagnostics = emptyDerivationRuleDiagnostics({
    rule: LUXALGO_LIQUIDITY_ZONE_ENTRY_DERIVATION_RULE,
    version: LUXALGO_LIQUIDITY_ZONE_ENTRY_DERIVATION_VERSION,
  });

  if (!entryOptions || indicator.id !== LUXALGO_ICT_SMC_OPT_IN_ALLOWLIST[0].id) {
    return { features: [], nextIndex: startIndex, diagnostics };
  }

  let index = startIndex;
  const usedZoneFeatureIds = new Set();
  const derivedFeatures = [];
  const indexes = barIndexByTime(bars);
  const structures = features
    .map(bullishStructureEvent)
    .filter(Boolean)
    .sort((left, right) => Date.parse(left.feature.availabilityTime) - Date.parse(right.feature.availabilityTime));
  diagnostics.counts.sourceStructureEvents = structures.length;

  features
    .map(diagnoseBullishLiquidityZone)
    .filter(Boolean)
    .forEach((example) => {
      incrementDiagnosticCount(diagnostics, 'invalidZoneGeometry');
      addDiagnosticExample(diagnostics, example);
    });

  const zones = features
    .map(bullishLiquidityZone)
    .filter(Boolean);
  diagnostics.counts.sourceZones = zones.length;

  structures.forEach((structureEvent) => {
    const startBarIndex = indexes.get(structureEvent.feature.availabilityTime);
    if (startBarIndex === undefined) {
      incrementDiagnosticCount(diagnostics, 'missingProvenance');
      addDiagnosticExample(diagnostics, {
        kind: 'missing_provenance',
        indicatorId: structureEvent.feature.indicatorId,
        sourceFeatureId: structureEvent.feature.id,
        featureType: structureEvent.feature.type,
        featureName: structureEvent.feature.name,
        eventTime: structureEvent.feature.eventTime,
        reason: 'structure event availability time does not match a collected bar',
        evidence: {
          availabilityTime: structureEvent.feature.availabilityTime,
        },
      });
      return;
    }

    const lastBarIndex = Math.min(
      bars.length - 1,
      startBarIndex + entryOptions.maxBarsAfterStructureEvent,
    );

    for (let barIndex = startBarIndex; barIndex <= lastBarIndex; barIndex += 1) {
      const bar = bars[barIndex];
      const activeZones = zones.filter((zone) => !usedZoneFeatureIds.has(zone.feature.id));
      activeZones
        .filter((zone) => isZoneInvalidatedByBar(zone, bar))
        .forEach((zone) => usedZoneFeatureIds.add(zone.feature.id));

      const selectableZone = selectLiquidityZone({
        zones: activeZones.filter((zone) => (
          !usedZoneFeatureIds.has(zone.feature.id)
          && zoneConfirms({
            zone,
            bar,
            confirmationMode: entryOptions.confirmationMode,
          })
        )),
        structureFeature: structureEvent.feature,
        confirmationBar: bar,
        zonePreference: entryOptions.zonePreference,
      });

      if (!selectableZone) continue;

      derivedFeatures.push(normalizeLiquidityZoneEntrySignalFeature({
        indicator,
        structureEvent,
        zone: selectableZone,
        confirmationBar: bar,
        confirmationMode: entryOptions.confirmationMode,
        index,
      }));
      incrementDiagnosticCount(diagnostics, 'derivedFeatures');
      index += 1;
      usedZoneFeatureIds.add(selectableZone.feature.id);
      break;
    }
  });

  warnIfCount(
    diagnostics,
    'invalidZoneGeometry',
    (count) => `${count} liquidity zone could not be used because its geometry was invalid.`,
  );
  warnIfCount(
    diagnostics,
    'missingProvenance',
    (count) => `${count} structure event could not be evaluated because required provenance was missing.`,
  );

  return {
    features: derivedFeatures,
    nextIndex: index,
    diagnostics,
  };
}

function normalizeGraphicFeatures({
  study,
  indicator,
  bars,
  startIndex,
  candidateSignalDerivation,
}) {
  let index = startIndex;
  const features = [];
  const graphic = study.graphic || {};

  (graphic.labels || []).forEach((label) => {
    const eventTime = firstGraphicTime(label, ['x', 'time'], bars);
    if (!eventTime) return;
    const availabilityTime = featureAvailabilityTime({
      repaintingRisk: indicator.repaintingRisk,
      eventTime,
      explicitAvailabilityTime: label.availabilityTime ?? label.$availabilityTime,
      bars,
    });
    features.push(normalizeGraphicFeature({
      indicator,
      type: 'label',
      name: label.name || label.text || `label_${label.id}`,
      eventTime,
      availabilityTime,
      value: {
        price: label.y,
        yLoc: label.yLoc,
        text: label.text,
        style: label.style,
        color: label.color,
        textColor: label.textColor,
      },
      index,
      graphic: {
        kind: 'label',
        id: label.id,
        sourceFields: {
          ...label,
        },
      },
    }));
    index += 1;
  });

  (graphic.lines || []).forEach((line) => {
    const eventTime = firstGraphicTime(line, ['x1', 'x2'], bars);
    const endTime = latestGraphicTime(line, ['x1', 'x2'], bars);
    if (!eventTime) return;
    const availabilityTime = featureAvailabilityTime({
      repaintingRisk: indicator.repaintingRisk,
      eventTime,
      explicitAvailabilityTime: line.availabilityTime ?? line.$availabilityTime,
      structuralEndTime: endTime,
      bars,
    });
    features.push(normalizeGraphicFeature({
      indicator,
      type: 'line',
      name: line.name || `line_${line.id}`,
      eventTime,
      availabilityTime,
      value: {
        startTime: toIsoFeatureTimestamp(line.x1, bars),
        startPrice: line.y1,
        endTime: toIsoFeatureTimestamp(line.x2, bars),
        endPrice: line.y2,
        extend: line.extend,
        style: line.style,
        color: line.color,
        width: line.width,
      },
      index,
      graphic: {
        kind: 'line',
        id: line.id,
        sourceFields: {
          ...line,
        },
      },
    }));
    index += 1;
  });

  (graphic.boxes || []).forEach((box) => {
    const eventTime = firstGraphicTime(box, ['x1', 'x2'], bars);
    const endTime = latestGraphicTime(box, ['x1', 'x2'], bars);
    if (!eventTime) return;
    const availabilityTime = featureAvailabilityTime({
      repaintingRisk: indicator.repaintingRisk,
      eventTime,
      explicitAvailabilityTime: box.availabilityTime ?? box.$availabilityTime,
      structuralEndTime: endTime,
      bars,
    });
    features.push(normalizeGraphicFeature({
      indicator,
      type: 'box',
      name: box.name || box.text || `box_${box.id}`,
      eventTime,
      availabilityTime,
      value: {
        startTime: toIsoFeatureTimestamp(box.x1, bars),
        endTime: toIsoFeatureTimestamp(box.x2, bars),
        top: box.y1,
        bottom: box.y2,
        color: box.color,
        bgColor: box.bgColor,
        extend: box.extend,
        style: box.style,
        width: box.width,
        text: box.text,
      },
      index,
      graphic: {
        kind: 'box',
        id: box.id,
        sourceFields: {
          ...box,
        },
      },
    }));
    index += 1;
  });

  (graphic.horizHists || []).forEach((hist) => {
    const eventTime = firstGraphicTime(hist, ['firstBarTime', 'lastBarTime'], bars);
    const endTime = latestGraphicTime(hist, ['firstBarTime', 'lastBarTime'], bars);
    if (!eventTime) return;
    const availabilityTime = featureAvailabilityTime({
      repaintingRisk: indicator.repaintingRisk,
      eventTime,
      explicitAvailabilityTime: hist.availabilityTime ?? hist.$availabilityTime,
      structuralEndTime: endTime,
      bars,
    });
    features.push(normalizeGraphicFeature({
      indicator,
      type: 'profile',
      name: hist.name || `profile_${hist.id}`,
      eventTime,
      availabilityTime,
      value: {
        firstBarTime: toIsoFeatureTimestamp(hist.firstBarTime, bars),
        lastBarTime: toIsoFeatureTimestamp(hist.lastBarTime, bars),
        priceLow: hist.priceLow,
        priceHigh: hist.priceHigh,
        rate: hist.rate,
      },
      index,
      graphic: {
        kind: 'profile',
        id: hist.id,
        sourceFields: {
          ...hist,
        },
      },
    }));
    index += 1;
  });

  const derivedSignalResult = deriveLuxAlgoStructureSignals({
    indicator,
    features,
    startIndex: index,
  });
  features.push(...derivedSignalResult.features);
  index = derivedSignalResult.nextIndex;
  const diagnostics = [derivedSignalResult.diagnostics];

  const liquidityZoneEntryResult = deriveLuxAlgoLiquidityZoneEntrySignals({
    indicator,
    features,
    bars,
    options: candidateSignalDerivation,
    startIndex: index,
  });
  features.push(...liquidityZoneEntryResult.features);
  index = liquidityZoneEntryResult.nextIndex;
  diagnostics.push(liquidityZoneEntryResult.diagnostics);

  return { features, nextIndex: index, diagnostics };
}

function indicatorStudiesToFeatures({
  studies = [],
  bars = [],
  allowlist = CURATED_INDICATOR_ALLOWLIST,
  candidateSignalDerivation,
} = {}) {
  const byId = allowlistById(allowlist);
  let index = 0;
  const features = [];
  const diagnostics = [];

  studies.forEach((study) => {
    const indicator = byId.get(normalizeStudyId(study));
    if (!indicator) return;

    const plotResult = normalizePlotFeatures({
      study,
      indicator,
      bars,
      startIndex: index,
    });
    features.push(...plotResult.features);
    index = plotResult.nextIndex;

    const graphicResult = normalizeGraphicFeatures({
      study,
      indicator,
      bars,
      startIndex: index,
      candidateSignalDerivation,
    });
    features.push(...graphicResult.features);
    index = graphicResult.nextIndex;
    diagnostics.push(...graphicResult.diagnostics);
  });

  const sortedFeatures = features.sort((left, right) => (
    Date.parse(left.availabilityTime) - Date.parse(right.availabilityTime)
    || Date.parse(left.eventTime) - Date.parse(right.eventTime)
    || left.id.localeCompare(right.id)
  ));

  Object.defineProperty(sortedFeatures, 'derivationDiagnostics', {
    value: createDerivationDiagnostics(diagnostics),
    enumerable: false,
  });
  return sortedFeatures;
}

function defaultResolveIndicator(indicator) {
  return miscRequests.getIndicator(
    indicator.id,
    indicator.version === 'tradingview' ? 'last' : indicator.version,
    process.env.SESSION,
    process.env.SIGNATURE,
  );
}

function waitForStudyReady(study, { timeoutMs, indicatorId }) {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      reject(new Error(`Timed out waiting for TradingView indicator study ${indicatorId}`));
    }, timeoutMs);

    const complete = () => {
      clearTimeout(timeout);
      resolve(study);
    };

    if (typeof study.onReady === 'function') {
      study.onReady(complete);
    } else {
      complete();
    }

    if (typeof study.onError === 'function') {
      study.onError((...error) => {
        clearTimeout(timeout);
        reject(new Error(`TradingView indicator study ${indicatorId} error: ${error.join(' ')}`));
      });
    }
  });
}

async function collectIndicatorStudies({
  chart,
  allowlist = CURATED_INDICATOR_ALLOWLIST,
  resolveIndicator = defaultResolveIndicator,
  timeoutMs,
} = {}) {
  if (!chart || typeof chart.Study !== 'function') return [];

  return Promise.all(allowlist.map(async (allowlistedIndicator) => {
    const indicator = await resolveIndicator(allowlistedIndicator);
    const study = new chart.Study(indicator);
    study.indicatorId = allowlistedIndicator.id;
    await waitForStudyReady(study, {
      timeoutMs,
      indicatorId: allowlistedIndicator.id,
    });
    return study;
  }));
}

function periodsToRthBars(periods, options = {}) {
  const session = {
    timezone: options.timezone || ES_RTH_BASE_DEFAULTS.timezone,
    start: options.sessionStart || ES_RTH_BASE_DEFAULTS.sessionStart,
    end: options.sessionEnd || ES_RTH_BASE_DEFAULTS.sessionEnd,
  };

  const bars = periods
    .map(toContractBar)
    .filter((bar) => isInsideSession(bar.time, session))
    .sort((left, right) => Date.parse(left.time) - Date.parse(right.time));

  if (bars.length < (options.minBars || ES_RTH_BASE_DEFAULTS.minBars)) return [];
  return bars;
}

function buildEsRthDataset({
  defaults,
  bars,
  infos = {},
  now = new Date(),
  datasetId,
  indicatorAllowlist = CURATED_INDICATOR_ALLOWLIST,
  indicatorStudies = [],
  candidateSignalDerivation,
  collection,
} = {}) {
  const collectedAt = now instanceof Date ? now : new Date(now);
  const session = {
    name: defaults.sessionName,
    timezone: defaults.timezone,
    start: defaults.sessionStart,
    end: defaults.sessionEnd,
    flatBeforeCloseMinutes: defaults.flatBeforeCloseMinutes,
  };
  const features = indicatorStudiesToFeatures({
    studies: indicatorStudies,
    bars,
    allowlist: indicatorAllowlist,
    candidateSignalDerivation,
  });

  return {
    manifest: {
      schemaVersion: 1,
      datasetId: datasetId || stableDatasetId(defaults.datasetPrefix, collectedAt),
      collectedAt: collectedAt.toISOString(),
      source: 'tradingview',
      symbol: {
        ticker: defaults.symbol,
        root: defaults.root,
        assetClass: defaults.assetClass,
      },
      session: {
        ...session,
        sessions: deriveRthSessions(bars, session),
      },
      bar: {
        interval: defaults.interval,
        priceScale: infos.pricescale || 100,
        volumeUnit: defaults.volumeUnit,
      },
      contract: {
        type: 'continuous_futures',
        continuous: true,
        rollPolicy: {
          source: 'tradingview',
          description: 'TradingView continuous futures contract ES1! with provider-managed roll construction.',
        },
      },
      indicators: indicatorAllowlist.map((indicator) => ({ ...indicator })),
      ...(collection ? { collection: { ...collection } } : {}),
    },
    bars,
    features,
    derivationDiagnostics: features.derivationDiagnostics,
  };
}

function buildEsRth5mDataset(options = {}) {
  return buildEsRthDataset({
    ...options,
    defaults: ES_RTH_5M_DEFAULTS,
  });
}

function buildEsRth15mDataset(options = {}) {
  return buildEsRthDataset({
    ...options,
    defaults: ES_RTH_15M_DEFAULTS,
  });
}

function writeJsonSync(filePath, value) {
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`);
}

function writeVersionedDatasetSync(outputPath, dataset) {
  fs.mkdirSync(outputPath, { recursive: true });
  writeJsonSync(path.join(outputPath, 'manifest.json'), dataset.manifest);
  writeJsonSync(path.join(outputPath, 'bars.json'), dataset.bars);
  writeJsonSync(path.join(outputPath, 'features.json'), dataset.features);
  const derivationDiagnosticsPath = path.join(outputPath, DERIVATION_DIAGNOSTICS_FILE);
  if (dataset.derivationDiagnostics) {
    writeJsonSync(derivationDiagnosticsPath, dataset.derivationDiagnostics);
  } else if (fs.existsSync(derivationDiagnosticsPath)) {
    fs.unlinkSync(derivationDiagnosticsPath);
  }
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

async function collectEsRthDataset({
  defaults,
  buildDataset,
  createClient,
  outputPath,
  now = new Date(),
  range = defaults.range,
  minBars = defaults.minBars,
  timeoutMs = 30000,
  to,
  indicatorAllowlist = CURATED_INDICATOR_ALLOWLIST,
  indicatorStudies = [],
  includeIndicatorFeatures = true,
  candidateSignalDerivation,
  resolveIndicator = defaultResolveIndicator,
  tradingViewBackend = 'data',
  collection,
} = {}) {
  if (!outputPath) throw new Error('outputPath is required');

  const clientOptions = {
    token: process.env.SESSION,
    signature: process.env.SIGNATURE,
    server: tradingViewBackend,
  };
  const client = createClient ? createClient(clientOptions) : new Client(clientOptions);
  const chart = new client.Session.Chart();

  try {
    const barsPromise = waitForChartBars(chart, { minBars, timeoutMs });
    chart.setMarket(defaults.symbol, {
      timeframe: defaults.tradingViewTimeframe,
      range,
      to,
      session: 'regular',
      backadjustment: true,
    });

    const bars = await barsPromise;
    const collectedIndicatorStudies = indicatorStudies.length > 0 || !includeIndicatorFeatures
      ? indicatorStudies
      : await collectIndicatorStudies({
        chart,
        allowlist: indicatorAllowlist,
        resolveIndicator,
        timeoutMs,
      });
    const dataset = buildDataset({
      bars,
      infos: chart.infos,
      now,
      indicatorAllowlist,
      indicatorStudies: collectedIndicatorStudies,
      candidateSignalDerivation,
      collection,
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

async function collectEsRth5mDataset(options = {}) {
  return collectEsRthDataset({
    ...options,
    defaults: ES_RTH_5M_DEFAULTS,
    buildDataset: buildEsRth5mDataset,
  });
}

async function collectEsRth5mLuxAlgoIctSmcDataset(options = {}) {
  return collectEsRthDataset({
    ...options,
    defaults: ES_RTH_5M_DEFAULTS,
    buildDataset: buildEsRth5mDataset,
    indicatorAllowlist: LUXALGO_ICT_SMC_OPT_IN_ALLOWLIST,
    tradingViewBackend: LUXALGO_ICT_SMC_TRADINGVIEW_BACKEND,
    collection: {
      kind: LUXALGO_ICT_SMC_COLLECTION_KIND,
      tradingViewBackend: LUXALGO_ICT_SMC_TRADINGVIEW_BACKEND,
      optIn: true,
    },
  });
}

async function collectEsRth15mDataset(options = {}) {
  return collectEsRthDataset({
    ...options,
    defaults: ES_RTH_15M_DEFAULTS,
    buildDataset: buildEsRth15mDataset,
  });
}

module.exports = {
  collectEsRth5mDataset,
  collectEsRth5mLuxAlgoIctSmcDataset,
  collectEsRth15mDataset,
  buildEsRth5mDataset,
  buildEsRth15mDataset,
  collectIndicatorStudies,
  indicatorStudiesToFeatures,
  periodsToRthBars,
  writeVersionedDatasetSync,
  CURATED_INDICATOR_ALLOWLIST,
  LUXALGO_ICT_SMC_OPT_IN_ALLOWLIST,
};
