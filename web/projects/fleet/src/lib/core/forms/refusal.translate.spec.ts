import type { RefusalMap } from './outcome';
import { readRefusal } from './refusal.translate';

interface Draft {
  slug: string;
  cron: string;
  chore: string;
  name: string;
}

const MAP: RefusalMap<Draft> = {
  field: (path) =>
    (({ slug: 'slug', 'schedule.cron': 'cron', 'chores[0].key': 'chore', name: 'name' }) as const)[path as 'slug'] ?? null,
  kinds: { 'urn:blizzard:problem:slug-taken': 'slug' },
};

describe('readRefusal', () => {
  it('maps nested and dotted paths to a field', () => {
    for (const loc of [['body', 'schedule', 'cron'], ['body', 'schedule.cron']]) {
      expect(readRefusal({ detail: [{ loc, msg: 'bad cron' }] }, MAP)).toEqual({
        ok: false,
        fields: { cron: 'bad cron' },
        form: null,
      });
    }
  });
  it('renders a number segment as an index', () => {
    expect(readRefusal({ detail: [{ loc: ['body', 'chores', 0, 'key'], msg: 'dup' }] }, MAP)).toEqual({
      ok: false,
      fields: { chore: 'dup' },
      form: null,
    });
  });
  it.each(['body', 'query', 'path'])('drops a leading %s segment', (location) => {
    const out = readRefusal({ detail: [{ loc: [location, 'name'], msg: 'required' }] }, MAP);
    expect(out).toEqual({ ok: false, fields: { name: 'required' }, form: null });
  });
  it('keeps a location word that is not leading', () => {
    const out = readRefusal({ detail: [{ loc: ['name', 'body'], msg: 'x' }] }, MAP);
    expect(out).toEqual({ ok: false, fields: {}, form: 'name.body: x' });
  });
  it('sends an unmapped entry to the form line, named by its path', () => {
    const out = readRefusal({ detail: [{ loc: ['body', 'other'], msg: 'nope' }, { loc: ['body', 'name'], msg: 'req' }] }, MAP);
    expect(out).toEqual({ ok: false, fields: { name: 'req' }, form: 'other: nope' });
  });
  it('joins several messages on one field', () => {
    const out = readRefusal({ detail: [{ loc: ['body', 'name'], msg: 'a' }, { loc: ['body', 'name'], msg: 'b' }] }, MAP);
    expect(out).toEqual({ ok: false, fields: { name: 'a; b' }, form: null });
  });
  it('sends a pathless entry to the form line', () => {
    expect(readRefusal({ detail: [{ loc: ['body'], msg: 'whole body bad' }] }, MAP)).toEqual({
      ok: false,
      fields: {},
      form: 'whole body bad',
    });
  });
  it('sends a malformed list to the form line, worded by errorMessage', () => {
    const out = readRefusal({ title: 'Unprocessable', detail: [{ loc: ['body', 'name'] }] }, MAP);
    expect(out).toEqual({ ok: false, fields: {}, form: 'Unprocessable' });
  });
  it('maps a kind-only refusal with a string detail to its field', () => {
    const out = readRefusal({ type: 'urn:blizzard:problem:slug-taken', detail: 'Slug is taken.' }, MAP);
    expect(out).toEqual({ ok: false, fields: { slug: 'Slug is taken.' }, form: null });
  });
  it('maps a kind-only refusal with a coded-object detail to its field', () => {
    const out = readRefusal({ type: 'urn:blizzard:problem:slug-taken', detail: { message: 'Taken.' } }, MAP);
    expect(out).toEqual({ ok: false, fields: { slug: 'Taken.' }, form: null });
  });
  it('does not read an inherited key as a kind', () => {
    const out = readRefusal({ type: 'constructor', detail: 'boom' }, MAP);
    expect(out).toEqual({ ok: false, fields: {}, form: 'boom' });
  });
  it('words an unknown 4xx or 5xx on the form line', () => {
    expect(readRefusal({ type: 'urn:other', detail: 'Conflict.' }, MAP)).toEqual({ ok: false, fields: {}, form: 'Conflict.' });
    expect(readRefusal({ status: 500 }, MAP)).toEqual({ ok: false, fields: {}, form: 'Save failed.' });
  });
  it('words a network error with the default or a passed fallback', () => {
    const error = new TypeError('Failed to fetch');
    expect(readRefusal(error, MAP)).toEqual({ ok: false, fields: {}, form: 'Save failed.' });
    expect(readRefusal(error, MAP, 'Create failed.')).toEqual({ ok: false, fields: {}, form: 'Create failed.' });
    expect(readRefusal(undefined, MAP, 'Create failed.')).toEqual({ ok: false, fields: {}, form: 'Create failed.' });
    expect(readRefusal('boom', MAP)).toEqual({ ok: false, fields: {}, form: 'Save failed.' });
  });
});
