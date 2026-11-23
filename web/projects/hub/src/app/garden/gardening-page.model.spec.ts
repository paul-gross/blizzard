import type { Proposal } from './core/proposal.domain';
import { gardenProposal, gardenProposalClosure } from './core/proposal.builders';
import { gardenProjectChoices, gardenSurfaceCounts, gardenSurfaceTabs, surfaceLabel, surfaceOf, waitingLabel, waitingProposalCount } from './gardening-page.model';

function proposal(id: string, closed: boolean): Proposal {
  return gardenProposal({ id, closure: closed ? gardenProposalClosure({ closedBy: 'op', reason: null }) : null });
}

describe('waitingProposalCount', () => {
  it('counts only proposals with no closure', () => {
    expect(waitingProposalCount([proposal('gp_1', false), proposal('gp_2', true), proposal('gp_3', false)])).toBe(2);
  });

  it('reads zero for an empty docket', () => {
    expect(waitingProposalCount([])).toBe(0);
  });
});

describe('surfaceOf and surfaceLabel', () => {
  it('gives runs and findings a surface each', () => {
    expect(surfaceOf('runs')).toBe('runs');
    expect(surfaceOf('findings')).toBe('findings');
    expect(surfaceLabel('runs')).toBe('Runs');
    expect(surfaceLabel('findings')).toBe('Findings');
    expect(surfaceLabel('scopes')).toBe('Scopes');
  });

  it('names no surface for a missing or unknown tab', () => {
    expect(surfaceOf(null)).toBeNull();
    expect(surfaceLabel('nope')).toBeNull();
  });
});

describe('gardenSurfaceTabs', () => {
  it('orders the six tabs, omitting a total badge for paged findings', () => {
    expect(gardenSurfaceTabs({ scopes: 4, routines: 2, cadences: 1, runs: null, findings: null, proposals: 3 })).toEqual([
      { surface: 'scopes', label: 'Scopes', count: 4 },
      { surface: 'routines', label: 'Routines', count: 2 },
      { surface: 'cadences', label: 'Cadences', count: 1 },
      { surface: 'runs', label: 'Runs', count: null },
      { surface: 'findings', label: 'Findings', count: null },
      { surface: 'proposals', label: 'Proposals', count: 3 },
    ]);
  });
});

describe('gardenProjectChoices', () => {
  const projects = [
    { project_id: 'p_w', slug: 'winter', name: 'winter' },
    { project_id: 'p_b', slug: 'blizzard', name: 'blizzard' },
  ] as never[];

  it('lists the projects by name with their proposals waiting, then All', () => {
    const proposals = [
      { ...proposal('gp_1', false), projectId: 'p_w' },
      { ...proposal('gp_2', true), projectId: 'p_w' },
      { ...proposal('gp_3', false), projectId: 'p_b' },
      { ...proposal('gp_4', false), projectId: 'p_b' },
    ];
    expect(gardenProjectChoices(projects, proposals)).toEqual([
      { slug: 'blizzard', label: 'blizzard', waiting: 2 },
      { slug: 'winter', label: 'winter', waiting: 1 },
      { slug: null, label: 'All projects', waiting: null },
    ]);
  });

  it('shows no waiting figure until the proposals are read', () => {
    expect(gardenProjectChoices(projects, undefined).map((choice) => choice.waiting)).toEqual([null, null, null]);
  });
});

describe('waitingLabel', () => {
  it('reads one proposal singular and the rest plural', () => {
    expect(waitingLabel(1)).toBe('1 proposal waiting');
    expect(waitingLabel(0)).toBe('0 proposals waiting');
  });
});

describe('gardenSurfaceCounts', () => {
  it('counts complete lists and waiting proposals, but not paged findings', () => {
    expect(
      gardenSurfaceCounts({
        scopes: [{}, {}],
        routines: [{}],
        cadences: [{}, {}, {}],
        runs: [],
        proposals: [proposal('gp_1', false), proposal('gp_2', true)],
      }),
    ).toEqual({ scopes: 2, routines: 1, cadences: 3, runs: 0, findings: null, proposals: 1 });
  });

  it('leaves a count out until its read answers', () => {
    expect(gardenSurfaceCounts({ scopes: undefined, routines: undefined, cadences: undefined, runs: undefined, proposals: undefined })).toEqual({
      scopes: null,
      routines: null,
      cadences: null,
      runs: null,
      findings: null,
      proposals: null,
    });
  });
});
