import type { FlowFactView, FlowView } from '../../api/hub';
import { fixtureInstant } from './clock';

/** An idle delivery flow onto `rp_a`'s `master`: nothing in line, no turn, nothing landed or judged yet. */
export function flowView(overrides: Partial<FlowView> = {}): FlowView {
  return {
    flow_id: 'fl_a',
    repository_id: 'rp_a',
    repository_name: null,
    repository_slug: null,
    branch: 'master',
    resource: 'rp_a:master',
    line: [],
    turn: null,
    last_landing: null,
    last_verdict: null,
    ...overrides,
  };
}

/** One flow-record fact: a chunk's submission onto `fl_a`, a minute before {@link FIXTURE_NOW}. */
export function flowFactView(overrides: Partial<FlowFactView> = {}): FlowFactView {
  return {
    id: 1,
    flow_id: 'fl_a',
    chunk_id: 'ch_01',
    kind: 'submitted',
    at: fixtureInstant(-60),
    outcome: null,
    revision: null,
    rework: false,
    submission_id: 'sub-ch_01',
    verdict: null,
    wait: false,
    ...overrides,
  };
}
