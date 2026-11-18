import { projectDraftErrors } from './project.domain';

describe('project draft rules', () => {
  const valid = { slug: 'a-b', name: '', description: '' };
  it('allows blank names only on create', () => {
    expect(projectDraftErrors(valid, true)).toEqual({});
    expect(projectDraftErrors(valid, false)).toHaveProperty('name');
  });
  it.each(['', 'A', 'a--b', '-a', 'a-', 'a'.repeat(65)])('rejects slug %s', (slug) => {
    expect(projectDraftErrors({ ...valid, slug }, true)).toHaveProperty('slug');
  });
  it('rejects long names', () => {
    expect(projectDraftErrors({ ...valid, name: 'a'.repeat(129) }, true)).toHaveProperty('name');
  });
});
