import { type GardenProposalClosureView, type GardenProposalView } from 'fleet';
import { gardenProposalClosureView, gardenProposalView } from 'fleet/testing';
import { describe, expect, it } from 'vitest';

import { fromGardenProposalClosureView, fromGardenProposalView } from './proposal.translate';

describe('fromGardenProposalView', () => {
  it('reads every field of a waiting operator proposal', () => {
    expect(
      fromGardenProposalView(
        gardenProposalView({
          proposal_id: 'gp_1',
          title: 'T',
          body: 'B',
          class: 'remediate',
          origin: 'operator',
          created_by: 'u_1',
          created_at: '2026-01-01T00:00:00Z',
          routine_name: null,
          project_id: 'prj_1',
          findings: ['fnd_1', 'fnd_2'],
        }),
      ),
    ).toEqual({
      id: 'gp_1',
      title: 'T',
      body: 'B',
      proposalClass: 'remediate',
      origin: 'operator',
      createdBy: 'u_1',
      createdAt: '2026-01-01T00:00:00Z',
      routineSlug: null,
      projectId: 'prj_1',
      findingIds: ['fnd_1', 'fnd_2'],
      closure: null,
    });
  });

  it('reads an accepted-and-minted closure', () => {
    const proposal = fromGardenProposalView(
      gardenProposalView({
        proposal_id: 'gp_1',
        closure: gardenProposalClosureView({ closure: 'accepted', item_outcome: 'minted', source: 'hub', ref: '42', reason: null }),
      }),
    );
    expect(proposal.closure).toMatchObject({ kind: 'accepted', itemOutcome: 'minted', source: 'hub', ref: '42', reason: null });
  });

  it('carries an origin this build does not know as unlisted', () => {
    const proposal = fromGardenProposalView(gardenProposalView({ proposal_id: 'gp_1', origin: 'import' as GardenProposalView['origin'] }));
    expect(proposal.origin).toEqual({ unlisted: 'import' });
  });
});

describe('fromGardenProposalClosureView', () => {
  it('reads a passed closure, minting nothing', () => {
    expect(fromGardenProposalClosureView(gardenProposalClosureView())).toEqual({
      kind: 'passed',
      closedBy: 'u_1',
      closedAt: gardenProposalClosureView().closed_at,
      reason: 'not worth it yet',
      itemOutcome: null,
      source: null,
      ref: null,
    });
  });

  it('carries a closure kind and item outcome this build does not know as unlisted', () => {
    const closure = fromGardenProposalClosureView(
      gardenProposalClosureView({
        closure: 'deferred' as GardenProposalClosureView['closure'],
        item_outcome: 'queued' as GardenProposalClosureView['item_outcome'],
      }),
    );
    expect(closure.kind).toEqual({ unlisted: 'deferred' });
    expect(closure.itemOutcome).toEqual({ unlisted: 'queued' });
  });
});
