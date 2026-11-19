import { gardenRoutineView, gardenScopeView } from 'fleet/testing';

import { SCOPE_DESCRIPTION_REFUSALS, fromRoutineView, fromScopeView, toScopeDescriptionRequest } from './scope.translate';

describe('fromScopeView', () => {
  it('reads a scope, naming its project by name and falling back to its slug', () => {
    expect(fromScopeView(gardenScopeView({ slug: 'web', description: 'Web', retired: true, project_id: 'p_1', project_name: 'Blizzard' }))).toEqual({
      slug: 'web',
      description: 'Web',
      retired: true,
      projectId: 'p_1',
      projectName: 'Blizzard',
    });
    expect(fromScopeView(gardenScopeView({ slug: 'web', project_id: 'p_1', project_name: null, project_slug: 'blizzard' })).projectName).toBe('blizzard');
  });

  it('reads an unset retired flag and absent project as not retired and none', () => {
    const scope = fromScopeView({ ...gardenScopeView({ slug: 'web' }), retired: undefined, project_id: null, project_name: null, project_slug: null } as never);
    expect(scope).toMatchObject({ retired: false, projectId: null, projectName: null });
  });
});

describe('fromRoutineView', () => {
  it('reads the routine as the scope panel names it', () => {
    expect(fromRoutineView(gardenRoutineView({ routine_id: 'rtn_1', slug: 'nightly', name: 'Nightly', default_scope_slug: 'web' }))).toEqual({
      routineId: 'rtn_1',
      slug: 'nightly',
      name: 'Nightly',
      defaultScopeSlug: 'web',
    });
  });
});

describe('toScopeDescriptionRequest', () => {
  it('sends the trimmed description', () => {
    expect(toScopeDescriptionRequest({ description: '  new words  ' })).toEqual({ description: 'new words' });
  });
});

describe('SCOPE_DESCRIPTION_REFUSALS', () => {
  it('lands the description path on its field and any other on none', () => {
    expect(SCOPE_DESCRIPTION_REFUSALS.field('description')).toBe('description');
    expect(SCOPE_DESCRIPTION_REFUSALS.field('slug')).toBeNull();
  });
});
