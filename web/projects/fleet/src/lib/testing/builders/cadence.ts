import type {
  CadenceAttentionView,
  CadenceErrandView,
  CadenceView,
  ChoreView,
  ErrandRunView,
  RunCadenceView,
} from '../../api/hub';
import { fixtureInstant } from './clock';

/** One chore keyed `nightly`, running routine `nightly` over every scope in full, promoting, with no note. */
export function gardenChoreView(overrides: Partial<ChoreView> = {}): ChoreView {
  return {
    chore_id: 'chr_1',
    key: 'nightly',
    routine: 'nightly',
    scopes: 'all',
    mode: 'full',
    promote: true,
    note: null,
    scopes_set_at: fixtureInstant(-7 * 24 * 3600),
    ...overrides,
  };
}

/**
 * An enabled cadence `evening` firing at `0 20 * * *` in America/Chicago, at least ten days apart, keeping no chore and
 * needing no attention — its next five moments a day apart from the fixture clock's next evening.
 */
export function gardenCadenceView(overrides: Pick<CadenceView, 'slug'> & Partial<CadenceView>): CadenceView {
  return {
    cadence_id: `cad_${overrides.slug}`,
    name: overrides.slug,
    project_id: null,
    project_name: null,
    project_slug: null,
    schedule: { cron: '0 20 * * *', zone: 'America/Chicago' },
    spacing_minutes: 10 * 24 * 60,
    anchor: null,
    chores: [],
    attention: [],
    errand_counts: { due: 0, running: 0, waiting: 0, stopped: 0 },
    next_moments: [14, 38, 62, 86, 110].map((hours) => fixtureInstant(hours * 3600)),
    retired: false,
    revision: 1,
    created_at: fixtureInstant(-7 * 24 * 3600),
    ...overrides,
  };
}

/** One thing stopping a cadence: chore `nightly` over a scope the project lacks. */
export function gardenCadenceAttentionView(overrides: Partial<CadenceAttentionView> = {}): CadenceAttentionView {
  return { chore: 'nightly', scope: 'gone', reason: 'scope_unknown', ...overrides };
}

/** An errand's last run: a done chunk scheduled, started, and finished an hour before the fixture clock. */
export function gardenErrandRunView(overrides: Partial<ErrandRunView> = {}): ErrandRunView {
  return {
    chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
    status: 'done',
    scheduled_at: fixtureInstant(-3600),
    started_at: fixtureInstant(-3600),
    finished_at: fixtureInstant(-1800),
    ...overrides,
  };
}

/** A waiting errand: chore `nightly` over scope `blizzard`, never run, due fourteen hours after the fixture clock. */
export function gardenCadenceErrandView(overrides: Partial<CadenceErrandView> = {}): CadenceErrandView {
  return {
    chore: 'nightly',
    scope: 'blizzard',
    state: 'waiting',
    attention: [],
    last_run: null,
    due_at: fixtureInstant(14 * 3600),
    ...overrides,
  };
}

/** The cadence `evening` and its chore `nightly`, as a run it minted names them. */
export function runCadenceView(overrides: Partial<RunCadenceView> = {}): RunCadenceView {
  return {
    cadence_id: 'cad_evening',
    cadence_slug: 'evening',
    cadence_name: 'evening',
    chore_id: 'chr_1',
    chore_key: 'nightly',
    ...overrides,
  };
}
