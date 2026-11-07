import type { TemplateRef } from '@angular/core';
import type { ChunkCountsView, ProjectView } from 'fleet';

import { projectFacts } from './project-facts';

const BLIZZARD: ProjectView = {
  project_id: 'proj_b',
  slug: 'blizzard',
  name: 'Blizzard',
  description: '',
  color: 'teal',
  revision: 1,
  created_at: '2026-10-01T00:00:00Z',
};

function counts(ready: number, running: number, done: number): ChunkCountsView {
  return {
    ready,
    running,
    done,
    delivering: 0,
    needs_human: 0,
    not_ready: 0,
    paused: 0,
    stopped: 0,
    terminal: done,
    total: ready + running + done,
  } as ChunkCountsView;
}

describe('projectFacts', () => {
  it("states the overview's facts, leaving out the ones not yet read", () => {
    const labels = (facts: readonly { label: string }[]) => facts.map((fact) => fact.label);
    expect(labels(projectFacts(BLIZZARD, { former: [], counts: undefined, color: undefined, created: undefined, revision: undefined }))).toEqual([
      'Slug',
      'Id',
    ]);
    const full = projectFacts(BLIZZARD, {
      former: ['default'],
      counts: counts(1, 1, 3),
      color: undefined,
      created: undefined,
      revision: undefined,
    });
    expect(labels(full)).toEqual(['Slug', 'Former slugs', 'Queue', 'Id']);
    expect(full.find((fact) => fact.label === 'Queue')?.value).toBe('1 ready · 1 running');
  });

  it('states the color fact once its template is handed in', () => {
    const color = {} as TemplateRef<unknown>;
    const facts = projectFacts(BLIZZARD, { former: [], counts: undefined, color, created: undefined, revision: undefined });
    expect(facts.map((fact) => fact.label)).toEqual(['Slug', 'Color', 'Id']);
    expect(facts.find((fact) => fact.label === 'Color')?.template).toBe(color);
  });

  it('states nothing before the project is read', () => {
    expect(projectFacts(null, { former: [], counts: undefined, color: undefined, created: undefined, revision: undefined })).toEqual([]);
  });
});
