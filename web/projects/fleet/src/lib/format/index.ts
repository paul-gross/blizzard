/*
 * The loose formatting/display modules grouped into one sub-barrel (issue #82) —
 * `compactRef`, cost/token formatting, board lane/tone folds, and time formatting.
 * The implementation files stay at `lib/` root (no consumer import-path churn); this
 * barrel is purely the public re-export surface, one line per module.
 */

export { compactRef, ENTITY_DISPLAY, type EntityDisplay } from '../compact-ref';
export { formatCost, formatTokens } from '../cost-format';
export { LANES, STATUS_LANE, STATUS_TONE, laneFor, type Lane } from '../chunk-lanes';
export { formatWhen, formatAge, formatHeldFor, ageMs, formatUtcClock, SKEW_TOLERANCE_MS } from '../when';
// formatSeenAgo (runner-view.ts) and formatClockTime (event-log-panel.ts) are
// intentionally not re-exported here — each has exactly one fleet-internal
// caller today, which imports it directly from `../when`; no consumer outside
// this library needs them.
