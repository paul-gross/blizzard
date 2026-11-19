import { gardenRoutineView } from 'fleet/testing';

import { fromRoutineView, ROUTINE_CREATE_REFUSALS, routineEditRefusals, toCreateRequest, toEditBaseline, toEditRequest, toUrgencyRef } from './routine.translate';

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

describe('toCreateRequest', () => {
  it('trims the definition and states null for a blank name and the tenant default', () => {
    const draft = { slug: ' nightly ', name: ' ', graph: ' garden ', scope: ' web ', urgency: '' };
    expect(toCreateRequest(draft)).toEqual({
      slug: 'nightly',
      name: null,
      graph_name: 'garden',
      default_scope_slug: 'web',
      default_urgency: null,
    });
  });

  it('states a typed name and a chosen urgency', () => {
    const request = toCreateRequest({ slug: 'a', name: ' Nightly ', graph: 'g', scope: 's', urgency: 'urgent' });
    expect(request.name).toBe('Nightly');
    expect(request.default_urgency).toBe('urgent');
  });
});

describe('ROUTINE_CREATE_REFUSALS', () => {
  it('maps each hub field path to its draft field', () => {
    const paths = ['slug', 'name', 'graph_name', 'default_scope_slug', 'default_urgency'];
    expect(paths.map((p) => ROUTINE_CREATE_REFUSALS.field(p))).toEqual(['slug', 'name', 'graph', 'scope', 'urgency']);
    expect(ROUTINE_CREATE_REFUSALS.field('default_model')).toBeNull();
  });

  it('sends a malformed scope slug to the scope', () => {
    expect(ROUTINE_CREATE_REFUSALS.kinds['urn:blizzard:problem:scope-slug-invalid']).toBe('scope');
  });
});

describe('toEditRequest', () => {
  const before = { graph: 'garden', urgency: '' };

  it('sends a changed graph trimmed, and nothing else', () => {
    expect(toEditRequest(before, { ...before, graph: ' garden-delta ' })).toEqual({ graph_name: 'garden-delta' });
  });

  it('sends a changed urgency, the tenant default as null', () => {
    expect(toEditRequest(before, { ...before, urgency: 'urgent' })).toEqual({ default_urgency: 'urgent' });
    expect(toEditRequest({ ...before, urgency: 'urgent' }, before)).toEqual({ default_urgency: null });
  });

  it('omits every unchanged field', () => {
    expect(toEditRequest(before, { graph: ' garden ', urgency: '' })).toEqual({});
  });
});

describe('routineEditRefusals', () => {
  const before = { graph: 'garden', urgency: '' };

  it('routes each validation path to its own field', () => {
    const map = routineEditRefusals(before, before);
    expect(map.field('graph_name')).toBe('graph');
    expect(map.field('default_urgency')).toBe('urgency');
    expect(map.field('slug')).toBeNull();
  });

  it('lands a definition refusal, which names no field, on the one field the edit changed', () => {
    const kind = 'urn:blizzard:problem:routine-definition-invalid';
    expect(routineEditRefusals(before, { ...before, graph: 'x' }).kinds[kind]).toBe('graph');
    expect(routineEditRefusals(before, { ...before, urgency: 'urgent' }).kinds[kind]).toBe('urgency');
    expect(routineEditRefusals(before, before).kinds[kind]).toBeUndefined();
  });
});
