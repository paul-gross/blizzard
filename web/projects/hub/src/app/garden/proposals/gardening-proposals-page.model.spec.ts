import { fixtureInstant } from 'fleet/testing';

import type { Proposal } from '../core/proposal.domain';
import { gardenProposal, gardenProposalClosure } from '../core/proposal.builders';
import { gardenRoutine } from '../core/routine.builders';

import {
  ALL_CLASSES,
  ALL_ROUTINES,
  SHOW_ALL,
  classChipValue,
  filterProposals,
  isGardenProposalWaiting,
  pendingClosureIds,
  proposalClassChips,
  proposalListRows,
  proposalSelectionTarget,
  proposalRoutineChips,
  waitingChipValue,
  type ProposalFilters,
} from './gardening-proposals-page.model';

const CREATED_AT = fixtureInstant(-24 * 3600);

function proposal(overrides: Partial<Proposal> = {}): Proposal {
  return gardenProposal({
    id: 'gp_1',
    routineSlug: 'comments',
    proposalClass: 'stale-docstring',
    title: 'Fix the docstring',
    body: 'b',
    createdAt: CREATED_AT,
    ...overrides,
  });
}

const PASSED = gardenProposalClosure({ closedBy: 'op', reason: null });

const NO_FILTERS: ProposalFilters = { waitingOnly: false, cls: null, routine: null, closing: new Set() };

describe('isGardenProposalWaiting', () => {
  it('is waiting when the proposal carries no closure', () => {
    expect(isGardenProposalWaiting(proposal({ closure: null }))).toBe(true);
  });

  it('is not waiting once a closure is recorded', () => {
    expect(isGardenProposalWaiting(proposal({ closure: PASSED }))).toBe(false);
  });
});

describe('pendingClosureIds', () => {
  it('collects the proposal ids of every pending pass and accept', () => {
    const ids = pendingClosureIds(
      [{ proposalId: 'gp_1', reason: 'r' }],
      [
        { proposalId: 'gp_2', mintWorkItem: true },
        { proposalId: 'gp_1', mintWorkItem: false },
      ],
    );
    expect([...ids].sort()).toEqual(['gp_1', 'gp_2']);
  });

  it('is empty with nothing in flight', () => {
    expect(pendingClosureIds([], []).size).toBe(0);
  });
});

describe('proposalClassChips', () => {
  it('leads with All, then each distinct class alphabetized and prefixed', () => {
    const chips = proposalClassChips([
      proposal({ proposalClass: 'zeta' }),
      proposal({ proposalClass: 'all' }),
      proposal({ proposalClass: 'zeta' }),
    ]);
    expect(chips).toEqual([
      { value: ALL_CLASSES, label: 'All classes', testid: 'gardening-proposal-class-all' },
      { value: 'class:all', label: 'all', testid: 'gardening-proposal-class-item-all' },
      { value: 'class:zeta', label: 'zeta', testid: 'gardening-proposal-class-item-zeta' },
    ]);
  });

  it('offers only All on an empty docket', () => {
    expect(proposalClassChips([]).map((c) => c.value)).toEqual([ALL_CLASSES]);
  });
});

describe('classChipValue', () => {
  it('reads the All chip for no class filter', () => {
    expect(classChipValue(null)).toBe(ALL_CLASSES);
  });

  it('prefixes a real class, so a class named all never selects the All chip', () => {
    expect(classChipValue('all')).toBe('class:all');
  });
});

describe('proposalRoutineChips', () => {
  it('leads with All, then each distinct named routine alphabetized, skipping routine-less proposals', () => {
    const chips = proposalRoutineChips([
      proposal({ routineSlug: 'comments' }),
      proposal({ routineSlug: null, origin: 'operator', createdBy: 'alice' }),
      proposal({ routineSlug: 'architecture' }),
      proposal({ routineSlug: 'comments' }),
    ], []);
    expect(chips).toEqual([
      { value: ALL_ROUTINES, label: 'All routines', testid: 'gardening-proposal-routine-all' },
      { value: 'architecture', label: 'architecture', testid: 'gardening-proposal-routine-item-architecture' },
      { value: 'comments', label: 'comments', testid: 'gardening-proposal-routine-item-comments' },
    ]);
  });

  it('keys each chip by slug and labels it with the display name, alphabetized by the label', () => {
    const chips = proposalRoutineChips(
      [proposal({ routineSlug: 'arch' }), proposal({ routineSlug: 'cmt' })],
      [gardenRoutine({ slug: 'arch', name: 'Zoning review' }), gardenRoutine({ slug: 'cmt', name: 'Comment sweep' })],
    );
    expect(chips.slice(1)).toEqual([
      { value: 'cmt', label: 'Comment sweep', testid: 'gardening-proposal-routine-item-cmt' },
      { value: 'arch', label: 'Zoning review', testid: 'gardening-proposal-routine-item-arch' },
    ]);
  });
});

