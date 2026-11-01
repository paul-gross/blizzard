import type { RepositoryLinkView } from 'fleet';

import { foldLandingPositions, landingSections, type LandingSections, resolveLandingDrop } from './project-landing-order.model';
import type { ProjectLinkRowVm } from './projects.model';

const row = (key: string, name: string): ProjectLinkRowVm => ({ key, name, detail: '', builtIn: false, revision: 1 });
const link = (repository_id: string, slug: string, position: number | null): RepositoryLinkView => ({
  project_id: 'proj_b',
  repository_id,
  slug,
  name: slug,
  linked: true,
  position,
  revision: 1,
});

const A = row('repo_a', 'alpha');
const B = row('repo_b', 'bravo');
const C = row('repo_c', 'charlie');
const X = row('repo_x', 'xray');
const Y = row('repo_y', 'yankee');
/** Ranked a, b, c; unranked x, y — the hub's order. */
const SECTIONS: LandingSections = { ranked: [A, B, C], unranked: [X, Y] };
const keys = (sections: LandingSections) => ({
  ranked: sections.ranked.map((r) => r.key),
  unranked: sections.unranked.map((r) => r.key),
});

describe('landingSections', () => {
  it("keeps the hub's order: ranked first, then unranked in its slug order, never re-sorted", () => {
    const rows = [C, A, X, Y];
    const links = [link('repo_c', 'charlie', 1), link('repo_a', 'alpha', 2), link('repo_x', 'xray', null), link('repo_y', 'yankee', null)];

    expect(keys(landingSections(rows, links))).toEqual({ ranked: ['repo_c', 'repo_a'], unranked: ['repo_x', 'repo_y'] });
  });
});

describe('resolveLandingDrop', () => {
  it('places a repository dropped between two ranked ones after its new predecessor', () => {
    expect(resolveLandingDrop(SECTIONS, { key: 'repo_c', from: 'ranked', previousIndex: 2, to: 'ranked', currentIndex: 1 })).toEqual({
      repository: 'repo_c',
      after_repository: 'repo_a',
    });
  });

  it('places a repository dropped at the front after nothing', () => {
    expect(resolveLandingDrop(SECTIONS, { key: 'repo_b', from: 'ranked', previousIndex: 1, to: 'ranked', currentIndex: 0 })).toEqual({
      repository: 'repo_b',
      after_repository: null,
    });
  });

  it('unranks a repository dropped from ranked into "Unranked"', () => {
    expect(resolveLandingDrop(SECTIONS, { key: 'repo_a', from: 'ranked', previousIndex: 0, to: 'unranked', currentIndex: 1 })).toEqual({
      repository: 'repo_a',
      unranked: true,
    });
  });

  it('ranks a repository dropped from "Unranked" after the ranked one above it', () => {
    expect(resolveLandingDrop(SECTIONS, { key: 'repo_y', from: 'unranked', previousIndex: 1, to: 'ranked', currentIndex: 3 })).toEqual({
      repository: 'repo_y',
      after_repository: 'repo_c',
    });
    expect(resolveLandingDrop(SECTIONS, { key: 'repo_x', from: 'unranked', previousIndex: 0, to: 'ranked', currentIndex: 0 })).toEqual({
      repository: 'repo_x',
      after_repository: null,
    });
  });

  it('writes nothing for a move within "Unranked", or a drop that changes nothing', () => {
    expect(resolveLandingDrop(SECTIONS, { key: 'repo_x', from: 'unranked', previousIndex: 0, to: 'unranked', currentIndex: 1 })).toBeNull();
    expect(resolveLandingDrop(SECTIONS, { key: 'repo_b', from: 'ranked', previousIndex: 1, to: 'ranked', currentIndex: 1 })).toBeNull();
  });
});

describe('foldLandingPositions', () => {
  it('renders a pending placement where it asked to land, before it settles', () => {
    const folded = foldLandingPositions(SECTIONS, [{ projectId: 'proj_b', body: { repository: 'repo_c', after_repository: null } }], 'proj_b');

    expect(keys(folded)).toEqual({ ranked: ['repo_c', 'repo_a', 'repo_b'], unranked: ['repo_x', 'repo_y'] });
  });

  it('ranks a pending row out of "Unranked", and unranks one into its slug place', () => {
    const folded = foldLandingPositions(
      SECTIONS,
      [
        { projectId: 'proj_b', body: { repository: 'repo_y', after_repository: 'repo_a' } },
        { projectId: 'proj_b', body: { repository: 'repo_b', unranked: true } },
      ],
      'proj_b',
    );

    expect(keys(folded)).toEqual({ ranked: ['repo_a', 'repo_y', 'repo_c'], unranked: ['repo_b', 'repo_x'] });
  });

  it("ignores another project's pending writes", () => {
    const folded = foldLandingPositions(SECTIONS, [{ projectId: 'proj_o', body: { repository: 'repo_c', after_repository: null } }], 'proj_b');

    expect(folded).toBe(SECTIONS);
  });
});
