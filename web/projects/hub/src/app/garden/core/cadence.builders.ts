// The cadence domain builders specs and stories build their inputs from — domain literals only, no wire, no Angular.
// The wire builders (`gardenCadenceView` and its kin in `fleet/testing`) stay for the translator, query, and mutation
// specs and for answering a stubbed transport.
import { fixtureInstant } from 'fleet/testing';

import type { Cadence, CadenceAttention, CadenceErrand, Chore, ErrandRun } from './cadence.domain';

/** One chore keyed `nightly`, running routine `nightly` over every scope in full, promoting, with no note. */
export function gardenChore(overrides: Partial<Chore> = {}): Chore {
  return {
    id: 'chr_1',
    key: 'nightly',
    routine: 'nightly',
    scopes: 'all',
    mode: 'full',
    promote: true,
    note: null,
    urgency: null,
    scopesSetAt: fixtureInstant(-7 * 24 * 3600),
    ...overrides,
  };
}

/**
 * An enabled cadence `evening` firing at `0 20 * * *` in America/Chicago, at least ten days apart, keeping no chore and
 * needing no attention — its next five moments a day apart from the fixture clock's next evening.
 */
export function gardenCadence(overrides: Pick<Cadence, 'slug'> & Partial<Cadence>): Cadence {
  return {
    id: `cad_${overrides.slug}`,
    revision: 1,
    name: overrides.slug,
    projectId: null,
    projectName: null,
    projectSlug: null,
    schedule: { cron: '0 20 * * *', zone: 'America/Chicago' },
    spacingMinutes: 10 * 24 * 60,
    anchor: null,
    chores: [],
    attention: [],
    errandCounts: { due: 0, running: 0, waiting: 0, stopped: 0 },
    nextMoments: [14, 38, 62, 86, 110].map((hours) => fixtureInstant(hours * 3600)),
    retired: false,
    createdAt: fixtureInstant(-7 * 24 * 3600),
    ...overrides,
  };
}

/** One thing stopping a cadence: chore `nightly` over a scope the project lacks. */
export function gardenCadenceAttention(overrides: Partial<CadenceAttention> = {}): CadenceAttention {
  return { chore: 'nightly', scope: 'gone', reason: 'scope_unknown', ...overrides };
}

/** A finished last run: its chunk, scheduled an hour before the fixture clock. */
export function gardenErrandRun(overrides: Partial<ErrandRun> = {}): ErrandRun {
  return { chunkId: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9', scheduledAt: fixtureInstant(-3600), ...overrides };
}

/** A waiting errand: chore `nightly` over scope `blizzard`, never run, due fourteen hours after the fixture clock. */
export function gardenCadenceErrand(overrides: Partial<CadenceErrand> = {}): CadenceErrand {
  return {
    chore: 'nightly',
    scope: 'blizzard',
    state: 'waiting',
    attention: [],
    lastRun: null,
    dueAt: fixtureInstant(14 * 3600),
    ...overrides,
  };
}