describe('waitingChipValue', () => {
  it('reads waiting under the waiting-only filter and all otherwise', () => {
    expect(waitingChipValue(true)).toBe('waiting');
    expect(waitingChipValue(false)).toBe(SHOW_ALL);
  });
});

describe('filterProposals', () => {
  const waiting = proposal({ id: 'gp_1' });
  const closing = proposal({ id: 'gp_2' });
  const passed = proposal({ id: 'gp_3', closure: PASSED });
  const otherClass = proposal({ id: 'gp_4', proposalClass: 'dead-code', routineSlug: 'architecture' });
  const docket = [waiting, closing, passed, otherClass];
  const ids = (ps: readonly Proposal[]) => ps.map((p) => p.id);

  it('keeps everything with no filter, closed and closing alike', () => {
    expect(ids(filterProposals(docket, { ...NO_FILTERS, closing: new Set(['gp_2']) }))).toEqual([
      'gp_1',
      'gp_2',
      'gp_3',
      'gp_4',
    ]);
  });

  it('drops closed and closing proposals under waiting-only', () => {
    expect(ids(filterProposals(docket, { ...NO_FILTERS, waitingOnly: true, closing: new Set(['gp_2']) }))).toEqual([
      'gp_1',
      'gp_4',
    ]);
  });

  it('narrows to one class', () => {
    expect(ids(filterProposals(docket, { ...NO_FILTERS, cls: 'dead-code' }))).toEqual(['gp_4']);
  });

  it('narrows to one routine', () => {
    expect(ids(filterProposals(docket, { ...NO_FILTERS, routine: 'comments' }))).toEqual(['gp_1', 'gp_2', 'gp_3']);
  });
});

describe('proposalListRows', () => {
  it('maps each proposal, reading waiting off its closure', () => {
    expect(proposalListRows([proposal(), proposal({ id: 'gp_3', closure: PASSED })])).toEqual([
      {
        proposalId: 'gp_1',
        title: 'Fix the docstring',
        proposalClass: 'stale-docstring',
        waiting: true,
        createdAt: CREATED_AT,
      },
      {
        proposalId: 'gp_3',
        title: 'Fix the docstring',
        proposalClass: 'stale-docstring',
        waiting: false,
        createdAt: CREATED_AT,
      },
    ]);
  });
});

describe('proposalSelectionTarget', () => {
  const ROWS = ['p1', 'p2'];

  it('stays while the list read has not settled', () => {
    expect(proposalSelectionTarget(false, 'gone', [], false)).toBeNull();
    expect(proposalSelectionTarget(false, null, ROWS, false)).toBeNull();
  });

  it('stays on a routed proposal the filters keep', () => {
    expect(proposalSelectionTarget(true, 'p2', ROWS, false)).toBeNull();
    expect(proposalSelectionTarget(true, 'p2', ROWS, true)).toBeNull();
  });

  it('moves an excluded proposal to the first row on desktop', () => {
    expect(proposalSelectionTarget(true, 'gone', ROWS, false)).toEqual({ proposalId: 'p1' });
  });

  it('opens the first row from a bare route on desktop', () => {
    expect(proposalSelectionTarget(true, null, ROWS, false)).toEqual({ proposalId: 'p1' });
  });

  it('returns an excluded proposal to the bare docket when no row remains on desktop', () => {
    expect(proposalSelectionTarget(true, 'gone', [], false)).toEqual({ proposalId: null });
  });

  it('stays on a bare route over an empty docket on desktop', () => {
    expect(proposalSelectionTarget(true, null, [], false)).toBeNull();
  });

  it('keeps a routed proposal an open closing dialog holds, though the filters exclude it', () => {
    expect(proposalSelectionTarget(true, 'gone', ROWS, false, 'gone')).toBeNull();
    expect(proposalSelectionTarget(true, 'gone', [], true, 'gone')).toBeNull();
    expect(proposalSelectionTarget(true, 'gone', ROWS, false, 'p2')).toEqual({ proposalId: 'p1' });
  });

  it('returns an excluded proposal to the docket on mobile and leaves a bare route there', () => {
    expect(proposalSelectionTarget(true, 'gone', ROWS, true)).toEqual({ proposalId: null });
    expect(proposalSelectionTarget(true, null, ROWS, true)).toBeNull();
  });
});
