import { fixtureInstant } from 'fleet/testing';

import type { Finding } from '../core/finding.domain';
import { gardenFinding } from '../core/finding.builders';
import type { Proposal, ProposalClosure } from '../core/proposal.domain';
import { gardenProposal, gardenProposalClosure } from '../core/proposal.builders';
import { gardenRoutine } from '../core/routine.builders';

import {
  acceptedItemPointer,
  proposalById,
  proposalClosureVm,
  proposalEvidenceRows,
  proposalOriginVm,
  proposalPanelState,
  proposalPanelVm,
  proposalWorkItemVm,
} from './gardening-proposal-detail.model';

const CREATED_AT = fixtureInstant(-24 * 3600);
const CLOSED_AT = fixtureInstant(-3600);

function proposal(overrides: Partial<Proposal> = {}): Proposal {
  return gardenProposal({
    id: 'gp_1',
    routineSlug: 'nightly',
    proposalClass: 'stale-docstring',
    title: 'Extract the shared helper',
    body: 'Three call sites duplicate this logic.',
    createdAt: CREATED_AT,
    ...overrides,
  });
}

function closure(overrides: Partial<ProposalClosure> = {}): ProposalClosure {
  return gardenProposalClosure({
    kind: 'accepted',
    closedBy: 'op',
    closedAt: CLOSED_AT,
    reason: 'worth it',
    itemOutcome: 'minted',
    source: 'blizzard',
    ref: 'ch_7',
    ...overrides,
  });
}

function finding(id: string, overrides: Partial<Finding> = {}): Finding {
  return gardenFinding({ id, locus: 'a.py:1', summary: 'summary', observedCount: 1, ...overrides });
}

function query(pending: boolean, error = false) {
  return { isPending: () => pending, isError: () => error };
}

const WORK_ITEM = { label: 'Extract the helper', webUrl: 'https://example.test/ch_7' };

describe('proposalById', () => {
  const docket = [proposal(), proposal({ id: 'gp_2' })];

  it('reads null on the bare route', () => {
    expect(proposalById(docket, null)).toBeNull();
  });

  it('reads null for an id the docket does not hold', () => {
    expect(proposalById(docket, 'gp_9')).toBeNull();
  });

  it('finds the routed proposal', () => {
    expect(proposalById(docket, 'gp_2')).toBe(docket[1]);
  });
});

describe('proposalPanelState', () => {
  it('is ready once a proposal is selected, whatever the read says', () => {
    expect(proposalPanelState(proposal(), query(true))).toBe('ready');
  });

  it('follows the list read, as empty, with nothing selected', () => {
    expect(proposalPanelState(null, query(true))).toBe('loading');
    expect(proposalPanelState(null, query(false, true))).toBe('error');
    expect(proposalPanelState(null, query(false))).toBe('empty');
  });
});

describe('acceptedItemPointer', () => {
  it('reads null with no closure', () => {
    expect(acceptedItemPointer(null)).toBeNull();
    expect(acceptedItemPointer(undefined)).toBeNull();
  });

  it('reads null for a passed proposal', () => {
    expect(acceptedItemPointer(closure({ kind: 'passed', itemOutcome: null }))).toBeNull();
  });

  it('reads null for an accepted proposal whose item was not minted', () => {
    expect(acceptedItemPointer(closure({ itemOutcome: 'declined' }))).toBeNull();
  });

  it('names the minted item by source and ref', () => {
    expect(acceptedItemPointer(closure())).toEqual({ source: 'blizzard', ref: 'ch_7' });
  });
});

describe('proposalWorkItemVm', () => {
  const pointer = { source: 'blizzard', ref: 'ch_7' };

  it('reads null with no pointer', () => {
    expect(proposalWorkItemVm(null, false, undefined)).toBeNull();
  });

  it('reads null while the read is in flight', () => {
    expect(proposalWorkItemVm(pointer, true, { label: 'x', webUrl: null })).toBeNull();
  });

  it('carries the resolved record', () => {
    expect(proposalWorkItemVm(pointer, false, { label: 'Extract', webUrl: 'https://example.test/ch_7' })).toEqual({
      label: 'Extract',
      webUrl: 'https://example.test/ch_7',
    });
  });

  it('falls back to the bare pointer once the read failed', () => {
    expect(proposalWorkItemVm(pointer, false, undefined)).toEqual({ label: 'blizzard:ch_7', webUrl: null });
  });

  it('falls back to the bare pointer for a label-less record', () => {
    expect(proposalWorkItemVm(pointer, false, { label: null, webUrl: null })).toEqual({
      label: 'blizzard:ch_7',
      webUrl: null,
    });
  });
});

