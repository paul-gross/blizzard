/**
 * The one instant every fixture dates its records from, and the workshop pins
 * `FLEET_CLOCK` to — a fixed UTC moment, so a relative time reads the same on
 * every visit and a spec's literal never depends on when it ran.
 */
export const FIXTURE_NOW = Date.UTC(2026, 0, 15, 12, 0, 0);

/** The instant `seconds` after {@link FIXTURE_NOW} (before it, when negative), as the
 * ISO string the wire carries — how a builder dates a record without reading a clock. */
export function fixtureInstant(seconds: number): string {
  return new Date(FIXTURE_NOW + seconds * 1000).toISOString();
}
