import { type GardenProposalView } from 'fleet';

import { gardenProjectChoices, gardenSurfaceCounts, gardenSurfaceTabs, surfaceLabel, surfaceOf, waitingLabel, waitingProposalCount } from './gardening-page.model';

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
  it('orders the five tabs scopes, routines, runs, findings, proposals, each with its count', () => {
    expect(gardenSurfaceTabs({ scopes: 4, routines: 2, runs: null, findings: 7, proposals: 3 })).toEqual([
      { surface: 'scopes', label: 'Scopes', count: 4 },
      { surface: 'routines', label: 'Routines', count: 2 },
      { surface: 'runs', label: 'Runs', count: null },
      { surface: 'findings', label: 'Findings', count: 7 },
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
      { ...proposal('gp_1', false), project_id: 'p_w' },
      { ...proposal('gp_2', true), project_id: 'p_w' },
      { ...proposal('gp_3', false), project_id: 'p_b' },
      { ...proposal('gp_4', false), project_id: 'p_b' },
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
  it('counts each list, and for proposals only the waiting ones', () => {
    expect(
      gardenSurfaceCounts({
        scopes: [{}, {}],
        routines: [{}],
        runs: [],
        findings: [{}, {}, {}],
        proposals: [proposal('gp_1', false), proposal('gp_2', true)],
      }),
    ).toEqual({ scopes: 2, routines: 1, runs: 0, findings: 3, proposals: 1 });
  });

  it('leaves a count out until its read answers', () => {
    expect(gardenSurfaceCounts({ scopes: undefined, routines: undefined, runs: undefined, findings: undefined, proposals: undefined })).toEqual({
      scopes: null,
      routines: null,
      runs: null,
      findings: null,
      proposals: null,
    });
  });
});
