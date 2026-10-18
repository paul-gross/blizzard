import type { ProjectView } from 'fleet/shell';

import { lensChoices } from './project-lens.model';

const project = (slug: string, name: string): ProjectView => ({
  project_id: `proj_${slug}`,
  slug,
  name,
  description: '',
  revision: 1,
  created_at: '2026-10-01T00:00:00Z',
});

describe('lensChoices', () => {
  it('offers All, then every project by name', () => {
    expect(
      lensChoices([project('zeta', 'Zeta'), project('blizzard', 'Blizzard')]),
    ).toEqual([
      { slug: null, label: 'All' },
      { slug: 'blizzard', label: 'Blizzard' },
      { slug: 'zeta', label: 'Zeta' },
    ]);
  });

  it('offers only All for a tenant with no project', () => {
    expect(lensChoices([])).toEqual([{ slug: null, label: 'All' }]);
  });
});
