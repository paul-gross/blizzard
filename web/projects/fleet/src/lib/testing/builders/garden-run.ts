import type {
  DeliveredSetDeltaView,
  DeliveredSetView,
  ProjectView,
  RoutineBaselineView,
  RoutineRunResponse,
  RoutineView,
  RunDeltaView,
  RunEscalationView,
  RunRowView,
  ScopeView,
} from '../../api/hub';
import { fixtureInstant } from './clock';

/** One finding set a run delivered, at the given counts — none by default. */
export function deliveredSetView(
  overrides: Pick<DeliveredSetView, 'finding_set_id'> & Partial<DeliveredSetView>,
): DeliveredSetView {
  return {
    added_count: 0,
    gone_count: 0,
    measurement: null,
    observed_count: 0,
    revisions: {},
    ...overrides,
  };
}

/** One finding set's delta, empty in every group by default. */
export function deliveredSetDeltaView(
  overrides: Pick<DeliveredSetDeltaView, 'finding_set_id'> & Partial<DeliveredSetDeltaView>,
): DeliveredSetDeltaView {
  return {
    added: [],
    gone: [],
    measurement: null,
    observed: [],
    revisions: {},
    ...overrides,
  };
}

/** A run's escalation, at the node that raised it. */
export function runEscalationView(overrides: Partial<RunEscalationView> = {}): RunEscalationView {
  return {
    node_name: 'survey',
    takeover_command: 'blizzard hub chunk takeover ch_1',
    wrapped_takeover_command: '',
    ...overrides,
  };
}

/** One run-list row: a full `nightly` sweep of `blizzard` that finished, delivering nothing, under no project. */
export function runRowView(overrides: Pick<RunRowView, 'chunk_id'> & Partial<RunRowView>): RunRowView {
  return {
    delivered: [],
    escalation: null,
    minted_at: fixtureInstant(-24 * 3600),
    mode: 'full',
    outcome: 'done',
    project_id: null,
    project_name: null,
    project_slug: null,
    routine_name: 'nightly',
    scope_slug: 'blizzard',
    ...overrides,
  };
}

/** One run's delta: a full `nightly` sweep of `blizzard` that finished, delivering no sets. */
export function runDeltaView(overrides: Pick<RunDeltaView, 'chunk_id'> & Partial<RunDeltaView>): RunDeltaView {
  return {
    escalation: null,
    mode: 'full',
    outcome: 'done',
    project_id: null,
    project_name: null,
    project_slug: null,
    routine_name: 'nightly',
    scope_slug: 'blizzard',
    sets: [],
    ...overrides,
  };
}

/** The run verb's answer: a freshly minted full run with no baseline. */
export function routineRunResponse(
  overrides: Pick<RoutineRunResponse, 'chunk_id'> & Partial<RoutineRunResponse>,
): RoutineRunResponse {
  return {
    baseline_finding_set_id: null,
    baseline_revisions: null,
    body: 'Routine: gardening',
    created_at: fixtureInstant(0),
    downgraded: false,
    effective_mode: 'full',
    ref: '1',
    routine_name: 'gardening',
    scope_slug: 'blizzard',
    source: 'hub',
    title: 'gardening run (full)',
    ...overrides,
  };
}

/** A routine's recorded baseline over one scope, with no repos. */
export function routineBaselineView(
  overrides: Pick<RoutineBaselineView, 'scope_slug'> & Partial<RoutineBaselineView>,
): RoutineBaselineView {
  return {
    finding_set_id: 'fins_1',
    recorded_at: fixtureInstant(-24 * 3600),
    repos: [],
    ...overrides,
  };
}

/** A live gardening routine, named after its slug, under no project. */
export function gardenRoutineView(overrides: Pick<RoutineView, 'routine_id' | 'name'> & Partial<RoutineView>): RoutineView {
  return {
    created_at: fixtureInstant(-7 * 24 * 3600),
    default_effort: null,
    default_harnesses: [],
    default_model: [],
    default_scope_slug: 'blizzard',
    graph_name: 'gardening',
    project_id: null,
    project_name: null,
    project_slug: null,
    retired: false,
    revision: 1,
    slug: overrides.name,
    ...overrides,
  };
}

/** A live scope, under no project. */
export function gardenScopeView(overrides: Pick<ScopeView, 'slug'> & Partial<ScopeView>): ScopeView {
  return {
    created_at: fixtureInstant(-7 * 24 * 3600),
    description: '',
    project_id: null,
    project_name: null,
    project_slug: null,
    retired: false,
    revision: 1,
    ...overrides,
  };
}

/** A project, named after its slug. */
export function gardenProjectView(
  overrides: Pick<ProjectView, 'project_id' | 'slug'> & Partial<ProjectView>,
): ProjectView {
  return {
    color: 'blue',
    created_at: fixtureInstant(-30 * 24 * 3600),
    description: '',
    name: overrides.slug,
    revision: 1,
    ...overrides,
  };
}
