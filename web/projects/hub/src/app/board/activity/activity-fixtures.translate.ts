import type { ActivityView } from 'fleet/hub-api';

/** A backfill HTTP row for transport stubs. */
export const activityView = (overrides: Partial<ActivityView>): ActivityView => ({
  type: 'chunk-changed',
  key: 'k',
  at: '2020-01-01T00:00:00Z',
  by: null,
  cause: null,
  chunk_id: null,
  graph_id: null,
  kind: null,
  node: null,
  prev_node: null,
  transition_from: null,
  transition_to: null,
  prev_status: null,
  reason: null,
  runner_id: null,
  runner_name: null,
  severity: null,
  status: null,
  ...overrides,
});
