import { gardenRoutineView } from 'fleet/testing';

import { fromRoutineView, toEditBaseline, toUrgencyRef } from './routine.translate';

describe('fromRoutineView', () => {
  it('reads every field of a served routine', () => {
    const routine = fromRoutineView(
      gardenRoutineView({
        routine_id: 'rtn_1',
        name: 'Nightly',
        slug: 'nightly',
        graph_name: 'sweep',
        default_scope_slug: 'core',
        default_urgency: 'urg_1',
        default_model: ['opus'],
        default_effort: 'high',
        revision: 4,
        retired: true,
        project_id: 'prj_1',
        project_name: 'Blizzard',
        project_slug: 'blizzard',
      }),
    );
    expect(routine).toMatchObject({
      id: 'rtn_1',
      revision: 4,
      slug: 'nightly',
      name: 'Nightly',
      graph: 'sweep',
      defaultScope: 'core',
      defaultUrgency: 'urg_1',
      defaultModel: ['opus'],
      defaultEffort: 'high',
      retired: true,
      projectId: 'prj_1',
      projectName: 'Blizzard',
      projectSlug: 'blizzard',
    });
  });

  it('reads an absent effective urgency as null and a present one as id and slug', () => {
    expect(fromRoutineView(gardenRoutineView({ routine_id: 'r', name: 'a' })).effectiveUrgency).toBeNull();
    expect(
      fromRoutineView(
        gardenRoutineView({ routine_id: 'r', name: 'a', effective_urgency: { urgency_id: 'urg_2', slug: 'high' } }),
      ).effectiveUrgency,
    ).toEqual({ id: 'urg_2', slug: 'high' });
  });

  it('reads the kept-by entries', () => {
    const routine = fromRoutineView(
      gardenRoutineView({
        routine_id: 'r',
        name: 'a',
        kept_by: [{ cadence_name: 'Evening', cadence_slug: 'evening', chore_key: 'nightly', next_moment: '2026-01-01T00:00:00Z' }],
      }),
    );
    expect(routine.keptBy).toEqual([
      { cadenceSlug: 'evening', cadenceName: 'Evening', choreKey: 'nightly', nextMoment: '2026-01-01T00:00:00Z' },
    ]);
  });

  it('keeps a null revision', () => {
    expect(fromRoutineView(gardenRoutineView({ routine_id: 'r', name: 'a', revision: null })).revision).toBeNull();
  });
});

describe('toEditBaseline', () => {
  it('captures the id, revision and draft from one read', () => {
    const routine = fromRoutineView(
      gardenRoutineView({ routine_id: 'rtn_1', name: 'a', revision: 3, graph_name: 'sweep', default_urgency: 'urg_1' }),
    );
    expect(toEditBaseline(routine)).toEqual({ id: 'rtn_1', revision: 3, draft: { graph: 'sweep', urgency: 'urg_1' } });
  });

  it('reads the tenant default as a blank urgency', () => {
    const routine = fromRoutineView(gardenRoutineView({ routine_id: 'r', name: 'a' }));
    expect(toEditBaseline(routine)?.draft.urgency).toBe('');
  });

  it('answers null for a routine without a revision', () => {
    expect(toEditBaseline(fromRoutineView(gardenRoutineView({ routine_id: 'r', name: 'a', revision: null })))).toBeNull();
  });
});

describe('toUrgencyRef', () => {
  it('maps an urgency to its wire reference and null to null', () => {
    expect(toUrgencyRef({ id: 'urg_1', slug: 'high' })).toEqual({ urgency_id: 'urg_1', slug: 'high' });
    expect(toUrgencyRef(null)).toBeNull();
  });
});
