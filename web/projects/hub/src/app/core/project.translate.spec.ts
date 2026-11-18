import { projectView } from 'fleet/testing';

import { fromProjectView } from './project.translate';

describe('fromProjectView', () => {
  it('carries the project fields and revision without changing unknown colors', () => {
    const wire = projectView({ color: 'future-color', revision: 42, created_at: '2026-10-01T00:00:00Z' });
    expect(fromProjectView(wire)).toEqual({
      project_id: wire.project_id,
      slug: wire.slug,
      name: wire.name,
      description: wire.description,
      color: 'future-color',
      revision: 42,
      created_at: '2026-10-01T00:00:00Z',
    });
  });
});
