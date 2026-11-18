import { projectRefusal, toProjectCreate, toProjectPatch } from './project.translate';

describe('project translation', () => {
  it('trims creation and defaults a blank name', () => {
    expect(toProjectCreate({ slug: ' alpha ', name: ' ', description: '  ' })).toEqual({ slug: 'alpha', name: 'alpha', description: '' });
  });
  it('sends only changed fields and clears description with empty text', () => {
    const before = { slug: 'alpha', name: 'Alpha', description: 'text' };
    expect(toProjectPatch(before, { ...before, description: ' ' })).toEqual({ description: '' });
    expect(toProjectPatch(before, { ...before, name: ' Alpha ' })).toEqual({});
  });
  it('maps field paths and slug conflicts but not revision conflicts', () => {
    expect(projectRefusal({ detail: [{ loc: ['body', 'name'], msg: 'bad' }] })).toEqual({ ok: false, fields: { name: 'bad' }, form: null });
    expect(projectRefusal({ type: 'urn:blizzard:problem:project-slug-taken', detail: 'Taken' })).toEqual({ ok: false, fields: { slug: 'Taken' }, form: null });
    expect(projectRefusal({ type: 'urn:blizzard:problem:revision-conflict', detail: 'Stale' })).toEqual({ ok: false, fields: {}, form: 'Stale' });
  });
});
