import type { EventView } from '../../api/hub';
import { fixtureInstant } from './clock';

/** One fleet event-log entry: an info-severity lease mint on runner `rn_01`, a minute before {@link FIXTURE_NOW}. */
export function eventView(overrides: Partial<EventView> = {}): EventView {
  return {
    id: 1,
    recorded_at: fixtureInstant(-60),
    severity: 'info',
    kind: 'lease-minted',
    message: 'lease minted',
    runner_id: 'rn_01',
    runner_name: null,
    chunk_id: null,
    lease_id: null,
    node_name: null,
    detail: null,
    ...overrides,
  };
}
