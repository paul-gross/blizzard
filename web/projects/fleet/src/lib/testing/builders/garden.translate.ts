import type {
  FindingDetailView,
  FindingFactView,
  FindingView,
  GardenProposalCountsRowView,
  GardenProposalCountsView,
  GardenSweepsView,
  GraphSummaryView,
  MeasurementReadingView,
  ScopeSweepView,
  TrendPeriodView,
  TrendView,
  WorkItemView,
} from '../../api/hub';

/** A live routine-raised finding `fnd_1` in scope `blizzard`, never observed, with no severity. */
export function gardenFindingView(overrides: Partial<FindingView> = {}): FindingView {
  return {
    finding_id: 'fnd_1',
    class: 'stale-docstring',
    summary: 'A stale docstring',
    locus: 'docs/a.md',
    scope_slug: 'blizzard',
    state: 'live',
    live: true,
    source: 'routine',
    severity: null,
    exit: null,
    note: null,
    observed_count: 0,
    first_observed_at: null,
    last_seen_at: null,
    introduced: null,
    introduced_at: null,
    project_id: null,
    project_name: null,
    project_slug: null,
    raised_by_chunk_id: null,
    routine_name: null,
    ...overrides,
  };
}

/** {@link gardenFindingView}'s finding with its fact history — empty unless the caller names one. */
export function gardenFindingDetailView(overrides: Partial<FindingDetailView> = {}): FindingDetailView {
  return { ...gardenFindingView(), facts: [], ...overrides };
}

/** One `add` fact on a finding's history, recorded by no one at the start of 2026. */
export function gardenFindingFactView(overrides: Partial<FindingFactView> = {}): FindingFactView {
  return {
    kind: 'add',
    actor: null,
    note: null,
    proposal_id: null,
    superseded_by: null,
    recorded_at: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

/** One proposal-counts row for routine `nightly`'s class `stale-docstring`, all zero. */
export function gardenProposalCountsRowView(
  overrides: Partial<GardenProposalCountsRowView> = {},
): GardenProposalCountsRowView {
  return {
    origin: 'routine-run',
    routine_name: 'nightly',
    class: 'stale-docstring',
    open: 0,
    passed: 0,
    accepted_with_item: 0,
    accepted_without_item: 0,
    created: 0,
    project_id: null,
    project_name: null,
    project_slug: null,
    ...overrides,
  };
}

/** Proposal counts over January 2026 for every routine and origin, with no rows. */
export function gardenProposalCountsView(overrides: Partial<GardenProposalCountsView> = {}): GardenProposalCountsView {
  return {
    since: '2026-01-01T00:00:00Z',
    until: '2026-01-29T00:00:00Z',
    routine: null,
    origin: null,
    rows: [],
    ...overrides,
  };
}

/** One quiet trend week starting 2026-01-01. */
export function gardenTrendPeriodView(overrides: Partial<TrendPeriodView> = {}): TrendPeriodView {
  return {
    period_start: '2026-01-01T00:00:00Z',
    period_end: '2026-01-08T00:00:00Z',
    created: 0,
    exits: {},
    outflow: 0,
    withdrawn: 0,
    reopened: 0,
    ...overrides,
  };
}

/** Routine `nightly`'s weekly trend over January 2026, with no periods and no live findings. */
export function gardenTrendView(overrides: Partial<TrendView> = {}): TrendView {
  return {
    routine_name: 'nightly',
    since: '2026-01-01T00:00:00Z',
    until: '2026-01-29T00:00:00Z',
    period_days: 7,
    periods: [],
    age: { boundary: '2026-01-01T00:00:00Z', recent: 0, older: 0, unattributed: 0 },
    ...overrides,
  };
}

/** Scope `blizzard`, never swept. */
export function gardenScopeSweepView(overrides: Partial<ScopeSweepView> = {}): ScopeSweepView {
  return { scope_slug: 'blizzard', finding_set_id: null, produced_at: null, revisions: {}, ...overrides };
}

/** One measurement reading on scope `blizzard`. */
export function gardenMeasurementReadingView(overrides: Partial<MeasurementReadingView> = {}): MeasurementReadingView {
  return { scope_slug: 'blizzard', produced_at: '2026-01-10T00:00:00Z', measurement: '0 findings', ...overrides };
}

/** Routine `nightly`'s sweeps over January 2026, nothing swept or measured. */
export function gardenSweepsView(overrides: Partial<GardenSweepsView> = {}): GardenSweepsView {
  return {
    routine_name: 'nightly',
    since: '2026-01-01T00:00:00Z',
    until: '2026-01-29T00:00:00Z',
    last_swept: [],
    measurements: [],
    ...overrides,
  };
}

/** The effective `garden-routine` graph's list row, entered at `nd_1`. */
export function gardenGraphSummaryView(overrides: Partial<GraphSummaryView> = {}): GraphSummaryView {
  return {
    graph_id: 'gr_1',
    name: 'garden-routine',
    entry_node_id: 'nd_1',
    created_at: '2026-01-01T00:00:00Z',
    effective: true,
    lifecycle: 'effective',
    retired: false,
    ...overrides,
  };
}

/** An open work item `hub#42` written by user `u_1`, with no priority or link. */
export function gardenWorkItemView(overrides: Partial<WorkItemView> = {}): WorkItemView {
  return {
    source: 'hub',
    ref: '42',
    slug: null,
    title: 't',
    body: 'b',
    author: { kind: 'user', user_id: 'u_1', chunk_id: null, login: null, node_name: null, runner_id: null, runner_name: null },
    closure: null,
    closed_at: null,
    created_at: '2026-01-01T00:00:00Z',
    edited_at: '2026-01-01T00:00:00Z',
    label: 'hub#42',
    stated_priority: null,
    web_url: null,
    ...overrides,
  };
}
