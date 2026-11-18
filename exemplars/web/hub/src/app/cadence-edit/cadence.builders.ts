// exemplar: bzh:frontend-domain-boundary — the domain builder specs build their inputs from; no wire, no Angular.

import type { Cadence } from './cadence.domain';

/** A cadence `evening` at revision 1, firing at `0 20 * * *` in America/Chicago, at least ten days apart. */
export function editCadence(overrides: Partial<Cadence> = {}): Cadence {
  return {
    id: 'cad_evening',
    revision: 1,
    slug: 'evening',
    name: 'evening',
    schedule: { cron: '0 20 * * *', zone: 'America/Chicago' },
    spacingMinutes: 10 * 24 * 60,
    anchor: null,
    ...overrides,
  };
}
