import { type GardenProposalView } from 'fleet';

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
  proposalRoutineChips,
  waitingChipValue,
  type ProposalFilters,
} from './gardening-proposals-page.model';

function proposal(overrides: Partial<GardenProposalView> = {}): GardenProposalView {
  return {
    proposal_id: 'gp_1',
    origin: 'routine-run',
    routine_name: 'comments',
    class: 'stale-docstring',
    title: 'Fix the docstring',
    body: 'b',
    created_at: '2026-01-01T00:00:00Z',
    findings: [],
    closure: null,
    ...overrides,
  };
}

const PASSED = {
  closure: 'passed',
  closed_by: 'op',
  closed_at: '2026-01-02T00:00:00Z',
  reason: null,
  item_outcome: null,
  source: null,
  ref: null,
} as const;

const NO_FILTERS: ProposalFilters = { waitingOnly: false, cls: null, routine: null, closing: new Set() };

describe('isGardenProposalWaiting', () => {
  it('is waiting when the proposal carries no closure', () => {
    expect(isGardenProposalWaiting({ closure: null } as never)).toBe(true);
    expect(isGardenProposalWaiting({} as never)).toBe(true);
  });

  it('is not waiting once a closure is recorded', () => {
    expect(isGardenProposalWaiting({ closure: { closure: 'passed' } } as never)).toBe(false);
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
      proposal({ class: 'zeta' }),
      proposal({ class: 'all' }),
      proposal({ class: 'zeta' }),
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
      proposal({ routine_name: 'comments' }),
      proposal({ routine_name: null, origin: 'operator', created_by: 'alice' }),
      proposal({ routine_name: 'architecture' }),
      proposal({ routine_name: 'comments' }),
    ]);
    expect(chips).toEqual([
      { value: ALL_ROUTINES, label: 'All routines', testid: 'gardening-proposal-routine-all' },
      { value: 'architecture', label: 'architecture', testid: 'gardening-proposal-routine-item-architecture' },
      { value: 'comments', label: 'comments', testid: 'gardening-proposal-routine-item-comments' },
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
  const waiting = proposal({ proposal_id: 'gp_1' });
  const closing = proposal({ proposal_id: 'gp_2' });
  const passed = proposal({ proposal_id: 'gp_3', closure: PASSED });
  const otherClass = proposal({ proposal_id: 'gp_4', class: 'dead-code', routine_name: 'architecture' });
  const docket = [waiting, closing, passed, otherClass];
  const ids = (ps: readonly GardenProposalView[]) => ps.map((p) => p.proposal_id);

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
    expect(proposalListRows([proposal(), proposal({ proposal_id: 'gp_3', closure: PASSED })])).toEqual([
      {
        proposalId: 'gp_1',
        title: 'Fix the docstring',
        proposalClass: 'stale-docstring',
        waiting: true,
        createdAt: '2026-01-01T00:00:00Z',
      },
      {
        proposalId: 'gp_3',
        title: 'Fix the docstring',
        proposalClass: 'stale-docstring',
        waiting: false,
        createdAt: '2026-01-01T00:00:00Z',
      },
    ]);
  });
});
