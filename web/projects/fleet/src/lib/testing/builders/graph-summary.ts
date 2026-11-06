import type { GraphSummaryView } from '../../api/hub';
import { fixtureInstant } from './clock';

/** One graph-list row: the effective `build` graph, minted a day before {@link FIXTURE_NOW}. */
export function graphSummaryView(overrides: Partial<GraphSummaryView> = {}): GraphSummaryView {
  return {
    graph_id: 'gr_build',
    name: 'build',
    created_at: fixtureInstant(-24 * 3600),
    effective: true,
    entry_node_id: 'nd_build',
    lifecycle: 'effective',
    retired: false,
    ...overrides,
  };
}