describe('proposalOriginVm', () => {
  it('names a routine run by its routine', () => {
    expect(proposalOriginVm(proposal(), [])).toEqual({ kind: 'routine-run', routineName: 'nightly' });
  });

  it("reads the routine's display name, looked up by the slug the proposal carries", () => {
    const routines = [gardenRoutine({ slug: 'nightly', name: 'Nightly sweep' })];
    expect(proposalOriginVm(proposal(), routines)).toEqual({ kind: 'routine-run', routineName: 'Nightly sweep' });
    expect(
      proposalOriginVm(proposal({ origin: 'operator', createdBy: 'alice', routineSlug: 'nightly' }), routines),
    ).toEqual({ kind: 'operator', createdBy: 'alice', routineName: 'Nightly sweep' });
  });

  it('gives an origin the client does not declare the unlisted arm, raw', () => {
    expect(proposalOriginVm(proposal({ origin: { unlisted: 'importer' } }), [])).toEqual({ kind: 'unlisted', raw: 'importer' });
  });

  it('names an operator and the routine it may cite', () => {
    expect(proposalOriginVm(proposal({ origin: 'operator', createdBy: 'alice', routineSlug: null }), [])).toEqual({
      kind: 'operator',
      createdBy: 'alice',
      routineName: null,
    });
  });
});

describe('proposalClosureVm', () => {
  it('maps a passed closure without a work item', () => {
    expect(proposalClosureVm(closure({ kind: 'passed', itemOutcome: null }), WORK_ITEM)).toEqual({
      kind: 'passed',
      closedBy: 'op',
      closedAt: CLOSED_AT,
      reason: 'worth it',
    });
  });

  it('gives a closure the client does not declare the unlisted arm, raw', () => {
    expect(proposalClosureVm(closure({ kind: { unlisted: 'deferred' } }), WORK_ITEM)).toEqual({
      kind: 'unlisted',
      raw: 'deferred',
      closedBy: 'op',
      closedAt: CLOSED_AT,
      reason: 'worth it',
    });
  });

  it('carries the work item on an accepted-and-minted closure', () => {
    expect(proposalClosureVm(closure(), WORK_ITEM)).toEqual({
      kind: 'accepted',
      closedBy: 'op',
      closedAt: CLOSED_AT,
      reason: 'worth it',
      workItem: WORK_ITEM,
    });
  });

  it('drops the work item on an accepted-and-declined closure', () => {
    expect(proposalClosureVm(closure({ itemOutcome: 'declined' }), WORK_ITEM)).toMatchObject({
      kind: 'accepted',
      workItem: null,
    });
  });
});

describe('proposalPanelVm', () => {
  it('reads null with nothing selected', () => {
    expect(proposalPanelVm(null, WORK_ITEM, [])).toBeNull();
  });

  it('maps a waiting proposal with no findings', () => {
    expect(proposalPanelVm(proposal(), null, [])).toEqual({
      proposalId: 'gp_1',
      origin: { kind: 'routine-run', routineName: 'nightly' },
      proposalClass: 'stale-docstring',
      title: 'Extract the shared helper',
      body: 'Three call sites duplicate this logic.',
      closure: null,
      createdAt: CREATED_AT,
      hasFindings: false,
    });
  });

  it('maps the closure and flags cited findings', () => {
    const vm = proposalPanelVm(proposal({ closure: closure(), findingIds: ['fnd_1'] }), WORK_ITEM, []);
    expect(vm?.closure).toMatchObject({ kind: 'accepted', workItem: WORK_ITEM });
    expect(vm?.hasFindings).toBe(true);
  });
});

describe('proposalEvidenceRows', () => {
  it('maps each finding, repeating the work item and nulling an absent exit', () => {
    expect(proposalEvidenceRows([finding('fnd_1')], WORK_ITEM, [])).toEqual([
      {
        findingId: 'fnd_1',
        locus: 'a.py:1',
        summary: 'summary',
        state: 'live',
        exit: null,
        workItem: WORK_ITEM,
        pending: false,
      },
    ]);
  });

  it('marks only rows whose finding has triage in flight as pending', () => {
    const rows = proposalEvidenceRows(
      [finding('fnd_1', { exit: 'outflow' }), finding('fnd_2')],
      null,
      [{ findingIds: ['fnd_2', 'fnd_9'], note: 'n' }],
    );
    expect(rows.map((r) => [r.findingId, r.exit, r.pending])).toEqual([
      ['fnd_1', 'outflow', false],
      ['fnd_2', null, true],
    ]);
  });
});
