import { describe, expect, it } from 'vitest';
import { repositorySummary } from 'fleet/testing';
import { toProjectRepository } from './repositories.translate';

describe('project repository projection', () => {
  it('preserves identity, branch and retirement', () => {
    const repository = { repository_id: 'r', slug: 'repo', name: 'Repo', owner: 'o', repo: 'r', base_branch: 'main', retired: true };
    expect(toProjectRepository(repositorySummary(repository))).toEqual(repository);
  });
});
