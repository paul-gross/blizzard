import { type GardenProposalView } from 'fleet';

import { waitingProposalCount } from './gardening-page.model';

function proposal(id: string, closed: boolean): GardenProposalView {
  return {
    proposal_id: id,
    origin: 'routine-run',
    routine_name: 'nightly',
    class: 'stale-docstring',
    title: 't',
    body: 'b',
    created_at: '2026-01-01T00:00:00Z',
    findings: [],
    closure: closed
      ? {
          closure: 'passed',
          closed_by: 'op',
          closed_at: '2026-01-02T00:00:00Z',
          reason: null,
          item_outcome: null,
          source: null,
          ref: null,
        }
      : null,
  };
}

describe('waitingProposalCount', () => {
  it('counts only proposals with no closure', () => {
    expect(waitingProposalCount([proposal('gp_1', false), proposal('gp_2', true), proposal('gp_3', false)])).toBe(2);
  });

  it('reads zero for an empty docket', () => {
    expect(waitingProposalCount([])).toBe(0);
  });
});
