import type { Project } from './project.domain';

export function project(overrides: Partial<Project> = {}): Project {
  return {
    project_id: 'prj_1',
    slug: 'winter',
    name: 'Winter',
    description: '',
    color: 'blue',
    revision: 1,
    created_at: '2026-07-21T00:00:00Z',
    ...overrides,
  };
}
