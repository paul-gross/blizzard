/**
 * The one instant every fixture dates its records from, and the workshop pins
 * `FLEET_CLOCK` to — a fixed UTC moment, so a relative time reads the same on
 * every visit and a spec's literal never depends on when it ran.
 */
export const FIXTURE_NOW = Date.UTC(2026, 0, 15, 12, 0, 0);
